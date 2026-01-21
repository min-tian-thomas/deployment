from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml


def load_binary_requirements(root: Path) -> Dict[str, Dict]:
    req_file = root / "deployments" / "required_binaries.yaml"
    if not req_file.exists():
        raise SystemExit(f"binary requirements file not found: {req_file}")

    with req_file.open() as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise SystemExit(
            f"invalid format in {req_file}, expected mapping of binary -> config"
        )

    return data


def load_binary_target(root: Path, binary_name: str, tag_or_version: str) -> Path:
    req_file = root / "deployments" / "required_binaries.yaml"
    data = load_binary_requirements(root)

    binary_cfg: Dict = data.get(binary_name) or {}
    if not binary_cfg:
        raise SystemExit(f"binary '{binary_name}' not defined in {req_file}")

    tags: Dict = binary_cfg.get("tags") or {}
    required_versions_raw = binary_cfg.get("required_versions") or []
    required_versions = {str(v) for v in required_versions_raw}

    if tag_or_version in tags:
        version = str(tags[tag_or_version])
    else:
        version = str(tag_or_version)

    if required_versions and version not in required_versions:
        raise SystemExit(
            f"version '{version}' (from tag '{tag_or_version}') not in required_versions "
            f"for binary '{binary_name}' in {req_file}"
        )

    bin_dir = root / "install" / "binaries" / binary_name / version
    bin_dir.mkdir(parents=True, exist_ok=True)

    bin_path = bin_dir / binary_name

    if not bin_path.exists():
        bin_path.write_text(
            "#!/usr/bin/env bash\n" f"echo 'mock {binary_name} {version}' \"$@\"\n",
            encoding="utf-8",
        )
        try:
            bin_path.chmod(0o755)
        except PermissionError:
            pass

    return bin_path
