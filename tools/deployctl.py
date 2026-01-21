#!/usr/bin/env python3

from __future__ import annotations

import argparse

import gen_binaries
import gen_config
from app_indexer import build_global_app_index
from schema_validation import validate_all_schemas


def main() -> None:
    parser = argparse.ArgumentParser(prog="deployctl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--root", default=None)

    p_binaries = sub.add_parser("binaries")
    p_binaries.add_argument("--root", default=None)

    p_config = sub.add_parser("config")
    p_config.add_argument("--dc", default=None)
    p_config.add_argument("--host", default=None)
    p_config.add_argument("--app", default=None)

    args = parser.parse_args()

    root = gen_config.ROOT

    if args.cmd == "validate":
        validate_all_schemas(root)
        print("[validate] 所有 YAML/schema 校验通过")
        return

    if args.cmd == "binaries":
        gen_binaries.prepare_all_binaries()
        return

    if args.cmd == "config":
        if args.dc is None and args.host is None and args.app is None:
            gen_config.generate_all()
            return

        if args.dc is None or args.host is None:
            raise SystemExit("--dc and --host are required when using filtered config")

        validate_all_schemas(root)

        # build global app index for cross-app references
        gen_config.APP_GLOBAL_INDEX = build_global_app_index(deployments_root=root / "deployments")
        gen_config.HOST_BUSY_ISOLATED_USAGE = {}

        target_app = args.app or gen_config.APP_NAME
        gen_config.validate_and_render(args.dc, args.host, target_app)
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
