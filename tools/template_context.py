from __future__ import annotations

from typing import Dict, Optional


def build_template_replacements(
    *,
    template_text: str,
    env: Dict,
    host: Dict,
    log_cpu: int,
    main_loop_cpu: int,
    admin_loop_cpu: int,
) -> Dict[str, object]:
    needs_listen_nic = "{{listen_nic}}" in template_text
    nic_ip: Optional[str] = None
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
                f"ip for nic '{nic_name}' not found in hosts.yaml for host '{host.get('name') or ''}'"
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

    return replacements
