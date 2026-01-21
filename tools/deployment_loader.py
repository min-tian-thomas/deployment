from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml


def load_datacenter(root: Path, dc_id: str, host_name: str) -> Dict:
    dc_file = root / "deployments" / dc_id / "hosts.yaml"
    if not dc_file.exists():
        raise SystemExit(f"hosts topology file not found for datacenter '{dc_id}': {dc_file}")

    with dc_file.open() as f:
        data = yaml.safe_load(f) or {}

    hosts_map: Dict = data or {}
    host = hosts_map.get(host_name)
    if not isinstance(host, dict):
        raise SystemExit(f"host '{host_name}' not found in {dc_file}")

    return host


def load_deployment(root: Path, dc_id: str, host_name: str, app_name: str) -> Dict:
    dep_file = root / "deployments" / dc_id / "deployments.yaml"
    with dep_file.open() as f:
        data = yaml.safe_load(f) or {}

    hosts_map: Dict = data or {}
    host_cfg: Dict = hosts_map.get(host_name) or {}
    if not host_cfg:
        raise SystemExit(f"no deployments defined for host '{host_name}' in {dep_file}")

    app_def: Dict = host_cfg.get(app_name) or {}
    if not app_def:
        raise SystemExit(f"no app '{app_name}' defined under host '{host_name}' in {dep_file}")

    return {
        "shared_cpus": host_cfg.get("shared_cpus", ""),
        "log_dir": host_cfg.get("log_dir"),
        "app": app_def,
    }
