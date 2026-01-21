#!/usr/bin/env python3

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_binary_requirements() -> Dict[str, Dict]:
    req_file = ROOT / "deployments" / "required_binaries.yaml"
    if not req_file.exists():
        raise SystemExit(f"binary requirements file not found: {req_file}")

    with req_file.open() as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise SystemExit(
            f"invalid format in {req_file}, expected mapping of binary -> config"
        )

    return data


def prepare_all_binaries() -> None:
    binaries_root = ROOT / "install" / "binaries"
    binaries_root.mkdir(parents=True, exist_ok=True)

    data = load_binary_requirements()

    for binary_name, cfg in data.items():
        if not isinstance(cfg, dict):
            continue

        required_versions_raw = cfg.get("required_versions") or []
        required_versions = {str(v) for v in required_versions_raw}
        if not required_versions:
            continue

        bin_root = binaries_root / binary_name
        bin_root.mkdir(parents=True, exist_ok=True)

        for version in sorted(required_versions):
            print(f"[binary] {binary_name}:{version}")
            bin_dir = bin_root / version
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

        if bin_root.exists():
            for child in bin_root.iterdir():
                if not child.is_dir():
                    continue
                if child.name not in required_versions:
                    print(
                        f"[binary-clean] removing obsolete version: {binary_name}/{child.name}"
                    )
                    shutil.rmtree(child, ignore_errors=True)


if __name__ == "__main__":
    prepare_all_binaries()
