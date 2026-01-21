from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List


def refresh_deployment_comments_for_dc(
    dc_id: str,
    hosts_data: Dict,
    dep_data: Dict,
    dep_file: Path,
    build_cpu_numa_map_from_host: Callable[[Dict], Dict[int, int]],
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
