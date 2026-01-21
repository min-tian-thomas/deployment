from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Set


def render_validate_and_inject(
    *,
    template_text: str,
    replacements: Dict[str, object],
    app_name: str,
    template_name: str,
    host_log_dir: str | None,
    total_cpus: int,
    isolated_cpus: Set[int],
    admin_loop_cpu: int,
    dc_id: str,
    host_name: str,
    busy_usage: Dict[int, str],
) -> str:
    rendered = template_text
    for key, value in replacements.items():
        rendered = re.sub(r"{{\s*" + re.escape(str(key)) + r"\s*}}", str(value), rendered)

    leftover = re.findall(r"{{\s*[^}]+\s*}}", rendered)
    if leftover:
        raise SystemExit(
            f"unresolved template variables in rendered config for app '{app_name}' "
            f"(template {template_name}): {sorted(set(leftover))}"
        )

    try:
        rendered_obj = json.loads(rendered)
    except json.JSONDecodeError as e:
        raise SystemExit(f"failed to parse rendered JSON for app '{app_name}' (template {template_name}): {e}")

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

    return json.dumps(rendered_obj, indent=4) + "\n"
