#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Set

import yaml

ROOT = Path(__file__).resolve().parents[1]

# 当前 MVP 仅支持单一应用 dce_md_publisher
APP_NAME = "dce_md_publisher"


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
    dc_file = ROOT / "datacenters" / "datacenters.yaml"
    with dc_file.open() as f:
        data = yaml.safe_load(f)

    dcs: List[Dict] = data.get("datacenters") or []
    dc = next((d for d in dcs if d.get("id") == dc_id), None)
    if not dc:
        raise SystemExit(f"datacenter '{dc_id}' not found in {dc_file}")

    hosts: List[Dict] = dc.get("hosts") or []
    host = next((h for h in hosts if h.get("name") == host_name), None)
    if not host:
        raise SystemExit(f"host '{host_name}' not found in datacenter '{dc_id}'")

    return host


def build_cpu_numa_map(total_cpus: int, numa_nodes: int) -> Dict[int, int]:
    if total_cpus <= 0 or numa_nodes <= 0:
        raise ValueError("cpus and numa_nodes must be positive")
    base = total_cpus // numa_nodes
    rem = total_cpus % numa_nodes

    mapping: Dict[int, int] = {}
    cpu = 0
    for node in range(numa_nodes):
        count = base + (1 if node < rem else 0)
        for _ in range(count):
            mapping[cpu] = node
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

    dep_file = ROOT / "deploy" / dc_id / "deployments.yaml"
    with dep_file.open() as f:
        data = yaml.safe_load(f)

    deployments = data.get("deployments") or {}
    host_cfg: Dict = deployments.get(host_name) or {}
    if not host_cfg:
        raise SystemExit(f"no deployments defined for host '{host_name}' in {dep_file}")

    app_def: Dict = host_cfg.get(app_name) or {}
    if not app_def:
        raise SystemExit(
            f"no app '{app_name}' defined under host '{host_name}' in {dep_file}"
        )

    return {
        "shared_cpus": host_cfg.get("shared_cpus", ""),
        "app": app_def,
    }


def load_app_config(app_name: str) -> Dict:
    """加载 apps/ 下的应用定义。

    期望结构：

    dce_md_publisher:
      binary: md_server
      tag: prod
      config_template: dce_md_publisher.json
    """

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


def validate_and_render(
    dc_id: str = "idc_shanghai", host_name: str = "host01", app_name: str = APP_NAME
) -> Path:
    host = load_datacenter(dc_id, host_name)
    total_cpus = int(host.get("cpus", 0))
    numa_nodes = int(host.get("numa_nodes", 1))
    log_cpus = parse_cpu_set(str(host.get("log_cpus", "")))
    isolated_cpus = parse_cpu_set(str(host.get("isolated_cpus", "")))

    cpu_numa = build_cpu_numa_map(total_cpus, numa_nodes)

    dep_info = load_deployment(dc_id, host_name, app_name)
    shared_cpus = parse_cpu_set(str(dep_info.get("shared_cpus", "")))

    app_def: Dict = dep_info["app"]

    cfg_envs: List[Dict] = app_def.get("cfg_envs") or []
    if not cfg_envs:
        raise SystemExit("cfg_envs is empty in deployments definition")

    env = cfg_envs[0]

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

    # 渲染模板
    app_cfg = load_app_config(app_name)
    template_name = app_cfg.get("config_template")
    if not template_name:
        raise SystemExit(
            f"config_template not defined for app '{app_name}' in apps/{app_name}.yaml"
        )

    template_path = ROOT / "deploy" / dc_id / "templates" / template_name
    template_text = template_path.read_text()

    replacements = {
        "listen_nic": env.get("listen_nic"),
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

    out_path = ROOT / "deploy" / dc_id / f"{app_name}_{host_name}.json"
    out_path.write_text(rendered + "\n")

    print(f"generated config written to: {out_path}")
    return out_path


def generate_all() -> None:
    """遍历所有 datacenter/host/app，生成对应配置。

    依赖约定：
    - datacenters/datacenters.yaml 列出所有 dc 和 hosts
    - 每个 dc 对应 deploy/<dc>/deployments.yaml
    - deployments.yaml 中：
        deployments[host].shared_cpus
        deployments[host][app_name] 为该 app 的部署定义
    """

    dc_file = ROOT / "datacenters" / "datacenters.yaml"
    with dc_file.open() as f:
        dc_data = yaml.safe_load(f) or {}

    datacenters = dc_data.get("datacenters") or []

    for dc in datacenters:
        dc_id = dc.get("id")
        if not dc_id:
            continue

        dep_file = ROOT / "deploy" / dc_id / "deployments.yaml"
        if not dep_file.exists():
            continue

        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}

        deployments = dep_data.get("deployments") or {}
        for host_name, host_cfg in deployments.items():
            # 校验 host 在 datacenters.yaml 中存在
            try:
                _ = load_datacenter(dc_id, host_name)
            except SystemExit as e:
                print(e, file=sys.stderr)
                continue

            # 跳过非 app 的键（目前只保留 shared_cpus 作为 host 级别配置）
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
    # 无参数：全局生成
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
