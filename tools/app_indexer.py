from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import yaml


def build_global_app_index(*, deployments_root: Path) -> Dict[str, Tuple[str, str]]:
    app_index: Dict[str, Tuple[str, str]] = {}

    if not deployments_root.exists():
        return app_index

    for dc_dir in deployments_root.iterdir():
        if not dc_dir.is_dir():
            continue

        dc_id = dc_dir.name
        dep_file = dc_dir / "deployments.yaml"
        if not dep_file.exists():
            continue

        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}

        if "deployments" in dep_data:
            raise SystemExit(
                "legacy deployments schema (top-level 'deployments') is no longer supported"
            )

        hosts_map: Dict = dep_data or {}
        for host_name, apps_map in hosts_map.items():
            if not isinstance(apps_map, dict):
                continue

            for app_name, app_def in apps_map.items():
                if app_name in ("log_dir", "shared_cpus"):
                    continue
                if not isinstance(app_def, dict):
                    continue

                prev = app_index.get(app_name)
                if prev is not None and prev != (dc_id, host_name):
                    prev_dc, prev_host = prev
                    raise SystemExit(
                        "application '{app}' defined multiple times: (dc={dc1}, host={h1}) and (dc={dc2}, host={h2}); "
                        "application names must be globally unique".format(
                            app=app_name,
                            dc1=prev_dc,
                            h1=prev_host,
                            dc2=dc_id,
                            h2=host_name,
                        )
                    )

                app_index[app_name] = (dc_id, host_name)

    return app_index
