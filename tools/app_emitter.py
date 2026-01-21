from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple


def write_rendered_config(*, apps_root: Path, template_name: str, rendered: str) -> Path:
    apps_root.mkdir(parents=True, exist_ok=True)
    cfg_path = apps_root / template_name
    cfg_path.write_text(rendered)
    return cfg_path


def create_or_replace_exec_symlink(
    *,
    apps_root: Path,
    app_name: str,
    bin_target: Path,
) -> Tuple[Path, str]:
    exec_path = apps_root / app_name
    if exec_path.is_symlink() or exec_path.exists():
        exec_path.unlink()

    rel_target = os.path.relpath(bin_target, start=apps_root)
    os.symlink(rel_target, exec_path)
    return exec_path, rel_target
