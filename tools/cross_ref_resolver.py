from __future__ import annotations

import re
from typing import Dict, Tuple

from deployment_loader import load_datacenter, load_deployment


def resolve_cross_app_placeholders(
    *,
    dc_id: str,
    host_name: str,
    template_text: str,
    template_name: str,
    app_name: str,
    app_index: Dict[str, Tuple[str, str]],
    repo_root,
) -> Dict[str, str]:
    placeholder_pattern = re.compile(r"{{\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*}}")
    ref_pairs = set(placeholder_pattern.findall(template_text))

    cross_refs: Dict[str, str] = {}

    for ref_app, ref_key in ref_pairs:
        mapping = app_index.get(ref_app)
        if mapping is None:
            raise SystemExit(
                f"referenced application '{ref_app}' not found in any deployments.yaml "
                f"(used in template {template_name} for app '{app_name}')"
            )

        ref_dc, ref_host = mapping

        if "shm" in ref_key.lower() and (dc_id != ref_dc or host_name != ref_host):
            raise SystemExit(
                "shared-memory key '{app}.{key}' must be used on the same host "
                "(referencer dc={dc_ref}, host={h_ref}, owner dc={dc_owner}, host={h_owner})".format(
                    app=ref_app,
                    key=ref_key,
                    dc_ref=dc_id,
                    h_ref=host_name,
                    dc_owner=ref_dc,
                    h_owner=ref_host,
                )
            )

        try:
            ref_dep = load_deployment(repo_root, ref_dc, ref_host, ref_app)
        except SystemExit as e:
            raise SystemExit(
                f"failed to load referenced app '{ref_app}' (dc={ref_dc}, host={ref_host}): {e}"
            )

        ref_app_def: Dict = ref_dep.get("app") or {}
        ref_templates = ref_app_def.get("templates")
        if isinstance(ref_templates, list) and ref_templates:
            ref_tmpl0 = ref_templates[0]
            ref_cfg_envs = ref_tmpl0.get("cfg_envs") or {}
        else:
            ref_cfg_envs = ref_app_def.get("cfg_envs") or {}

        if not isinstance(ref_cfg_envs, dict):
            raise SystemExit(
                f"cfg_envs for referenced app '{ref_app}' is not a mapping "
                f"(dc={ref_dc}, host={ref_host})"
            )

        if ref_key not in ref_cfg_envs:
            raise SystemExit(
                f"key '{ref_key}' not found in cfg_envs of referenced app '{ref_app}' "
                f"(dc={ref_dc}, host={ref_host})"
            )

        raw_val = ref_cfg_envs[ref_key]

        if ref_key == "listen_nic" and raw_val is not None:
            ref_host_topo = load_datacenter(repo_root, ref_dc, ref_host)
            ref_nics = ref_host_topo.get("nics", [])
            ref_ip = None
            for nic in ref_nics:
                if nic.get("name") == str(raw_val):
                    ref_ip = nic.get("ip")
                    break
            if ref_ip is None:
                raise SystemExit(
                    f"ip for nic '{raw_val}' not found in hosts.yaml for referenced app "
                    f"'{ref_app}' (dc={ref_dc}, host={ref_host})"
                )
            cross_refs[f"{ref_app}.{ref_key}"] = str(ref_ip)
        else:
            cross_refs[f"{ref_app}.{ref_key}"] = str(raw_val)

    return cross_refs
