from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

import yaml


def _schema_err(ctx: str, msg: str) -> str:
    return f"{ctx}: {msg}"


def _as_mapping(val: object, ctx: str) -> Dict:
    if not isinstance(val, dict):
        raise SystemExit(_schema_err(ctx, f"expected mapping, got {type(val).__name__}"))
    return val


def _as_list(val: object, ctx: str) -> List:
    if not isinstance(val, list):
        raise SystemExit(_schema_err(ctx, f"expected list, got {type(val).__name__}"))
    return val


def _as_str(val: object, ctx: str) -> str:
    if val is None:
        raise SystemExit(_schema_err(ctx, "expected string, got null"))
    if not isinstance(val, str):
        raise SystemExit(_schema_err(ctx, f"expected string, got {type(val).__name__}"))
    if not val.strip():
        raise SystemExit(_schema_err(ctx, "string must not be empty"))
    return val


def _as_int(val: object, ctx: str) -> int:
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit(_schema_err(ctx, f"expected int, got {val!r}"))


def validate_required_binaries_schema(root: Path) -> None:
    req_file = root / "deployments" / "required_binaries.yaml"
    if not req_file.exists():
        raise SystemExit(_schema_err("deployments/required_binaries.yaml", "file not found"))

    with req_file.open() as f:
        data = yaml.safe_load(f) or {}

    root_map = _as_mapping(data, "deployments/required_binaries.yaml")

    for bin_name, cfg in root_map.items():
        ctx = f"deployments/required_binaries.yaml:{bin_name}"
        cfg_map = _as_mapping(cfg, ctx)

        tags = cfg_map.get("tags")
        if tags is not None:
            tags_map = _as_mapping(tags, ctx + ".tags")
            for tag_k, tag_v in tags_map.items():
                _as_str(tag_k, ctx + ".tags.<tag>")
                _as_str(tag_v, ctx + f".tags.{tag_k}")

        req_versions = cfg_map.get("required_versions")
        if req_versions is None:
            raise SystemExit(_schema_err(ctx, "missing required_versions"))
        req_list = _as_list(req_versions, ctx + ".required_versions")
        if not req_list:
            raise SystemExit(_schema_err(ctx + ".required_versions", "must not be empty"))
        req_set = {str(v) for v in req_list}

        if tags is not None:
            for tag_k, tag_v in _as_mapping(tags, ctx + ".tags").items():
                if str(tag_v) not in req_set:
                    raise SystemExit(
                        _schema_err(
                            ctx + f".tags.{tag_k}",
                            f"version '{tag_v}' not in required_versions {sorted(req_set)}",
                        )
                    )


def validate_hosts_schema(dc_id: str, hosts_data: Dict) -> None:
    hosts_map = _as_mapping(hosts_data, f"deployments/{dc_id}/hosts.yaml")
    for host_name, host_cfg in hosts_map.items():
        ctx = f"deployments/{dc_id}/hosts.yaml:{host_name}"
        host_obj = _as_mapping(host_cfg, ctx)

        _as_int(host_obj.get("cpus"), ctx + ".cpus")
        _as_str(str(host_obj.get("isolated_cpus", "")), ctx + ".isolated_cpus")
        _as_str(str(host_obj.get("shared_cpus", "")), ctx + ".shared_cpus")

        nodes = host_obj.get("numa_nodes")
        if nodes is not None:
            nodes_list = _as_list(nodes, ctx + ".numa_nodes")
            for i, node in enumerate(nodes_list):
                nctx = ctx + f".numa_nodes[{i}]"
                node_obj = _as_mapping(node, nctx)
                _as_int(node_obj.get("id"), nctx + ".id")
                _as_str(str(node_obj.get("cpus", "")), nctx + ".cpus")

        nics = host_obj.get("nics")
        nics_list = _as_list(nics, ctx + ".nics")
        if not nics_list:
            raise SystemExit(_schema_err(ctx + ".nics", "must not be empty"))
        for i, nic in enumerate(nics_list):
            nctx = ctx + f".nics[{i}]"
            nic_obj = _as_mapping(nic, nctx)
            _as_str(nic_obj.get("name"), nctx + ".name")
            _as_str(nic_obj.get("ip"), nctx + ".ip")


def _extract_first_cfg_envs(app_obj: Dict, ctx: str) -> Dict:
    templates = app_obj.get("templates")
    tlist = _as_list(templates, ctx + ".templates")
    if not tlist:
        raise SystemExit(_schema_err(ctx + ".templates", "must not be empty"))

    tmpl0 = _as_mapping(tlist[0], ctx + ".templates[0]")
    cfg_envs = tmpl0.get("cfg_envs")
    return _as_mapping(cfg_envs, ctx + ".templates[0].cfg_envs")


def validate_deployments_schema(
    root: Path, dc_id: str, dep_data: Dict, hosts_data: Dict
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, Dict], Dict[Tuple[str, str], Dict]]:
    dep_map = _as_mapping(dep_data, f"deployments/{dc_id}/deployments.yaml")
    hosts_map = _as_mapping(hosts_data, f"deployments/{dc_id}/hosts.yaml")

    if "deployments" in dep_map:
        raise SystemExit(
            _schema_err(
                f"deployments/{dc_id}/deployments.yaml",
                "old schema with top-level 'deployments' is not supported for strict validation",
            )
        )

    allowed_host_level_keys = {"log_dir", "shared_cpus"}

    app_index: Dict[str, Tuple[str, str]] = {}
    app_cfg_envs: Dict[str, Dict] = {}
    host_topology_map: Dict[Tuple[str, str], Dict] = {}

    for host_name, host_cfg in dep_map.items():
        hctx = f"deployments/{dc_id}/deployments.yaml:{host_name}"
        host_obj = _as_mapping(host_cfg, hctx)

        if host_name not in hosts_map:
            raise SystemExit(
                _schema_err(
                    hctx,
                    f"host '{host_name}' not found in deployments/{dc_id}/hosts.yaml",
                )
            )

        host_topology_map[(dc_id, host_name)] = _as_mapping(hosts_map[host_name], f"deployments/{dc_id}/hosts.yaml:{host_name}")

        for k, v in host_obj.items():
            if k in allowed_host_level_keys:
                continue
            if not isinstance(v, dict):
                raise SystemExit(
                    _schema_err(
                        hctx,
                        f"invalid host-level key '{k}': only {sorted(allowed_host_level_keys)} are allowed (other keys must be application mappings)",
                    )
                )

        log_dir = host_obj.get("log_dir")
        if log_dir is not None:
            _as_str(log_dir, hctx + ".log_dir")

        shared_cpus = host_obj.get("shared_cpus")
        if shared_cpus is not None:
            _as_str(str(shared_cpus), hctx + ".shared_cpus")

        for app_name, app_def in host_obj.items():
            if app_name in allowed_host_level_keys:
                continue

            actx = hctx + f".{app_name}"
            app_obj = _as_mapping(app_def, actx)
            _as_str(app_obj.get("binary"), actx + ".binary")

            if app_obj.get("tag") is None and app_obj.get("version") is None:
                raise SystemExit(_schema_err(actx, "missing tag or version"))

            templates = app_obj.get("templates")
            tlist = _as_list(templates, actx + ".templates")
            if not tlist:
                raise SystemExit(_schema_err(actx + ".templates", "must not be empty"))

            for i, tmpl in enumerate(tlist):
                tctx = actx + f".templates[{i}]"
                tmpl_obj = _as_mapping(tmpl, tctx)
                template_name = _as_str(tmpl_obj.get("name"), tctx + ".name")
                template_path = root / "deployments" / dc_id / "templates" / template_name
                if not template_path.exists():
                    raise SystemExit(
                        _schema_err(
                            tctx + ".name",
                            f"template file not found: {template_path}",
                        )
                    )

                cfg_envs = tmpl_obj.get("cfg_envs")
                cfg_map = _as_mapping(cfg_envs, tctx + ".cfg_envs")

                _as_int(cfg_map.get("log_cpu"), tctx + ".cfg_envs.log_cpu")
                _as_int(cfg_map.get("main_loop_cpu"), tctx + ".cfg_envs.main_loop_cpu")
                _as_int(cfg_map.get("admin_loop_cpu"), tctx + ".cfg_envs.admin_loop_cpu")

            if app_name in app_index and app_index[app_name] != (dc_id, host_name):
                prev_dc, prev_host = app_index[app_name]
                raise SystemExit(
                    _schema_err(
                        actx,
                        "application '{app}' defined multiple times: (dc={dc1}, host={h1}) and (dc={dc2}, host={h2}); application names must be globally unique".format(
                            app=app_name,
                            dc1=prev_dc,
                            h1=prev_host,
                            dc2=dc_id,
                            h2=host_name,
                        ),
                    )
                )

            app_index[app_name] = (dc_id, host_name)
            app_cfg_envs[app_name] = _extract_first_cfg_envs(app_obj, actx)

    return app_index, app_cfg_envs, host_topology_map


def _validate_cross_app_refs(
    root: Path,
    app_index: Dict[str, Tuple[str, str]],
    app_cfg_envs: Dict[str, Dict],
    host_topology_map: Dict[Tuple[str, str], Dict],
) -> None:
    placeholder_pattern = re.compile(r"{{\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*}}")

    for app_name, (dc_id, host_name) in app_index.items():
        dep_file = root / "deployments" / dc_id / "deployments.yaml"
        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}
        dep_map = _as_mapping(dep_data, f"deployments/{dc_id}/deployments.yaml")
        host_obj = _as_mapping(dep_map.get(host_name) or {}, f"deployments/{dc_id}/deployments.yaml:{host_name}")
        app_obj = _as_mapping(host_obj.get(app_name) or {}, f"deployments/{dc_id}/deployments.yaml:{host_name}.{app_name}")

        actx = f"deployments/{dc_id}/deployments.yaml:{host_name}.{app_name}"

        # 读取显式声明的 depends_on（如果有）
        declared_deps: set[str] | None
        raw_depends = app_obj.get("depends_on")
        if raw_depends is None:
            declared_deps = None
        else:
            dep_list = _as_list(raw_depends, actx + ".depends_on")
            declared_deps = set()
            for i, dep in enumerate(dep_list):
                dep_name = _as_str(dep, actx + f".depends_on[{i}]")
                declared_deps.add(dep_name)

        referenced_apps: set[str] = set()

        templates = _as_list(app_obj.get("templates"), actx + ".templates")
        for i, tmpl in enumerate(templates):
            tctx = actx + f".templates[{i}]"
            tmpl_obj = _as_mapping(tmpl, tctx)
            template_name = _as_str(tmpl_obj.get("name"), tctx + ".name")
            template_path = root / "deployments" / dc_id / "templates" / template_name
            template_text = template_path.read_text()

            for ref_app, ref_key in set(placeholder_pattern.findall(template_text)):
                referenced_apps.add(ref_app)
                ref_loc = app_index.get(ref_app)
                if ref_loc is None:
                    raise SystemExit(
                        _schema_err(
                            tctx + ".name",
                            f"referenced application '{ref_app}' not found (placeholder '{{{{{ref_app}.{ref_key}}}}}')",
                        )
                    )

                ref_dc, ref_host = ref_loc
                ref_env = app_cfg_envs.get(ref_app) or {}

                if ref_key not in ref_env:
                    raise SystemExit(
                        _schema_err(
                            tctx + ".name",
                            f"referenced key '{ref_key}' not found in cfg_envs of app '{ref_app}' (placeholder '{{{{{ref_app}.{ref_key}}}}}')",
                        )
                    )

                if "shm" in ref_key.lower() and (dc_id != ref_dc or host_name != ref_host):
                    raise SystemExit(
                        _schema_err(
                            tctx + ".name",
                            "shared-memory key '{app}.{key}' must be used on the same host (referencer dc={dc_ref}, host={h_ref}, owner dc={dc_owner}, host={h_owner})".format(
                                app=ref_app,
                                key=ref_key,
                                dc_ref=dc_id,
                                h_ref=host_name,
                                dc_owner=ref_dc,
                                h_owner=ref_host,
                            ),
                        )
                    )

                if ref_key == "listen_nic":
                    topo = host_topology_map.get((ref_dc, ref_host))
                    if topo is None:
                        raise SystemExit(
                            _schema_err(
                                tctx + ".name",
                                f"hosts topology not loaded for referenced app '{ref_app}' (dc={ref_dc}, host={ref_host})",
                            )
                        )

                    nic_name = str(ref_env.get("listen_nic"))
                    nics = topo.get("nics") or []
                    ip = None
                    for nic in nics:
                        if isinstance(nic, dict) and nic.get("name") == nic_name:
                            ip = nic.get("ip")
                            break
                    if ip is None:
                        raise SystemExit(
                            _schema_err(
                                tctx + ".name",
                                f"ip for nic '{nic_name}' not found in hosts.yaml for referenced app '{ref_app}' (dc={ref_dc}, host={ref_host})",
                            )
                        )

        # 校验 depends_on 与实际跨应用引用的一致性，以及被依赖应用是否存在
        if declared_deps is not None:
            # 1) depends_on 中声明的应用必须在全局 app_index 中存在
            unknown = sorted(d for d in declared_deps if d not in app_index)
            if unknown:
                raise SystemExit(
                    _schema_err(
                        actx + ".depends_on",
                        "depends_on contains unknown applications: " f"{unknown}",
                    )
                )

            # 2) 所有模板中引用到的 app 必须被 depends_on 覆盖
            missing = referenced_apps - declared_deps
            if missing:
                raise SystemExit(
                    _schema_err(
                        actx + ".depends_on",
                        "depends_on is missing applications referenced in templates: "
                        f"{sorted(missing)}",
                    )
                )

            # 3) 多写的 depends_on 项仅打印 warning（前提是这些 app 确实存在）
            extra = declared_deps - referenced_apps
            if extra:
                # 仅告警，不阻止通过
                print(
                    "[validate][warning] app '{app}' has extra depends_on entries "
                    "not referenced in templates: {extras}".format(
                        app=app_name,
                        extras=sorted(extra),
                    )
                )


def validate_all_schemas(root: Path) -> None:
    validate_required_binaries_schema(root)

    deploy_root = root / "deployments"
    if not deploy_root.exists():
        raise SystemExit(_schema_err("deployments/", "directory not found"))

    merged_app_index: Dict[str, Tuple[str, str]] = {}
    merged_app_cfg_envs: Dict[str, Dict] = {}
    host_topology_map: Dict[Tuple[str, str], Dict] = {}

    for dc_dir in deploy_root.iterdir():
        if not dc_dir.is_dir():
            continue

        dc_id = dc_dir.name
        dep_file = dc_dir / "deployments.yaml"
        hosts_file = dc_dir / "hosts.yaml"

        if not dep_file.exists() and not hosts_file.exists():
            continue
        if not dep_file.exists():
            raise SystemExit(_schema_err(f"deployments/{dc_id}", "deployments.yaml not found"))
        if not hosts_file.exists():
            raise SystemExit(_schema_err(f"deployments/{dc_id}", "hosts.yaml not found"))

        with hosts_file.open() as f:
            hosts_data = yaml.safe_load(f) or {}
        with dep_file.open() as f:
            dep_data = yaml.safe_load(f) or {}

        validate_hosts_schema(dc_id, hosts_data)

        app_index, app_cfg_envs, topo_map = validate_deployments_schema(
            root, dc_id, dep_data, hosts_data
        )

        for k, v in topo_map.items():
            host_topology_map[k] = v

        for app_name, loc in app_index.items():
            if app_name in merged_app_index and merged_app_index[app_name] != loc:
                prev_dc, prev_host = merged_app_index[app_name]
                dc, host = loc
                raise SystemExit(
                    _schema_err(
                        f"deployments/{dc_id}/deployments.yaml",
                        "application '{app}' defined multiple times: (dc={dc1}, host={h1}) and (dc={dc2}, host={h2}); application names must be globally unique".format(
                            app=app_name,
                            dc1=prev_dc,
                            h1=prev_host,
                            dc2=dc,
                            h2=host,
                        ),
                    )
                )
            merged_app_index[app_name] = loc
            merged_app_cfg_envs[app_name] = app_cfg_envs[app_name]

    _validate_cross_app_refs(root, merged_app_index, merged_app_cfg_envs, host_topology_map)
