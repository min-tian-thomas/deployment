from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from binary_resolver import load_binary_target  # noqa: E402
from comment_refresher import refresh_deployment_comments_for_dc  # noqa: E402
from config_renderer import render_validate_and_inject  # noqa: E402
from cpu_topology import build_cpu_numa_map_from_host, parse_cpu_set  # noqa: E402
from cross_ref_resolver import resolve_cross_app_placeholders  # noqa: E402
from schema_validation import validate_all_schemas  # noqa: E402
from app_validator import validate_app_cpu_allocation, validate_host_cpu_sets  # noqa: E402
from app_emitter import create_or_replace_exec_symlink, write_rendered_config  # noqa: E402
from app_indexer import build_global_app_index  # noqa: E402
from template_context import build_template_replacements  # noqa: E402


class TestCoreModules(unittest.TestCase):
    def test_parse_cpu_set(self) -> None:
        self.assertEqual(parse_cpu_set(""), set())
        self.assertEqual(parse_cpu_set("0"), {0})
        self.assertEqual(parse_cpu_set("0,2,4"), {0, 2, 4})
        self.assertEqual(parse_cpu_set("1-3"), {1, 2, 3})
        self.assertEqual(parse_cpu_set("1-3,5"), {1, 2, 3, 5})

    def test_build_cpu_numa_map_from_host_list(self) -> None:
        host = {
            "cpus": 4,
            "numa_nodes": [
                {"id": 0, "cpus": "0-1"},
                {"id": 1, "cpus": "2-3"},
            ],
        }
        self.assertEqual(build_cpu_numa_map_from_host(host), {0: 0, 1: 0, 2: 1, 3: 1})

    def test_binary_resolver_tag_to_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deployments").mkdir(parents=True)
            (root / "install").mkdir(parents=True)

            (root / "deployments" / "required_binaries.yaml").write_text(
                """
md_server:
  tags:
    prod: v1
  required_versions:
    - v1
""".lstrip()
            )

            bin_path = load_binary_target(root, "md_server", "prod")
            self.assertTrue(bin_path.exists())
            self.assertIn("install/binaries/md_server/v1", str(bin_path))

    def test_schema_validation_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deployments" / "idc_test" / "templates").mkdir(parents=True)
            (root / "install").mkdir(parents=True)

            (root / "deployments" / "required_binaries.yaml").write_text(
                """
md_server:
  tags:
    prod: v1
  required_versions:
    - v1
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "hosts.yaml").write_text(
                """
host01:
  cpus: 2
  isolated_cpus: 1
  shared_cpus: 0
  nics:
    - name: eth0
      ip: 127.0.0.1
      type: ethernet
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "templates" / "app.json").write_text(
                "{}\n"
            )

            (root / "deployments" / "idc_test" / "deployments.yaml").write_text(
                """
host01:
  log_dir: /tmp/logs
  app:
    binary: md_server
    tag: prod
    templates:
      - name: app.json
        cfg_envs:
          log_cpu: 0
          main_loop_cpu: 1
          admin_loop_cpu: 0
""".lstrip()
            )

            validate_all_schemas(root)

    def test_schema_validation_cross_app_missing_app(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deployments" / "idc_test" / "templates").mkdir(parents=True)
            (root / "install").mkdir(parents=True)

            (root / "deployments" / "required_binaries.yaml").write_text(
                """
md_server:
  tags:
    prod: v1
  required_versions:
    - v1
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "hosts.yaml").write_text(
                """
host01:
  cpus: 2
  isolated_cpus: 1
  shared_cpus: 0
  nics:
    - name: eth0
      ip: 127.0.0.1
      type: ethernet
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "templates" / "app.json").write_text(
                '{"x": "{{missing.listen_port}}"}\n'
            )

            (root / "deployments" / "idc_test" / "deployments.yaml").write_text(
                """
host01:
  app:
    binary: md_server
    tag: prod
    templates:
      - name: app.json
        cfg_envs:
          log_cpu: 0
          main_loop_cpu: 1
          admin_loop_cpu: 0
""".lstrip()
            )

            with self.assertRaises(SystemExit):
                validate_all_schemas(root)

    def test_schema_validation_shm_cross_host_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deployments" / "idc_test" / "templates").mkdir(parents=True)
            (root / "install").mkdir(parents=True)

            (root / "deployments" / "required_binaries.yaml").write_text(
                """
md_server:
  tags:
    prod: v1
  required_versions:
    - v1
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "hosts.yaml").write_text(
                """
host01:
  cpus: 2
  isolated_cpus: 1
  shared_cpus: 0
  nics:
    - name: eth0
      ip: 127.0.0.1
      type: ethernet
host02:
  cpus: 2
  isolated_cpus: 1
  shared_cpus: 0
  nics:
    - name: eth0
      ip: 127.0.0.2
      type: ethernet
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "templates" / "a.json").write_text("{}\n")
            (root / "deployments" / "idc_test" / "templates" / "b.json").write_text(
                '{"shm": "{{a.some_shm_path}}"}\n'
            )

            (root / "deployments" / "idc_test" / "deployments.yaml").write_text(
                """
host01:
  a:
    binary: md_server
    tag: prod
    templates:
      - name: a.json
        cfg_envs:
          log_cpu: 0
          main_loop_cpu: 1
          admin_loop_cpu: 0
          some_shm_path: /dev/shm/a
host02:
  b:
    binary: md_server
    tag: prod
    templates:
      - name: b.json
        cfg_envs:
          log_cpu: 0
          main_loop_cpu: 1
          admin_loop_cpu: 0
""".lstrip()
            )

            with self.assertRaises(SystemExit):
                validate_all_schemas(root)

    def test_cross_ref_resolver_listen_nic_maps_to_ip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deployments" / "idc_test").mkdir(parents=True)

            (root / "deployments" / "idc_test" / "hosts.yaml").write_text(
                """
host01:
  cpus: 2
  isolated_cpus: 1
  shared_cpus: 0
  nics:
    - name: sf0
      ip: 10.0.0.1
      type: solarflare
""".lstrip()
            )

            (root / "deployments" / "idc_test" / "deployments.yaml").write_text(
                """
host01:
  pub:
    binary: md_server
    tag: prod
    templates:
      - name: pub.json
        cfg_envs:
          log_cpu: 0
          main_loop_cpu: 1
          admin_loop_cpu: 0
          listen_nic: sf0
          listen_port: 123
""".lstrip()
            )

            refs = resolve_cross_app_placeholders(
                dc_id="idc_test",
                host_name="host01",
                template_text='{"x": "{{pub.listen_nic}}"}',
                template_name="rec.json",
                app_name="rec",
                app_index={"pub": ("idc_test", "host01")},
                repo_root=root,
            )
            self.assertEqual(refs["pub.listen_nic"], "10.0.0.1")

    def test_comment_refresher_preserves_log_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dep_file = root / "deployments.yaml"

            hosts_data = {
                "host01": {
                    "nics": [{"name": "sf0", "ip": "10.0.0.1"}],
                    "numa_nodes": [{"id": 0, "cpus": "0-1"}],
                }
            }

            dep_data = {
                "host01": {
                    "log_dir": "/tmp/logs",
                    "app": {
                        "binary": "md_server",
                        "tag": "prod",
                        "templates": [
                            {
                                "name": "app.json",
                                "cfg_envs": {
                                    "log_cpu": 0,
                                    "main_loop_cpu": 1,
                                    "admin_loop_cpu": 0,
                                    "listen_nic": "sf0",
                                },
                            }
                        ],
                    },
                }
            }

            refresh_deployment_comments_for_dc(
                "idc_test",
                hosts_data,
                dep_data,
                dep_file,
                build_cpu_numa_map_from_host,
            )
            self.assertIn("log_dir: /tmp/logs", dep_file.read_text(encoding="utf-8"))

    def test_renderer_injects_log_dir(self) -> None:
        rendered = render_validate_and_inject(
            template_text='{"logging": {"log_level": "Info", "log_cpu": "{{log_cpu}}"}, "event_loops": [{"name": "admin_loop", "cpu_id": "{{admin_loop_cpu}}", "busy_spin": false}] }',
            replacements={"log_cpu": 0, "admin_loop_cpu": 0},
            app_name="app",
            template_name="t.json",
            host_log_dir="/tmp/logs",
            total_cpus=2,
            isolated_cpus={1},
            admin_loop_cpu=0,
            dc_id="idc_test",
            host_name="host01",
            busy_usage={},
        )
        self.assertIn('"log_dir": "/tmp/logs/app"', rendered)

    def test_renderer_rejects_admin_loop_busy_spin_true(self) -> None:
        with self.assertRaises(SystemExit):
            render_validate_and_inject(
                template_text='{"logging": {"log_level": "Info", "log_cpu": "{{log_cpu}}"}, "event_loops": [{"name": "admin_loop", "cpu_id": "{{admin_loop_cpu}}", "busy_spin": true}] }',
                replacements={"log_cpu": 0, "admin_loop_cpu": 0},
                app_name="app",
                template_name="t.json",
                host_log_dir=None,
                total_cpus=2,
                isolated_cpus={1},
                admin_loop_cpu=0,
                dc_id="idc_test",
                host_name="host01",
                busy_usage={},
            )

    def test_app_validator_rejects_host_cpu_overlap(self) -> None:
        with self.assertRaises(SystemExit):
            validate_host_cpu_sets(
                total_cpus=4,
                isolated_cpus={1, 2},
                shared_cpus={2, 3},
                host_name="host01",
            )

    def test_app_validator_busy_spin_cpu_reuse_fails(self) -> None:
        busy_usage = {2: "app_a"}
        with self.assertRaises(SystemExit):
            validate_app_cpu_allocation(
                dc_id="idc_test",
                host_name="host01",
                app_name="app_b",
                total_cpus=8,
                isolated_cpus={2, 3, 4},
                shared_cpus={0, 1},
                log_cpu=0,
                main_loop_cpu=2,
                admin_loop_cpu=1,
                busy_usage=busy_usage,
            )

    def test_app_emitter_writes_config_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apps_root = root / "app"
            bin_target = root / "bin" / "x"
            bin_target.parent.mkdir(parents=True, exist_ok=True)
            bin_target.write_text("ok\n")

            cfg_path = write_rendered_config(
                apps_root=apps_root,
                template_name="cfg.json",
                rendered="{}\n",
            )
            self.assertTrue(cfg_path.exists())

            exec_path, rel_target = create_or_replace_exec_symlink(
                apps_root=apps_root,
                app_name="app",
                bin_target=bin_target,
            )
            self.assertTrue(exec_path.is_symlink())
            self.assertEqual(str(exec_path.readlink()), rel_target)

    def test_app_indexer_detects_duplicate_app_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deployments_root = root / "deployments"
            (deployments_root / "dc1").mkdir(parents=True)
            (deployments_root / "dc2").mkdir(parents=True)

            (deployments_root / "dc1" / "deployments.yaml").write_text(
                """
host01:
  app:
    binary: md_server
    tag: prod
    templates: []
""".lstrip()
            )
            (deployments_root / "dc2" / "deployments.yaml").write_text(
                """
host02:
  app:
    binary: md_server
    tag: prod
    templates: []
""".lstrip()
            )

            with self.assertRaises(SystemExit):
                build_global_app_index(deployments_root=deployments_root)

    def test_template_context_requires_listen_nic_when_used(self) -> None:
        template_text = '{"listen_nic": "{{listen_nic}}"}'
        env = {"listen_port": 1}
        host = {"nics": [{"name": "sf0", "ip": "10.0.0.1"}]}

        with self.assertRaises(SystemExit):
            build_template_replacements(
                template_text=template_text,
                env=env,
                host=host,
                log_cpu=0,
                main_loop_cpu=1,
                admin_loop_cpu=0,
            )

    def test_template_context_maps_listen_nic_to_ip(self) -> None:
        template_text = '{"listen_nic": "{{listen_nic}}"}'
        env = {"listen_nic": "sf0", "listen_port": 1}
        host = {"nics": [{"name": "sf0", "ip": "10.0.0.1"}]}
        rep = build_template_replacements(
            template_text=template_text,
            env=env,
            host=host,
            log_cpu=0,
            main_loop_cpu=1,
            admin_loop_cpu=0,
        )
        self.assertEqual(rep["listen_nic"], "10.0.0.1")

    def test_template_context_does_not_require_listen_nic_when_unused(self) -> None:
        template_text = '{"x": 1}'
        env = {"listen_port": 1}
        host = {"nics": []}
        rep = build_template_replacements(
            template_text=template_text,
            env=env,
            host=host,
            log_cpu=0,
            main_loop_cpu=1,
            admin_loop_cpu=0,
        )
        self.assertIn("listen_nic", rep)
