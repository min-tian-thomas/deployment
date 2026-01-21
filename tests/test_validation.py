from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from schema_validation import validate_all_schemas  # noqa: E402
from gen_config import parse_cpu_set  # noqa: E402


class TestValidation(unittest.TestCase):
    def test_parse_cpu_set(self) -> None:
        self.assertEqual(parse_cpu_set(""), set())
        self.assertEqual(parse_cpu_set("0"), {0})
        self.assertEqual(parse_cpu_set("0,2,4"), {0, 2, 4})
        self.assertEqual(parse_cpu_set("1-3"), {1, 2, 3})
        self.assertEqual(parse_cpu_set("1-3,5"), {1, 2, 3, 5})

    def test_validate_all_schemas_ok(self) -> None:
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
  numa_nodes:
    - id: 0
      cpus: 0-1
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

    def test_validate_cross_app_ref_missing_app(self) -> None:
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
                "{\"x\": \"{{missing.listen_port}}\"}\n"
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


if __name__ == "__main__":
    unittest.main()
