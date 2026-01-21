#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml

from schema_validation import validate_all_schemas
from binary_resolver import load_binary_target
from config_renderer import render_validate_and_inject
import deployment_loader
from cross_ref_resolver import resolve_cross_app_placeholders
from comment_refresher import refresh_deployment_comments_for_dc
from cpu_topology import build_cpu_numa_map_from_host, parse_cpu_set
from app_validator import (
    parse_template_cfg_envs_cpu_fields,
    validate_app_cpu_allocation,
    validate_host_cpu_sets,
)
from app_emitter import create_or_replace_exec_symlink, write_rendered_config
from app_indexer import build_global_app_index
from template_context import build_template_replacements

ROOT = Path(__file__).resolve().parents[1]

# 当前 MVP 仅支持单一应用 dce_md_publisher
APP_NAME = "dce_md_publisher"

# 记录同一 host 上 busy-spin（main_loop_cpu）在 isolated_cpus 中的使用情况，用于跨应用去重校验
HOST_BUSY_ISOLATED_USAGE: Dict[Tuple[str, str], Dict[int, str]] = {}

# 全局应用索引：强制 app_name 在所有 dc/host 之间唯一，用于跨实例引用
# 结构：APP_GLOBAL_INDEX[app_name] = (dc_id, host_name)
APP_GLOBAL_INDEX: Dict[str, Tuple[str, str]] = {}


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

    return deployment_loader.load_datacenter(ROOT, dc_id, host_name)


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

    return deployment_loader.load_deployment(ROOT, dc_id, host_name, app_name)


def validate_and_render(
    dc_id: str = "idc_shanghai", host_name: str = "host01", app_name: str = APP_NAME
) -> Path:
    host = load_datacenter(dc_id, host_name)
    total_cpus = int(host.get("cpus", 0))
    isolated_cpus = parse_cpu_set(str(host.get("isolated_cpus", "")))
    host_shared_cpus = parse_cpu_set(str(host.get("shared_cpus", "")))

    validate_host_cpu_sets(
        total_cpus=total_cpus,
        isolated_cpus=isolated_cpus,
        shared_cpus=host_shared_cpus,
        host_name=host_name,
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

            log_cpu, main_loop_cpu, admin_loop_cpu = parse_template_cfg_envs_cpu_fields(env)

            host_key = (dc_id, host_name)
            busy_usage = HOST_BUSY_ISOLATED_USAGE.setdefault(host_key, {})

            validate_app_cpu_allocation(
                dc_id=dc_id,
                host_name=host_name,
                app_name=app_name,
                total_cpus=total_cpus,
                isolated_cpus=isolated_cpus,
                shared_cpus=shared_cpus,
                log_cpu=log_cpu,
                main_loop_cpu=main_loop_cpu,
                admin_loop_cpu=admin_loop_cpu,
                busy_usage=busy_usage,
            )

            used_cpus = {log_cpu, main_loop_cpu, admin_loop_cpu}

            # 生成 NUMA comments（打印到 stdout，方便审阅）
            print(f"CPU NUMA mapping for used CPUs (template {template_name}):")
            for cpu in sorted(used_cpus):
                node = cpu_numa.get(cpu, -1)
                print(f"  cpu {cpu}: numa_node {node}")

            template_path = ROOT / "deployments" / dc_id / "templates" / template_name
            if not template_path.exists():
                raise SystemExit(f"template file not found: {template_path}")

            template_text = template_path.read_text()

            replacements = build_template_replacements(
                template_text=template_text,
                env=env,
                host=host,
                log_cpu=log_cpu,
                main_loop_cpu=main_loop_cpu,
                admin_loop_cpu=admin_loop_cpu,
            )

            cross_refs = resolve_cross_app_placeholders(
                dc_id=dc_id,
                host_name=host_name,
                template_text=template_text,
                template_name=template_name,
                app_name=app_name,
                app_index=APP_GLOBAL_INDEX,
                repo_root=ROOT,
            )
            replacements.update(cross_refs)

            rendered = render_validate_and_inject(
                template_text=template_text,
                replacements=replacements,
                app_name=app_name,
                template_name=template_name,
                host_log_dir=str(host_log_dir) if host_log_dir is not None else None,
                total_cpus=total_cpus,
                isolated_cpus=isolated_cpus,
                admin_loop_cpu=admin_loop_cpu,
                dc_id=dc_id,
                host_name=host_name,
                busy_usage=busy_usage,
            )

            last_cfg_path = write_rendered_config(
                apps_root=apps_root, template_name=template_name, rendered=rendered
            )

        # 解析 binary + tag/version，创建或指向具体版本的二进制
        bin_target = load_binary_target(ROOT, binary_name, tag_or_version)

        exec_path, rel_target = create_or_replace_exec_symlink(
            apps_root=apps_root,
            app_name=app_name,
            bin_target=bin_target,
        )

        print(f"generated app directory: {apps_root}")
        if last_cfg_path is not None:
            print(f"  last config: {last_cfg_path}")
        print(f"  binary symlink: {exec_path} -> {rel_target}")
        # 返回最后一个模板生成的配置路径
        return last_cfg_path if last_cfg_path is not None else exec_path

    raise SystemExit(
        "legacy schema is no longer supported: please use deployments/<dc>/deployments.yaml with templates list"
    )


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

    APP_GLOBAL_INDEX.update(build_global_app_index(deployments_root=deploy_root))

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
            refresh_deployment_comments_for_dc(
                dc_id,
                hosts_data,
                hosts_map,
                dep_file,
                build_cpu_numa_map_from_host,
            )

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

        raise SystemExit(
            "legacy deployments schema (top-level 'deployments') is no longer supported"
        )


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
