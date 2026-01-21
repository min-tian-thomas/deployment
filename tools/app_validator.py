from __future__ import annotations

from typing import Dict, Set, Tuple


def validate_host_cpu_sets(
    *,
    total_cpus: int,
    isolated_cpus: Set[int],
    shared_cpus: Set[int],
    host_name: str,
) -> None:
    for cpu in sorted(isolated_cpus | shared_cpus):
        if cpu < 0 or cpu >= total_cpus:
            raise SystemExit(
                f"cpu id {cpu} out of range [0, {total_cpus}) in hosts.yaml for host {host_name}"
            )

    overlap = isolated_cpus & shared_cpus
    if overlap:
        raise SystemExit(
            f"isolated_cpus and shared_cpus overlap for host {host_name}: {sorted(overlap)}"
        )


def parse_template_cfg_envs_cpu_fields(env: Dict) -> Tuple[int, int, int]:
    try:
        log_cpu = int(env["log_cpu"])
        main_loop_cpu = int(env["main_loop_cpu"])
        admin_loop_cpu = int(env["admin_loop_cpu"])
    except KeyError as e:
        raise SystemExit(f"missing cpu field in cfg_envs: {e}")

    return log_cpu, main_loop_cpu, admin_loop_cpu


def validate_app_cpu_allocation(
    *,
    dc_id: str,
    host_name: str,
    app_name: str,
    total_cpus: int,
    isolated_cpus: Set[int],
    shared_cpus: Set[int],
    log_cpu: int,
    main_loop_cpu: int,
    admin_loop_cpu: int,
    busy_usage: Dict[int, str],
) -> None:
    used_cpus = {log_cpu, main_loop_cpu, admin_loop_cpu}

    if main_loop_cpu not in isolated_cpus:
        raise SystemExit(
            f"main_loop_cpu {main_loop_cpu} not in isolated_cpus {sorted(isolated_cpus)}"
        )

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
        raise SystemExit(f"log_cpu {log_cpu} not in shared_cpus {sorted(shared_cpus)}")

    if admin_loop_cpu not in shared_cpus:
        raise SystemExit(
            f"admin_loop_cpu {admin_loop_cpu} not in shared_cpus {sorted(shared_cpus)}"
        )

    for cpu in used_cpus:
        if cpu < 0 or cpu >= total_cpus:
            raise SystemExit(
                f"cpu id {cpu} out of range [0, {total_cpus}) for host {host_name}"
            )

    if len(used_cpus) != 3:
        raise SystemExit(
            "duplicated cpu ids detected among log_cpu/main_loop_cpu/admin_loop_cpu: "
            f"{sorted(used_cpus)}"
        )
