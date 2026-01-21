#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml

from schema_validation import validate_all_schemas

ROOT = Path(__file__).resolve().parents[1]

# 当前 MVP 仅支持单一应用 dce_md_publisher
APP_NAME = "dce_md_publisher"

# 记录同一 host 上 busy-spin（main_loop_cpu）在 isolated_cpus 中的使用情况，用于跨应用去重校验
HOST_BUSY_ISOLATED_USAGE: Dict[Tuple[str, str], Dict[int, str]] = {}

# 全局应用索引：强制 app_name 在所有 dc/host 之间唯一，用于跨实例引用
# 结构：APP_GLOBAL_INDEX[app_name] = (dc_id, host_name)
APP_GLOBAL_INDEX: Dict[str, Tuple[str, str]] = {}


def parse_cpu_set(expr: str) -> Set[int]:
    expr = expr.strip()
    if not expr:
        return set()
    result: Set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                raise ValueError(f"invalid cpu range: {part}")
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    return result


def load_datacenter(dc_id: str, host_name: str) -> Dict:
    """加载某个 DC 下指定主机的拓扑信息。

    现在的拓扑文件放在 deployments/<dc>/hosts.yaml，结构类似：

    datacenters:
      - id: idc_shanghai
        hosts:
          - name: host01
            cpus: 16
            numa_nodes:
              - id: 0
                cpus: 0-7
              - id: 1
                cpus: 8-15
            ...
    """

    dc_file = ROOT / "deployments" / dc_id / "hosts.yaml"
    if not dc_file.exists():
        raise SystemExit(f"hosts topology file not found for datacenter '{dc_id}': {dc_file}")

    with dc_file.open() as f:
        data = yaml.safe_load(f) or {}

    # 兼容旧写法：带 datacenters 列表
    dcs: List[Dict] = data.get("datacenters") or []
    if dcs:
        dc = next((d for d in dcs if d.get("id") == dc_id), None)
        if not dc:
            raise SystemExit(f"datacenter '{dc_id}' not found in {dc_file}")

        hosts: List[Dict] = dc.get("hosts") or []
        host = next((h for h in hosts if h.get("name") == host_name), None)
        if not host:
            raise SystemExit(f"host '{host_name}' not found in datacenter '{dc_id}'")

        return host

    # 新写法：顶层就是 host 映射
    hosts_map: Dict = data or {}
    host = hosts_map.get(host_name)
    if not isinstance(host, dict):
        raise SystemExit(f"host '{host_name}' not found in {dc_file}")

    return host


def build_cpu_numa_map_from_host(host: Dict) -> Dict[int, int]:
    """根据 host.numa_nodes 字段构建 cpu -> numa_node 的映射。

    支持两种写法：
    - 显式 numa_nodes 列表（推荐，目前 hosts.yaml 的写法）
    - 旧写法：numa_nodes 是整数，均匀切分 cpu
    """

    mapping: Dict[int, int] = {}

    nodes = host.get("numa_nodes")
    # 新写法：列表，每个元素包含 id 和 cpus 范围
    if isinstance(nodes, list):
        for node in nodes:
            node_id = int(node.get("id", 0))
            cpus_expr = str(node.get("cpus", ""))
            for cpu in parse_cpu_set(cpus_expr):
                mapping[cpu] = node_id
        return mapping

    # 兼容旧写法：按整数 numa_nodes 均匀切分
    total_cpus = int(host.get("cpus", 0))
    numa_nodes = int(nodes or 1)
    if total_cpus <= 0 or numa_nodes <= 0:
        raise ValueError("cpus and numa_nodes must be positive")

    base = total_cpus // numa_nodes
    rem = total_cpus % numa_nodes

    cpu = 0
    for node_id in range(numa_nodes):
        count = base + (1 if node_id < rem else 0)
        for _ in range(count):
            mapping[cpu] = node_id
            cpu += 1

    return mapping


def load_deployment(dc_id: str, host_name: str, app_name: str) -> Dict:
    """加载指定机房/host 下某个应用的部署定义。

    期望结构：

    deployments:
      host01:
        shared_cpus: 0, 1
        dce_md_publisher:
          isolated_cpus: 2
          cfg_envs: [...]
    """

    dep_file = ROOT / "deployments" / dc_id / "deployments.yaml"
    with dep_file.open() as f:
        data = yaml.safe_load(f) or {}

    # 旧写法：deployments[host][app]
    if "deployments" in data:
        deployments = data.get("deployments") or {}
        host_cfg: Dict = deployments.get(host_name) or {}
        if not host_cfg:
            raise SystemExit(
                f"no deployments defined for host '{host_name}' in {dep_file}"
            )

        app_def: Dict = host_cfg.get(app_name) or {}
        if not app_def:
            raise SystemExit(
                f"no app '{app_name}' defined under host '{host_name}' in {dep_file}"
            )

        return {
            "shared_cpus": host_cfg.get("shared_cpus", ""),
            "log_dir": host_cfg.get("log_dir"),
            "app": app_def,
        }

    # 新写法：顶层就是 host -> apps 映射
    hosts_map: Dict = data or {}
    host_cfg: Dict = hosts_map.get(host_name) or {}
    if not host_cfg:
        raise SystemExit(f"no deployments defined for host '{host_name}' in {dep_file}")

    app_def: Dict = host_cfg.get(app_name) or {}
    if not app_def:
        raise SystemExit(
            f"no app '{app_name}' defined under host '{host_name}' in {dep_file}"
        )

    return {
        "shared_cpus": "",  # shared_cpus 由 hosts.yaml 提供
        "log_dir": host_cfg.get("log_dir"),
        "app": app_def,
    }


def load_app_config(app_name: str) -> Dict:
    """加载 apps/ 下的应用定义（旧写法使用，新写法使用 deployments.yaml 中的 binary/tag/templates）。"""

    app_file = ROOT / "apps" / f"{app_name}.yaml"
    if not app_file.exists():
        raise SystemExit(f"app config file not found: {app_file}")

    with app_file.open() as f:
        data = yaml.safe_load(f) or {}

    app_cfg: Dict = data.get(app_name) or {}
    if not app_cfg:
        raise SystemExit(
            f"app '{app_name}' definition not found as top-level key in {app_file}"
        )

    return app_cfg


def load_binary_requirements() -> Dict[str, Dict]:
    """加载 deployments/required_binaries.yaml 中的全部 binary 定义。"""

    req_file = ROOT / "deployments" / "required_binaries.yaml"
    if not req_file.exists():
        raise SystemExit(f"binary requirements file not found: {req_file}")

    with req_file.open() as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise SystemExit(f"invalid format in {req_file}, expected mapping of binary -> config")

    return data


def load_binary_target(binary_name: str, tag_or_version: str) -> Path:
    """根据 deployments/required_binaries.yaml 解析出具体版本的本地路径，并返回目标二进制路径。

    约定目录结构：

    deployments/
      required_binaries.yaml

    install/
      binaries/
        md_server/
          v1.2.3/
            md_server          # 实际二进制文件（本 MVP 中可为 mock）
    """

    req_file = ROOT / "deployments" / "required_binaries.yaml"
    data = load_binary_requirements()

    binary_cfg: Dict = data.get(binary_name) or {}
    if not binary_cfg:
        raise SystemExit(f"binary '{binary_name}' not defined in {req_file}")

    tags: Dict = binary_cfg.get("tags") or {}
    required_versions_raw = binary_cfg.get("required_versions") or []
    required_versions = {str(v) for v in required_versions_raw}

    # 先将 tag_or_version 解析为具体 version
    if tag_or_version in tags:
        version = str(tags[tag_or_version])
    else:
        version = str(tag_or_version)

    if required_versions and version not in required_versions:
        raise SystemExit(
            f"version '{version}' (from tag '{tag_or_version}') not in required_versions "
            f"for binary '{binary_name}' in {req_file}"
        )

    bin_dir = ROOT / "install" / "binaries" / binary_name / version
    bin_dir.mkdir(parents=True, exist_ok=True)

    bin_path = bin_dir / binary_name

    # 如果二进制不存在，创建一个简单的 mock 可执行文件
    if not bin_path.exists():
        bin_path.write_text(
            "#!/usr/bin/env bash\n" f"echo 'mock {binary_name} {version}' \"$@\"\n",
            encoding="utf-8",
        )
        # 尝试设置可执行权限（在非类 Unix 系统上失败也无妨）
        try:
            bin_path.chmod(0o755)
        except PermissionError:
            pass

    return bin_path


def validate_and_render(
    dc_id: str = "idc_shanghai", host_name: str = "host01", app_name: str = APP_NAME
) -> Path:
    host = load_datacenter(dc_id, host_name)
    total_cpus = int(host.get("cpus", 0))
    isolated_cpus = parse_cpu_set(str(host.get("isolated_cpus", "")))
    host_shared_cpus = parse_cpu_set(str(host.get("shared_cpus", "")))

    for cpu in sorted(isolated_cpus | host_shared_cpus):
        if cpu < 0 or cpu >= total_cpus:
            raise SystemExit(
                f"cpu id {cpu} out of range [0, {total_cpus}) in hosts.yaml for host {host_name}"
            )
    overlap = isolated_cpus & host_shared_cpus
    if overlap:
        raise SystemExit(
            f"isolated_cpus and shared_cpus overlap for host {host_name}: {sorted(overlap)}"
        )

    cpu_numa = build_cpu_numa_map_from_host(host)

    dep_info = load_deployment(dc_id, host_name, app_name)
    dep_shared_cpus = parse_cpu_set(str(dep_info.get("shared_cpus", "")))
    # 优先使用 hosts.yaml 中的 shared_cpus，兼容旧 schema 时退回 deployments 里的 shared_cpus
    shared_cpus = host_shared_cpus or dep_shared_cpus

    host_log_dir = dep_info.get("log_dir")

    app_def: Dict = dep_info["app"]

    # 构建应用目录结构：install/<dc>/<host>/<app>/
    apps_root = ROOT / "install" / dc_id / host_name / app_name
    apps_root.mkdir(parents=True, exist_ok=True)

    # 新写法：deployments.yaml 中为每个 app 定义 binary/tag 和 templates 列表
    templates = app_def.get("templates")
    if templates:
        binary_name = app_def.get("binary")
        if not binary_name:
            raise SystemExit(
                f"binary not defined for app '{app_name}' in deployments.yaml (dc={dc_id}, host={host_name})"
            )

        tag_or_version = str(app_def.get("tag") or app_def.get("version") or "prod")

        last_cfg_path: Path | None = None

        for tmpl in templates:
            if not isinstance(tmpl, dict):
                continue

            template_name = tmpl.get("name")
            if not template_name:
                raise SystemExit(
                    f"template 'name' is required for app '{app_name}' in deployments.yaml (dc={dc_id}, host={host_name})"
                )

            cfg_envs_obj = tmpl.get("cfg_envs")
            if isinstance(cfg_envs_obj, list):
                cfg_envs: List[Dict] = cfg_envs_obj
                if not cfg_envs:
                    raise SystemExit("cfg_envs is empty in deployments definition")
                env = cfg_envs[0]
            elif isinstance(cfg_envs_obj, dict):
                env = cfg_envs_obj
            else:
                raise SystemExit(
                    "cfg_envs must be a mapping or a list of mappings in deployments definition"
                )

            try:
                log_cpu = int(env["log_cpu"])
                main_loop_cpu = int(env["main_loop_cpu"])
                admin_loop_cpu = int(env["admin_loop_cpu"])
            except KeyError as e:
                raise SystemExit(f"missing cpu field in cfg_envs: {e}")

            used_cpus = {log_cpu, main_loop_cpu, admin_loop_cpu}

            # 1) busy spin 的 cpu_id（main_loop_cpu）必须在 isolated_cpus 范围内
            if main_loop_cpu not in isolated_cpus:
                raise SystemExit(
                    f"main_loop_cpu {main_loop_cpu} not in isolated_cpus {sorted(isolated_cpus)}"
                )

            # 1.1) 同一 host 上所有 app 的 busy-spin 核在 isolated_cpus 上不允许重复使用
            host_key = (dc_id, host_name)
            busy_usage = HOST_BUSY_ISOLATED_USAGE.setdefault(host_key, {})
            if main_loop_cpu in busy_usage and busy_usage[main_loop_cpu] != app_name:
                raise SystemExit(
                    "main_loop_cpu {cpu} already used by app '{other}' on host '{host}' "
                    "(isolated_cpus must not be shared by busy-spin loops)".format(
                        cpu=main_loop_cpu,
                        other=busy_usage[main_loop_cpu],
                        host=host_name,
                    )
                )
            busy_usage[main_loop_cpu] = app_name

            if log_cpu not in shared_cpus:
                raise SystemExit(
                    f"log_cpu {log_cpu} not in shared_cpus {sorted(shared_cpus)}"
                )

            if admin_loop_cpu not in shared_cpus:
                raise SystemExit(
                    f"admin_loop_cpu {admin_loop_cpu} not in shared_cpus {sorted(shared_cpus)}"
                )

            # 3) 所有 cpu id 必须在合法范围 [0, total_cpus)
            for cpu in used_cpus:
                if cpu < 0 or cpu >= total_cpus:
                    raise SystemExit(
                        f"cpu id {cpu} out of range [0, {total_cpus}) for host {host_name}"
                    )

            # 4) 配置文件中的 cpu_id 不允许重复
            if len(used_cpus) != 3:
                raise SystemExit(
                    "duplicated cpu ids detected among log_cpu/main_loop_cpu/admin_loop_cpu: "
                    f"{sorted(used_cpus)}"
                )

            # 生成 NUMA comments（打印到 stdout，方便审阅）
            print(f"CPU NUMA mapping for used CPUs (template {template_name}):")
            for cpu in sorted(used_cpus):
                node = cpu_numa.get(cpu, -1)
                print(f"  cpu {cpu}: numa_node {node}")

            template_path = ROOT / "deployments" / dc_id / "templates" / template_name
            if not template_path.exists():
                raise SystemExit(f"template file not found: {template_path}")

            template_text = template_path.read_text()

            # 将 listen_nic (网卡名) 替换为对应 IP；仅当模板中实际使用了 {{listen_nic}} 时才强制要求
            needs_listen_nic = "{{listen_nic}}" in template_text
            nic_ip = None
            nic_name = env.get("listen_nic")
            if needs_listen_nic:
                if not nic_name:
                    raise SystemExit("listen_nic is not specified in cfg_envs")

                for nic in host.get("nics", []):
                    if nic.get("name") == nic_name:
                        nic_ip = nic.get("ip")
                        break
                if not nic_ip:
                    raise SystemExit(
                        f"ip for nic '{nic_name}' not found in hosts.yaml for host '{host_name}'"
                    )

            replacements = dict(env)
            replacements.update(
                {
                    "listen_nic": nic_ip if nic_ip is not None else nic_name,
                    "listen_port": env.get("listen_port"),
                    "log_cpu": log_cpu,
                    "main_loop_cpu": main_loop_cpu,
                    "admin_loop_cpu": admin_loop_cpu,
                }
            )

            # 通用跨实例引用：支持 {{AppName.key}}，可以跨 host / 跨 dc。
            cross_refs: Dict[str, str] = {}

            # 解析模板中出现的 AppName.key 形式的占位符
            placeholder_pattern = re.compile(r"{{\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*}}")
            ref_pairs = set(placeholder_pattern.findall(template_text))

            for ref_app, ref_key in ref_pairs:
                # 通过全局索引找到被引用应用所在的 dc / host
                mapping = APP_GLOBAL_INDEX.get(ref_app)
                if mapping is None:
                    raise SystemExit(
                        f"referenced application '{ref_app}' not found in any deployments.yaml "
                        f"(used in template {template_name} for app '{app_name}')"
                    )

                ref_dc, ref_host = mapping

                if "shm" in ref_key.lower() and (dc_id != ref_dc or host_name != ref_host):
                    raise SystemExit(
                        "shared-memory key '{app}.{key}' must be used on the same host "
                        "(referencer dc={dc_ref}, host={h_ref}, owner dc={dc_owner}, host={h_owner})".format(
                            app=ref_app,
                            key=ref_key,
                            dc_ref=dc_id,
                            h_ref=host_name,
                            dc_owner=ref_dc,
                            h_owner=ref_host,
                        )
                    )

                # 加载被引用 app 的部署定义
                try:
                    ref_dep = load_deployment(ref_dc, ref_host, ref_app)
                except SystemExit as e:
                    raise SystemExit(
                        f"failed to load referenced app '{ref_app}' (dc={ref_dc}, host={ref_host}): {e}"
                    )

                ref_app_def: Dict = ref_dep.get("app") or {}
                ref_templates = ref_app_def.get("templates")
                if isinstance(ref_templates, list) and ref_templates:
                    ref_tmpl0 = ref_templates[0]
                    ref_cfg_envs = ref_tmpl0.get("cfg_envs") or {}
                else:
                    ref_cfg_envs = ref_app_def.get("cfg_envs") or {}

                if not isinstance(ref_cfg_envs, dict):
                    raise SystemExit(
                        f"cfg_envs for referenced app '{ref_app}' is not a mapping "
                        f"(dc={ref_dc}, host={ref_host})"
                    )

                if ref_key not in ref_cfg_envs:
                    raise SystemExit(
                        f"key '{ref_key}' not found in cfg_envs of referenced app '{ref_app}' "
                        f"(dc={ref_dc}, host={ref_host})"
                    )

                raw_val = ref_cfg_envs[ref_key]

                # listen_nic 需要根据对应 host 的 hosts.yaml 解析为 IP。
                if ref_key == "listen_nic" and raw_val is not None:
                    ref_host_topo = load_datacenter(ref_dc, ref_host)
                    ref_nics = ref_host_topo.get("nics", [])
                    ref_ip = None
                    for nic in ref_nics:
                        if nic.get("name") == str(raw_val):
                            ref_ip = nic.get("ip")
                            break
                    if ref_ip is None:
                        raise SystemExit(
                            f"ip for nic '{raw_val}' not found in hosts.yaml for referenced app "
                            f"'{ref_app}' (dc={ref_dc}, host={ref_host})"
                        )
                    cross_refs[f"{ref_app}.{ref_key}"] = str(ref_ip)
                else:
                    cross_refs[f"{ref_app}.{ref_key}"] = str(raw_val)

            replacements.update(cross_refs)

            rendered = template_text
            for key, value in replacements.items():
                rendered = re.sub(
                    r"{{\s*" + re.escape(str(key)) + r"\s*}}", str(value), rendered
                )

            leftover = re.findall(r"{{\s*[^}]+\s*}}", rendered)
            if leftover:
                raise SystemExit(
                    f"unresolved template variables in rendered config for app '{app_name}' "
                    f"(template {template_name}): {sorted(set(leftover))}"
                )

            try:
                rendered_obj = json.loads(rendered)
            except json.JSONDecodeError as e:
                print("Rendered JSON is invalid:")
                print(rendered)
                raise SystemExit(f"failed to parse rendered JSON: {e}")

            if not isinstance(rendered_obj, dict):
                raise SystemExit(
                    f"rendered JSON must be an object for app '{app_name}' (template {template_name})"
                )

            if host_log_dir is not None:
                logging_obj = rendered_obj.get("logging")
                if not isinstance(logging_obj, dict):
                    raise SystemExit(
                        f"host log_dir is set but 'logging' is missing or not an object "
                        f"in config for app '{app_name}' (template {template_name})"
                    )
                logging_obj["log_dir"] = str(Path(str(host_log_dir)) / app_name)

            loops_obj = rendered_obj.get("event_loops")
            if not isinstance(loops_obj, list):
                raise SystemExit(
                    f"'event_loops' is missing or not a list in config for app '{app_name}' (template {template_name})"
                )

            has_admin_loop = False
            for loop in loops_obj:
                if not isinstance(loop, dict):
                    continue

                loop_name = loop.get("name")
                busy_spin = loop.get("busy_spin")
                cpu_id_raw = loop.get("cpu_id")
                try:
                    cpu_id = int(cpu_id_raw)
                except (TypeError, ValueError):
                    raise SystemExit(
                        f"invalid cpu_id '{cpu_id_raw}' in event_loops for app '{app_name}' (template {template_name})"
                    )

                if cpu_id < 0 or cpu_id >= total_cpus:
                    raise SystemExit(
                        f"cpu id {cpu_id} out of range [0, {total_cpus}) in event_loops for app '{app_name}'"
                    )

                if loop_name == "admin_loop":
                    has_admin_loop = True
                    if busy_spin is not False:
                        raise SystemExit(
                            f"admin_loop must have busy_spin=false for app '{app_name}' (template {template_name})"
                        )
                    if cpu_id != admin_loop_cpu:
                        raise SystemExit(
                            f"admin_loop cpu_id {cpu_id} does not match cfg_envs.admin_loop_cpu {admin_loop_cpu} "
                            f"for app '{app_name}' (template {template_name})"
                        )

                if busy_spin is True:
                    if cpu_id not in isolated_cpus:
                        raise SystemExit(
                            f"busy_spin loop '{loop_name}' cpu_id {cpu_id} not in isolated_cpus {sorted(isolated_cpus)}"
                        )

                    host_key = (dc_id, host_name)
                    busy_usage = HOST_BUSY_ISOLATED_USAGE.setdefault(host_key, {})
                    if cpu_id in busy_usage and busy_usage[cpu_id] != app_name:
                        raise SystemExit(
                            "busy_spin cpu {cpu} already used by app '{other}' on host '{host}' ".format(
                                cpu=cpu_id,
                                other=busy_usage[cpu_id],
                                host=host_name,
                            )
                        )
                    busy_usage[cpu_id] = app_name

            if not has_admin_loop:
                raise SystemExit(
                    f"admin_loop not found in event_loops for app '{app_name}' (template {template_name})"
                )

            rendered = json.dumps(rendered_obj, indent=4) + "\n"

            # 写入配置文件：使用模板名作为文件名
            cfg_path = apps_root / template_name
            cfg_path.write_text(rendered)
            last_cfg_path = cfg_path

        # 解析 binary + tag/version，创建或指向具体版本的二进制
        bin_target = load_binary_target(binary_name, tag_or_version)

        # 在应用目录下创建可执行文件 symlink：<app_name> -> binaries/.../<binary_name>
        exec_path = apps_root / app_name
        if exec_path.is_symlink() or exec_path.exists():
            exec_path.unlink()

        # 使用相对路径创建 symlink，保证仓库可移动
        rel_target = os.path.relpath(bin_target, start=apps_root)
        os.symlink(rel_target, exec_path)

        print(f"generated app directory: {apps_root}")
        if last_cfg_path is not None:
            print(f"  last config: {last_cfg_path}")
        print(f"  binary symlink: {exec_path} -> {rel_target}")
        # 返回最后一个模板生成的配置路径
        return last_cfg_path if last_cfg_path is not None else exec_path

    # 旧写法：app_def 直接包含 cfg_envs，binary/tag 由 apps/<app>.yaml 提供
    cfg_envs_obj = app_def.get("cfg_envs")
    if isinstance(cfg_envs_obj, list):
        cfg_envs: List[Dict] = cfg_envs_obj
        if not cfg_envs:
            raise SystemExit("cfg_envs is empty in deployments definition")
        env = cfg_envs[0]
    elif isinstance(cfg_envs_obj, dict):
        env = cfg_envs_obj
    else:
        raise SystemExit("cfg_envs must be a mapping or a list of mappings in deployments definition")

    try:
        log_cpu = int(env["log_cpu"])
        main_loop_cpu = int(env["main_loop_cpu"])
        admin_loop_cpu = int(env["admin_loop_cpu"])
    except KeyError as e:
        raise SystemExit(f"missing cpu field in cfg_envs: {e}")

    used_cpus = {log_cpu, main_loop_cpu, admin_loop_cpu}

    # 1) busy spin 的 cpu_id（main_loop_cpu）必须在 isolated_cpus 范围内
    if main_loop_cpu not in isolated_cpus:
        raise SystemExit(
            f"main_loop_cpu {main_loop_cpu} not in isolated_cpus {sorted(isolated_cpus)}"
        )

    # 1.1) 同一 host 上所有 app 的 busy-spin 核在 isolated_cpus 上不允许重复使用
    host_key = (dc_id, host_name)
    busy_usage = HOST_BUSY_ISOLATED_USAGE.setdefault(host_key, {})
    if main_loop_cpu in busy_usage and busy_usage[main_loop_cpu] != app_name:
        raise SystemExit(
            "main_loop_cpu {cpu} already used by app '{other}' on host '{host}' "
            "(isolated_cpus must not be shared by busy-spin loops)".format(
                cpu=main_loop_cpu,
                other=busy_usage[main_loop_cpu],
                host=host_name,
            )
        )
    busy_usage[main_loop_cpu] = app_name

    # 2) log_cpu 必须在 shared_cpus 范围内
    if log_cpu not in shared_cpus:
        raise SystemExit(
            f"log_cpu {log_cpu} not in shared_cpus {sorted(shared_cpus)}"
        )

    # 3) 所有 cpu id 必须在合法范围 [0, total_cpus)
    for cpu in used_cpus:
        if cpu < 0 or cpu >= total_cpus:
            raise SystemExit(
                f"cpu id {cpu} out of range [0, {total_cpus}) for host {host_name}"
            )

    # 4) 配置文件中的 cpu_id 不允许重复
    if len(used_cpus) != 3:
        raise SystemExit(
            f"duplicated cpu ids detected among log_cpu/main_loop_cpu/admin_loop_cpu: {sorted(used_cpus)}"
        )

    # 生成 NUMA comments（打印到 stdout，方便审阅）
    print("CPU NUMA mapping for used CPUs:")
    for cpu in sorted(used_cpus):
        node = cpu_numa.get(cpu, -1)
        print(f"  cpu {cpu}: numa_node {node}")

    # 渲染模板 & 准备二进制（旧写法从 apps/<app>.yaml 读取 template/binary/tag）
    app_cfg = load_app_config(app_name)
    template_name = app_cfg.get("config_template")
    if not template_name:
        raise SystemExit(
            f"config_template not defined for app '{app_name}' in apps/{app_name}.yaml"
        )

    template_path = ROOT / "deploy" / dc_id / "templates" / template_name
    template_text = template_path.read_text()

    # 将 listen_nic (网卡名) 替换为对应 IP
    nic_name = env.get("listen_nic")
    if not nic_name:
        raise SystemExit("listen_nic is not specified in cfg_envs")

    nic_ip = None
    for nic in host.get("nics", []):
        if nic.get("name") == nic_name:
            nic_ip = nic.get("ip")
            break
    if not nic_ip:
        raise SystemExit(
            f"ip for nic '{nic_name}' not found in hosts.yaml for host '{host_name}'"
        )

    replacements = {
        "listen_nic": nic_ip,
        "listen_port": env.get("listen_port"),
        "log_cpu": log_cpu,
        "main_loop_cpu": main_loop_cpu,
        "admin_loop_cpu": admin_loop_cpu,
    }

    rendered = template_text
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))

    # 简单校验生成的 JSON 是否合法
    try:
        json.loads(rendered)
    except json.JSONDecodeError as e:
        print("Rendered JSON is invalid:")
        print(rendered)
        raise SystemExit(f"failed to parse rendered JSON: {e}")

    # 写入配置文件：<app>.json
    cfg_path = apps_root / f"{app_name}.json"
    cfg_path.write_text(rendered + "\n")

    # 解析 binary + tag，创建或指向具体版本的二进制
    binary_name = app_cfg.get("binary")
    if not binary_name:
        raise SystemExit(f"binary not defined for app '{app_name}' in apps/{app_name}.yaml")

    tag = str(app_cfg.get("tag", "prod"))
    bin_target = load_binary_target(binary_name, tag)

    # 在应用目录下创建可执行文件 symlink：<app_name> -> binaries/.../<binary_name>
    exec_path = apps_root / app_name
    if exec_path.is_symlink() or exec_path.exists():
        exec_path.unlink()

    # 使用相对路径创建 symlink，保证仓库可移动
    rel_target = os.path.relpath(bin_target, start=apps_root)
    os.symlink(rel_target, exec_path)

    print(f"generated app directory: {apps_root}")
    print(f"  config: {cfg_path}")
    print(f"  binary symlink: {exec_path} -> {rel_target}")
    return cfg_path


def refresh_deployment_comments_for_dc(
    dc_id: str,
    hosts_data: Dict,
    dep_data: Dict,
    dep_file: Path,
) -> None:
    """根据 hosts.yaml 信息刷新 deployments.yaml 中的注释。

    - listen_nic: <nic_name>  # <ip>
    - *_cpu: <cpu_id>  # numa <id>

    仅支持新 schema（顶层 host -> apps 映射），旧 schema 直接忽略。
    该函数会重写 deployments.yaml 文件，丢弃原有注释，生成新的注释。
    """

    lines: List[str] = []

    for host_name, apps_map in dep_data.items():
        if not isinstance(apps_map, dict):
            continue

        lines.append(f"{host_name}:")

        if "log_dir" in apps_map:
            lines.append(f"  log_dir: {apps_map.get('log_dir')}")

        if "shared_cpus" in apps_map:
            lines.append(f"  shared_cpus: {apps_map.get('shared_cpus')}")

        host_topology = hosts_data.get(host_name) or {}
        try:
            cpu_numa = build_cpu_numa_map_from_host(host_topology)
        except Exception:
            cpu_numa = {}

        nic_ips: Dict[str, str] = {}
        for nic in host_topology.get("nics", []):
            name = str(nic.get("name"))
            ip = nic.get("ip")
            if name and ip:
                nic_ips[name] = str(ip)

        for app_name, app_def in apps_map.items():
            if app_name == "log_dir":
                continue
            if app_name == "shared_cpus":
                continue
            if not isinstance(app_def, dict):
                continue

            lines.append(f"  {app_name}:")

            # 输出 binary / tag / version 等简单字段
            for key in ("binary", "tag", "version"):
                if key in app_def:
                    lines.append(f"    {key}: {app_def[key]}")

            templates = app_def.get("templates")
            if not templates:
                continue

            lines.append("    templates:")
            for tmpl in templates:
                if not isinstance(tmpl, dict):
                    continue

                template_name = tmpl.get("name")
                lines.append(f"      - name: {template_name}")

                cfg_envs = tmpl.get("cfg_envs") or {}
                if not isinstance(cfg_envs, dict):
                    continue

                lines.append("        cfg_envs:")

                for env_key, env_val in cfg_envs.items():
                    comment = ""

                    if env_key == "listen_nic":
                        ip = nic_ips.get(str(env_val))
                        if ip:
                            comment = f"  # {ip}"
                    elif env_key in ("log_cpu", "main_loop_cpu", "admin_loop_cpu"):
                        try:
                            cpu_id = int(env_val)
                        except (TypeError, ValueError):
                            cpu_id = None
                        if cpu_id is not None:
                            node = cpu_numa.get(cpu_id)
                            if node is not None:
                                comment = f"  # numa {node}"

                    lines.append(f"          {env_key}: {env_val}{comment}")

    dep_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_all() -> None:
    """遍历所有 datacenter/host/app，生成对应配置。

    依赖约定：
    - 每个 dc 对应 deployments/<dc>/deployments.yaml
    - 部分 dc 还可以有 deployments/<dc>/hosts.yaml，提供 CPU/NUMA 拓扑
    - deployments.yaml 中：
        deployments[host].shared_cpus
        deployments[host][app_name] 为该 app 的部署定义
    """

    global HOST_BUSY_ISOLATED_USAGE, APP_GLOBAL_INDEX
    HOST_BUSY_ISOLATED_USAGE = {}
    APP_GLOBAL_INDEX = {}

    deploy_root = ROOT / "deployments"
    if not deploy_root.exists():
        return

    validate_all_schemas(ROOT)

    # 第一遍：构建全局 APP 索引，强制 app_name 在所有 dc/host 之间唯一
    for dc_dir in deploy_root.iterdir():
        if not dc_dir.is_dir():
            continue

        dc_id = dc_dir.name
        dep_file = dc_dir / "deployments.yaml"
        if not dep_file.exists():
            continue

        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}

        # 新写法：顶层 host -> apps
        if "deployments" not in dep_data:
            hosts_map: Dict = dep_data or {}
            for host_name, apps_map in hosts_map.items():
                if not isinstance(apps_map, dict):
                    continue
                for app_name, app_def in apps_map.items():
                    if not isinstance(app_def, dict):
                        continue
                    prev = APP_GLOBAL_INDEX.get(app_name)
                    if prev is not None and prev != (dc_id, host_name):
                        prev_dc, prev_host = prev
                        raise SystemExit(
                            "application '{app}' defined multiple times: "
                            "(dc={dc1}, host={h1}) and (dc={dc2}, host={h2}); "
                            "application names must be globally unique".format(
                                app=app_name,
                                dc1=prev_dc,
                                h1=prev_host,
                                dc2=dc_id,
                                h2=host_name,
                            )
                        )
                    APP_GLOBAL_INDEX[app_name] = (dc_id, host_name)

        # 旧写法：deployments[host][app]
        else:
            deployments_map = dep_data.get("deployments") or {}
            for host_name, host_cfg in deployments_map.items():
                if not isinstance(host_cfg, dict):
                    continue
                for app_name, app_def in host_cfg.items():
                    if app_name == "shared_cpus":
                        continue
                    if not isinstance(app_def, dict):
                        continue
                    prev = APP_GLOBAL_INDEX.get(app_name)
                    if prev is not None and prev != (dc_id, host_name):
                        prev_dc, prev_host = prev
                        raise SystemExit(
                            "application '{app}' defined multiple times: "
                            "(dc={dc1}, host={h1}) and (dc={dc2}, host={h2}); "
                            "application names must be globally unique".format(
                                app=app_name,
                                dc1=prev_dc,
                                h1=prev_host,
                                dc2=dc_id,
                                h2=host_name,
                            )
                        )
                    APP_GLOBAL_INDEX[app_name] = (dc_id, host_name)

    # 第二遍：按 dc/host/app 生成配置
    for dc_dir in deploy_root.iterdir():
        if not dc_dir.is_dir():
            continue

        dc_id = dc_dir.name
        dep_file = dc_dir / "deployments.yaml"
        hosts_file = dc_dir / "hosts.yaml"
        if not dep_file.exists() or not hosts_file.exists():
            continue

        with hosts_file.open() as f:
            hosts_data = yaml.safe_load(f) or {}

        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}

        # 新写法：顶层就是 host -> apps 映射
        if "deployments" not in dep_data:
            hosts_map = dep_data or {}

            # 在生成配置前，根据 hosts.yaml 刷新一次注释
            refresh_deployment_comments_for_dc(dc_id, hosts_data, hosts_map, dep_file)

            # 重新读取（数据结构未变，这一步主要是确保我们使用的是最新文件）
            with dep_file.open() as f:
                dep_data = yaml.safe_load(f) or {}

            hosts_map = dep_data or {}

            for host_name, apps_map in hosts_map.items():
                if not isinstance(apps_map, dict):
                    continue

                # 校验 host 存在
                if host_name not in hosts_data:
                    print(
                        f"host '{host_name}' in {dep_file} not found in hosts.yaml",
                        file=sys.stderr,
                    )
                    continue

                for app_name, app_def in apps_map.items():
                    if not isinstance(app_def, dict):
                        continue

                    print(f"[generate] dc={dc_id} host={host_name} app={app_name}")
                    try:
                        validate_and_render(dc_id, host_name, app_name)
                    except SystemExit as e:
                        print(f"  failed: {e}", file=sys.stderr)

            continue

        # 兼容旧写法：deployments[host][app]
        deployments_map = dep_data.get("deployments") or {}
        for host_name, host_cfg in deployments_map.items():
            try:
                _ = load_datacenter(dc_id, host_name)
            except SystemExit as e:
                print(e, file=sys.stderr)
                continue

            for app_name, app_def in host_cfg.items():
                if app_name == "shared_cpus":
                    continue
                if not isinstance(app_def, dict):
                    continue

                print(f"[generate] dc={dc_id} host={host_name} app={app_name}")
                try:
                    validate_and_render(dc_id, host_name, app_name)
                except SystemExit as e:
                    print(f"  failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    # 无参数：全局生成配置
    if len(sys.argv) == 1:
        generate_all()
    # 2 个参数：dc host（默认 APP_NAME）
    elif len(sys.argv) == 3:
        _, dc, host = sys.argv
        validate_and_render(dc, host, APP_NAME)
    # 3 个参数：dc host app
    elif len(sys.argv) == 4:
        _, dc, host, app = sys.argv
        validate_and_render(dc, host, app)
    else:
        print(
            "Usage: gen_config.py [dc_id host_name [app_name]]  (no args = generate all)",
            file=sys.stderr,
        )
        raise SystemExit(1)
