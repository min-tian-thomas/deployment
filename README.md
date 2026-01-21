# Deployment Tool MVP

This repository contains a **local deployment description** and a small tool to
render application configuration for a single data center / host.

Current scope is intentionally minimal and follows `.agent/actions_01.md` and
`.agent/design_01.md`.

## Directory layout

```text
deployment/
  apps/
    dce_md_publisher.yaml       # application abstraction (binary + tag + template)

  binaries/
    md_server.yaml              # binary tags (prod/staging,...)

  datacenters/
    datacenters.yaml            # DC + host CPU topology description

  deploy/
    idc_shanghai/
      deployments.yaml          # per-host, per-app deployment plan
      schedules.yaml            # per-app trading sessions (structure only, not yet used)
      templates/
        dce_md_publisher.json   # JSON template with {{...}} placeholders

  tools/
    gen_config.py               # MVP config generator / validator

  Makefile                      # helper targets (uv/venv/config)
  requirements.txt              # Python dependencies for tools/
```

## YAML schemas (current MVP)

### apps/dce_md_publisher.yaml

```yaml
dce_md_publisher:
  binary: md_server
  tag: prod
  config_template: dce_md_publisher.json
```

- `binary`: name of the binary described in `binaries/md_server.yaml`.
- `tag`: which tag to use (`prod` / `staging` / `latest` etc.).
- `config_template`: file name under `deploy/<dc>/templates/`.

### datacenters/datacenters.yaml

Only one DC/host is used in the MVP:

```yaml
datacenters:
  - id: idc_shanghai
    hosts:
      - name: host01
        cpus: 16
        numa_nodes: 2
        log_cpus: 0,1
        isolated_cpus: 2-15
        nics:
          - name: eth0
            type: ethernet
          - name: sf0
            type: solarflare
```

### deploy/idc_shanghai/deployments.yaml

Canonical schema (current MVP): top-level **host → apps** mapping

```yaml
host01:
  dce_md_publisher:
    isolated_cpus: 2
    cfg_envs:
      listen_nic: eth0
      listen_port: 8080
      log_cpu: 0
      main_loop_cpu: 2
      admin_loop_cpu: 1
```

- Top-level keys are host names (e.g. `host01`).
- Under each host, keys are app names (e.g. `dce_md_publisher`).
- `cfg_envs` can be either a single mapping (as above) or a list of
  mappings; the tool currently uses only the first entry when it is a list.

### deploy/idc_shanghai/schedules.yaml

```yaml
defaults:
  timezone: Asia/Shanghai

schedules:
  dce_md_publisher:
    rules:
      - sessions:
          - ["09:00", "15:10"] # Day Session
          - ["21:00", "04:10"] # Night Session (to the next day)
        days: "mon-fri"
```

- Captures trading sessions per app.
- **Not yet consumed** by `gen_config.py`; reserved for later scheduling logic.

### deploy/idc_shanghai/templates/dce_md_publisher.json

This is a JSON file with `{{...}}` placeholders, for example:

```json
{
  "logging": {
    "log_level": "Info",
    "log_cpu": "{{log_cpu}}"
  },
  "event_loops": [
    {
      "name": "main_loop",
      "cpu_id": "{{main_loop_cpu}}",
      "busy_spin": true
    },
    {
      "name": "admin_loop",
      "cpu_id": "{{admin_loop_cpu}}",
      "busy_spin": false
    }
  ],
  "listen_nic": "{{listen_nic}}",
  "listen_port": "{{listen_port}}"
}
```

The placeholders are replaced by `tools/gen_config.py` using string
replacement, and the result is validated as JSON.

## tools/gen_config.py

`gen_config.py` currently does the following for each `dc/host/app` it finds
in the YAML files:

1. Load host topology from `datacenters/datacenters.yaml`.
2. Load deployment definition from `deploy/<dc>/deployments.yaml`.
3. Load app definition from `apps/<app>.yaml` to find the template name.
4. Validate CPU layout:
   - `main_loop_cpu` must be in `isolated_cpus`.
   - `log_cpu` must be in `shared_cpus`.
   - All used CPUs must be within `[0, cpus)`.
   - `log_cpu`, `main_loop_cpu`, `admin_loop_cpu` must be distinct.
5. Compute a simple `cpu -> numa_node` mapping and print it for the used CPUs.
6. Render the JSON template by replacing `{{...}}` with concrete values.
7. Validate the rendered JSON and write it to:

   ```text
   deploy/<dc>/<app>_<host>.json
   ```

## Makefile usage

This repo already contains a Makefile with some helper targets based on `uv`.
For this deployment tool MVP you mainly need:

```bash
# 1) (optional) refresh requirements.txt from requirements.in
make requirements

# 2) create venv and install dependencies from requirements.txt
make venv

# 3) (optional) prepare mock binaries layout from binaries/*.yaml
make binaries

# 4) generate configs for all known dc/host/app
make config
```

`make binaries` will call `tools/gen_config.py binaries` and, for each
`binaries/<name>.yaml`, create mock binaries under:

```text
binaries/<name>/<version>/<name>
```

`make config` will run `tools/gen_config.py` without arguments, which scans
all datacenters / hosts / apps described in the YAML files and prints
CPU/NUMA mappings plus the paths of the generated JSON configs and
application directories under `deploy/<dc>/applications/<host>/<app>/`.

## Notes / Next steps

- `schedules.yaml` is currently only structural; the tool does not yet generate
  cron/systemd snippets based on it.
- Only one app (`dce_md_publisher`) and one host (`host01`) are supported in the
  current script. Extending to multiple apps/hosts should mainly involve:
  iterating over `deployments[host]` and calling the same render logic per app.
