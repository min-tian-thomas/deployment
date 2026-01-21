#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, List, Set, Tuple

import yaml

import gen_binaries
import gen_config
from app_indexer import build_global_app_index
from schema_validation import validate_all_schemas


PLACEHOLDER_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*}}")


def build_effective_deps(
    root: Path, app_index: Dict[str, Tuple[str, str]]
) -> Dict[str, Set[str]]:
    """Build effective depends_on for each app.

    If an app does not declare depends_on, its dependencies are inferred from
    cross-app template references ({{OtherApp.key}}). If depends_on is
    declared, it is taken as-is (schema_validation already checked consistency
    and printed warnings for extras).
    """

    deploy_root = root / "deployments"
    # 预读每个 dc 的 deployments.yaml
    dep_maps: Dict[str, Dict] = {}
    for dc_dir in deploy_root.iterdir():
        if not dc_dir.is_dir():
            continue
        dc_id = dc_dir.name
        dep_file = dc_dir / "deployments.yaml"
        if not dep_file.exists():
            continue
        with dep_file.open() as f:
            dep_maps[dc_id] = yaml.safe_load(f) or {}

    deps_by_app: Dict[str, Set[str]] = {name: set() for name in app_index.keys()}

    for app_name, (dc_id, host_name) in app_index.items():
        dep_map = dep_maps.get(dc_id) or {}
        host_obj = dep_map.get(host_name) or {}
        if not isinstance(host_obj, dict):
            continue
        app_obj = host_obj.get(app_name) or {}
        if not isinstance(app_obj, dict):
            continue

        # 显式 depends_on
        raw_depends = app_obj.get("depends_on")
        declared: Set[str] | None
        if raw_depends is None:
            declared = None
        else:
            if isinstance(raw_depends, list):
                declared = {str(x) for x in raw_depends}
            else:
                declared = {str(raw_depends)}

        # 模板引用到的 app
        referenced: Set[str] = set()
        templates = app_obj.get("templates") or []
        if isinstance(templates, list):
            for tmpl in templates:
                if not isinstance(tmpl, dict):
                    continue
                template_name = tmpl.get("name")
                if not template_name:
                    continue
                template_path = root / "deployments" / dc_id / "templates" / str(
                    template_name
                )
                if not template_path.exists():
                    continue
                text = template_path.read_text()
                for ref_app, _ref_key in set(PLACEHOLDER_PATTERN.findall(text)):
                    referenced.add(ref_app)

        if declared is None:
            deps_by_app[app_name] = referenced
        else:
            deps_by_app[app_name] = declared

    return deps_by_app


def topo_sort(deps: Dict[str, Set[str]]) -> List[str]:
    """Topologically sort applications based on dependency edges.

    deps[a] 是 a 依赖的应用集合。排序结果保证依赖总是出现在依赖者之前。
    若存在环，则抛出 SystemExit。
    """

    # 构建出度邻接表和入度
    outgoing: Dict[str, Set[str]] = defaultdict(set)
    in_degree: Dict[str, int] = {name: 0 for name in deps.keys()}

    for app, app_deps in deps.items():
        for dep in app_deps:
            if dep not in in_degree:
                in_degree[dep] = 0
            outgoing[dep].add(app)
            in_degree[app] = in_degree.get(app, 0) + 1

    # Kahn 算法
    queue: Deque[str] = deque(sorted([a for a, d in in_degree.items() if d == 0]))
    order: List[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for succ in sorted(outgoing.get(node, ())):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(order) != len(in_degree):
        raise SystemExit("dependency graph contains cycles; please check depends_on")

    return order


def _print_plan(
    *,
    root: Path,
    app_index: Dict[str, Tuple[str, str]],
    deps: Dict[str, Set[str]],
    order: List[str],
    dc_filter: str | None,
    host_filter: str | None,
    app_filter: str | None,
) -> None:
    if app_filter:
        if app_filter not in deps:
            raise SystemExit(f"app '{app_filter}' not found in deployments")

        # 计算 app 的依赖闭包
        subset: Set[str] = set()

        def dfs(a: str) -> None:
            for d in deps.get(a, set()):
                if d not in subset:
                    subset.add(d)
                    dfs(d)

        dfs(app_filter)
        subset.add(app_filter)

        sub_order = [a for a in order if a in subset]
        print(f"[plan] dependency chain for app='{app_filter}':")
        for name in sub_order:
            dc, host = app_index.get(name, ("?", "?"))
            print(f"  {name} (dc={dc}, host={host}, depends_on={sorted(deps.get(name, ()))})")
        return

    # 按 dc/host 分组
    hosts: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for app in order:
        loc = app_index.get(app)
        if not loc:
            continue
        dc_id, host = loc
        if dc_filter and dc_id != dc_filter:
            continue
        if host_filter and host != host_filter:
            continue
        hosts[(dc_id, host)].append(app)

    if not hosts:
        print("[plan] no applications matched the given filters")
        return

    for (dc_id, host), apps in sorted(hosts.items()):
        print(f"[plan] dc={dc_id} host={host}")
        print("  Start order:")
        for name in apps:
            ds = sorted(deps.get(name, ()))
            if ds:
                print(f"    - {name} (depends_on: {ds})")
            else:
                print(f"    - {name}")
        print("  Stop order:")
        for name in reversed(apps):
            print(f"    - {name}")
        print()


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

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--dc", default=None)
    p_plan.add_argument("--host", default=None)
    p_plan.add_argument("--app", default=None)

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

    if args.cmd == "plan":
        validate_all_schemas(root)
        app_index = build_global_app_index(deployments_root=root / "deployments")
        deps = build_effective_deps(root, app_index)
        order = topo_sort(deps)
        _print_plan(
            root=root,
            app_index=app_index,
            deps=deps,
            order=order,
            dc_filter=args.dc,
            host_filter=args.host,
            app_filter=args.app,
        )
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
