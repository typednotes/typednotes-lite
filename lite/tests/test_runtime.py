import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from images.smoke import service_environment

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lite_runtime", ROOT / "runtime.py")
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)


def configuration():
    keys = ("DATABASE_URL", "SECRETS_SERVER_STORAGE_DATABASE_URL", "SECRETS_MASTER_KEY", "SECRETS_BOOTSTRAP_PASSWORD",
            "SECRETS_SERVER_SERVICE_IDENTITIES", "LIAISON_ROOT_KEY", "APP_SECRETS_PASSWORD",
            "LIAISON_SECRETS_PASSWORD", "LUN_SECRETS_PASSWORD", "LODE_TOKEN", "LUN_TOKEN", "LUN_ID_SALT")
    return {key: "fixture-" + key for key in keys}


class EnvironmentTests(unittest.TestCase):
    def test_workers_do_not_inherit_privileged_credentials(self):
        source = configuration() | {"SCW_SECRET_KEY": "cloud-secret", "GITHUB_CLIENT_SECRET": "oauth-secret",
                                    "COMPUTE_DB_PASSWORD": "admin-secret", "LODE_MODEL_API_KEY": "direct-secret"}
        definitions = {service.name: service for service in runtime.services(source)}
        for name in ("lode", "lun"):
            env = definitions[name].env
            for key in ("DATABASE_URL", "SECRETS_SERVER_STORAGE_DATABASE_URL", "SECRETS_MASTER_KEY", "LIAISON_ROOT_KEY", "SCW_SECRET_KEY",
                        "GITHUB_CLIENT_SECRET", "COMPUTE_DB_PASSWORD", "LODE_MODEL_API_KEY",
                        "APP_SECRETS_PASSWORD", "LIAISON_SECRETS_PASSWORD"):
                self.assertNotIn(key, env, (name, key))
        self.assertNotIn("SECRETS_MASTER_KEY", definitions["app"].env)
        self.assertNotIn("SECRETS_PASSWORD", definitions["lode"].env)
        self.assertEqual(definitions["lun"].env["SECRETS_PASSWORD"], source["LUN_SECRETS_PASSWORD"])
        self.assertEqual(definitions["app"].env["SECRETS_PASSWORD"], source["APP_SECRETS_PASSWORD"])
        self.assertEqual(definitions["liaison"].env["SECRETS_PASSWORD"], source["LIAISON_SECRETS_PASSWORD"])
        self.assertEqual(definitions["ledger"].env["PORT"], "8081")

    def test_vault_uses_only_its_separate_database(self):
        source = configuration()
        definitions = {service.name: service for service in runtime.services(source)}
        self.assertEqual(definitions["vault"].env["SECRETS_SERVER_STORAGE_DATABASE_URL"],
                         source["SECRETS_SERVER_STORAGE_DATABASE_URL"])
        self.assertNotIn("DATABASE_URL", definitions["vault"].env)
        for name in ("app", "ledger", "liaison"):
            self.assertEqual(definitions[name].env["DATABASE_URL"], source["DATABASE_URL"])
            self.assertNotIn("SECRETS_SERVER_STORAGE_DATABASE_URL", definitions[name].env)
        del source["SECRETS_SERVER_STORAGE_DATABASE_URL"]
        with self.assertRaisesRegex(ValueError, "SECRETS_SERVER_STORAGE_DATABASE_URL"):
            runtime.services(source)

    def test_legacy_vault_blocks_startup_instead_of_bootstrapping_an_empty_vault(self):
        source = configuration() | {"DATABASE_URL": "postgres://runtime:password@database/app?sslmode=require"}
        for output, code in (("t\n", 0), ("", 1)):
            with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "secret")):
                with self.assertRaises(ValueError) as raised:
                    runtime.check_storage(source)
                self.assertNotIn("secret", str(raised.exception))
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "f\n", "")):
            runtime.check_storage(source)

    def test_standalone_services_use_remote_endpoints_and_only_their_own_credentials(self):
        if not shutil.which("lake"):
            self.skipTest("Lean is required to evaluate the actual fleet configuration")
        result = subprocess.run(["lake", "env", "lean", "--run", "scripts/service-config.lean"],
                                cwd=ROOT.parent, text=True, capture_output=True, check=True)
        for name, declared in json.loads(result.stdout).items():
            values = {origin: "fixture-" + origin for origin in declared["secretInputs"].values()}
            source = service_environment(declared, values)
            with self.subTest(service=name):
                definitions = runtime.services(source | {"PORT": "9000", "SCW_SECRET_KEY": "cloud"}, standalone=name)
                self.assertEqual(len(definitions), 1)
                service = definitions[0]
                self.assertEqual(service.name, name)
                self.assertTrue(service.health.startswith("http://127.0.0.1:9000/"))
                self.assertNotIn("SCW_SECRET_KEY", service.env)
                for key, value in declared["env"].items():
                    if key.endswith("_PORT") or key == "TYPEDNOTES_SERVICE":
                        continue
                    self.assertEqual(service.env[key], value, key)
                for key, origin in declared["secretInputs"].items():
                    self.assertEqual(service.env[key], "fixture-" + origin, key)
                core_values = {key: value for key, value in values.items() if not key.startswith(runtime.OAUTH_PREFIXES)}
                runtime.services(service_environment(declared, core_values) | {"PORT": str(declared["port"])}, standalone=name)
                if name in ("liaison", "lun"):
                    self.assertEqual(service.env["SECRETS_HOST"], "vault.example.internal")
                    self.assertEqual(service.env["SECRETS_PORT"], "80")
                    self.assertEqual(service.env["SECRETS_INSECURE"], "1")
                    for missing in ("SECRETS_HOST", "SECRETS_PORT"):
                        with self.assertRaisesRegex(ValueError, missing):
                            runtime.services({k: v for k, v in source.items() if k != missing}, standalone=name)

    def test_worker_preparation_imports_under_the_actual_scrubbed_environment(self):
        service = next(service for service in runtime.services(configuration()) if service.name == "lun")
        self.assertNotIn("PYTHONPATH", service.env)
        with tempfile.TemporaryDirectory() as directory:
            # The helper should reach its filesystem work without needing any
            # application modules. Elan's absent settings file is intentional.
            result = subprocess.run([sys.executable, str(ROOT / "runtime.py"), "--prepare-worker", "lun"],
                                    cwd=directory, env=service.env | {"ELAN_HOME": directory},
                                    text=True, capture_output=True, check=False)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("lun-settings.toml", result.stderr)

    def test_smoke_export_uses_the_selected_bundle_policies_and_front_door_ports(self):
        if not shutil.which("lake"):
            self.skipTest("Lean is required to export the smoke configuration")
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"vaultIdentities": "image-specific-vault-policies"}))
            result = subprocess.run(["lake", "env", "lean", "--run", "scripts/service-config.lean", "docker", str(manifest)],
                                    cwd=ROOT.parent, text=True, capture_output=True, check=True)
        declared = json.loads(result.stdout)
        self.assertEqual(set(declared), set(runtime.DEFINITIONS))
        self.assertEqual(declared["vault"]["env"]["SECRETS_SERVER_SERVICE_IDENTITIES"], "image-specific-vault-policies")
        for name in ("liaison", "lun"):
            self.assertEqual(declared[name]["env"]["SECRETS_HOST"], "vault")
            self.assertEqual(declared[name]["env"]["SECRETS_PORT"], "80")

    def test_app_is_last_and_internal_ports_cannot_collide(self):
        source = configuration() | {"PORT": "9000"}
        definitions = runtime.services(source)
        self.assertEqual(definitions[-1].name, "app")
        self.assertEqual(definitions[-1].env["PORT"], "9000")
        for port in ("8081", "8082", "8083", "8084", "8200", "0", "65536"):
            with self.assertRaises(ValueError):
                runtime.services(source | {"PORT": port})

    def test_missing_configuration_reports_names_not_values(self):
        source = configuration()
        del source["LUN_TOKEN"]
        with self.assertRaisesRegex(ValueError, "LUN_TOKEN") as raised:
            runtime.services(source)
        self.assertNotIn(source["SECRETS_MASTER_KEY"], str(raised.exception))

    def test_serverless_mode_never_silently_accepts_compute_administrator(self):
        with self.assertRaisesRegex(ValueError, "COMPUTE_DB_URL"):
            runtime.services(configuration() | {"COMPUTE_DB_URL": "postgres://admin@compute/db"})


class LocalSupervisor(runtime.Supervisor):
    """Real child processes without container-only Unix users or root privileges."""
    def start(self, service):
        child = subprocess.Popen(service.command, cwd=service.cwd, env=service.env, start_new_session=True)
        self.children.append((service, child))


class SupervisionTests(unittest.TestCase):
    def service(self, name, code, cwd):
        return runtime.Service(name, [sys.executable, "-c", code], {}, "http://unused", cwd=cwd)

    def test_even_a_successful_early_exit_stops_the_whole_container(self):
        with tempfile.TemporaryDirectory() as directory:
            definitions = [self.service("dependency", "import time; time.sleep(60)", directory),
                           self.service("app", "import time; time.sleep(0.1)", directory)]
            supervisor = LocalSupervisor(definitions, shutdown_timeout=1)
            with patch.object(runtime, "healthy", return_value=True):
                self.assertEqual(supervisor.run(), 1)
            self.assertTrue(all(child.poll() is not None for _, child in supervisor.children))

    def test_shutdown_during_startup_is_graceful_and_does_not_start_app(self):
        with tempfile.TemporaryDirectory() as directory:
            definitions = [self.service("dependency", "import time; time.sleep(60)", directory),
                           self.service("app", "import time; time.sleep(60)", directory)]
            supervisor = LocalSupervisor(definitions, shutdown_timeout=1)
            timer = threading.Timer(0.1, supervisor.stop)
            timer.start()
            try:
                with patch.object(runtime, "healthy", return_value=False):
                    self.assertEqual(supervisor.run(), 0)
            finally:
                timer.join()
            self.assertEqual(len(supervisor.children), 1)
            self.assertIsNotNone(supervisor.children[0][1].poll())

    def test_startup_timeout_stops_started_services(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = LocalSupervisor([self.service("vault", "import time; time.sleep(60)", directory)],
                                         startup_timeout=0.01, shutdown_timeout=1)
            started = time.monotonic()
            with patch.object(runtime, "healthy", return_value=False):
                self.assertEqual(supervisor.run(), 1)
            self.assertLess(time.monotonic() - started, 3)
            self.assertIsNotNone(supervisor.children[0][1].poll())


class DeploymentSecretTests(unittest.TestCase):
    def run_check(self, mode, **extra):
        env = {"PATH": os.environ["PATH"], "TYPEDNOTES_DEPLOYMENT": mode}
        for key in ("SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID", "SCW_DEFAULT_ORGANIZATION_ID",
                    "SECRETS_MASTER_KEY", "SECRETS_BOOTSTRAP_PASSWORD", "LIAISON_ROOT_KEY",
                    "APP_SECRETS_PASSWORD", "LIAISON_SECRETS_PASSWORD", "LUN_SECRETS_PASSWORD",
                    "LODE_TOKEN", "LUN_TOKEN", "LUN_ID_SALT", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
                    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITLAB_CLIENT_ID", "GITLAB_CLIENT_SECRET",
                    "DROPBOX_CLIENT_ID", "DROPBOX_CLIENT_SECRET", "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
                    "SLACK_SIGNING_SECRET", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
            env[key] = "x" * 32
        env.update(extra)
        return subprocess.run(["bash", str(ROOT.parent / "scripts/check-deployment-secrets.sh")],
                              env=env, capture_output=True, text=True, check=False)

    def test_lite_does_not_require_compute_or_inspector_secrets(self):
        result = self.run_check("lite")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_still_requires_its_database_and_inspector_secrets(self):
        for mode in ("full", "full-from-lite"):
            result = self.run_check(mode)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("COMPUTE_DB_PASSWORD", result.stderr)
            self.assertIn("DB_INSPECTOR_PASSWORD", result.stderr)
            result = self.run_check(mode, COMPUTE_DB_PASSWORD="Valid-Password1", DB_INSPECTOR_USERNAME="operator",
                                    DB_INSPECTOR_PASSWORD="fixture")
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_rejects_invalid_compute_password_and_lite_rejects_short_tokens(self):
        result = self.run_check("full", COMPUTE_DB_PASSWORD="invalid", DB_INSPECTOR_USERNAME="operator",
                                DB_INSPECTOR_PASSWORD="fixture")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("COMPUTE_DB_PASSWORD", result.stderr)
        result = self.run_check("lite", LUN_TOKEN="short")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LUN_TOKEN", result.stderr)
