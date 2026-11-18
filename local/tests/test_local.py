import copy
import hashlib
import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from images import migrations
from images.releases import canonical
from images.tests.fixtures import fixture_bundle
from lite.runtime import services
from local.entrypoint import configure_database, credentials
from local.postgres import Postgres

ROOT = Path(__file__).resolve().parents[2]


class CredentialTests(unittest.TestCase):
    def test_postgres_does_not_inherit_provider_keys_or_external_database_options(self):
        with patch.dict(os.environ, {"GITHUB_CLIENT_SECRET": "fixture", "PGOPTIONS": "fixture"}):
            pg = Postgres("/unused", identity=pwd.getpwuid(os.getuid()))
        self.assertNotIn("GITHUB_CLIENT_SECRET", pg.environment)
        self.assertNotIn("PGOPTIONS", pg.environment)

    def test_secrets_survive_restart_and_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            first = credentials(directory)
            self.assertEqual(first, credentials(directory))
            self.assertEqual((Path(directory) / "secrets.json").stat().st_mode & 0o777, 0o600)

    def test_missing_keys_next_to_existing_data_never_silently_regenerate(self):
        with tempfile.TemporaryDirectory() as directory:
            pg = Path(directory) / "postgres"
            pg.mkdir()
            (pg / "PG_VERSION").write_text("17")
            with self.assertRaisesRegex(ValueError, "restore"):
                credentials(directory)
            self.assertFalse((Path(directory) / "secrets.json").exists())

    def test_local_scopes_compute_credentials_to_app_and_persists_worker_homes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = credentials(directory) | {"DATABASE_URL": "postgres://runtime@db/app", "SECRETS_SERVER_STORAGE_DATABASE_URL": "postgres://runtime@db/app",
                                                 "COMPUTE_DB_URL": "postgres://admin@db/compute",
                                                "SECRETS_SERVER_SERVICE_IDENTITIES": "fixture"}
            definitions = services(source, state_root=Path(directory) / "services", allow_compute=True)
            for service in definitions:
                self.assertTrue(service.env["HOME"].startswith(directory))
                self.assertEqual("COMPUTE_DB_URL" in service.env, service.name == "app")
            with self.assertRaises(ValueError):
                services(source)


class LauncherTests(unittest.TestCase):
    def test_podman_preference_and_quoted_persistent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for engine in ("podman", "docker"):
                program = root / engine
                program.write_text(f"#!{sys.executable}\nimport json, sys\nif sys.argv[1] == 'run': print(json.dumps(sys.argv))\n")
                program.chmod(0o755)
            data = root / "my local data"
            result = subprocess.run(["bash", str(ROOT / "local/run.sh"), str(data), "--port", "9000", "--pull", "never"],
                                    env=os.environ | {"PATH": str(root) + ":" + os.environ["PATH"], "CONTAINER_ENGINE": ""},
                                    text=True, capture_output=True, check=True)
            args = json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(args[0].endswith("podman"))
            self.assertIn("127.0.0.1:9000:8080", args)
            self.assertIn(str(data.resolve()) + ":/data:Z", args)
            result = subprocess.run(["bash", str(ROOT / "local/run.sh"), str(data), "--engine", "docker", "--pull", "never"],
                                    env=os.environ | {"PATH": str(root) + ":" + os.environ["PATH"]},
                                    text=True, capture_output=True, check=True)
            self.assertTrue(json.loads(result.stdout.splitlines()[-1])[0].endswith("docker"))


@unittest.skipUnless(os.environ.get("LOCAL_POSTGRES_BIN"), "Set LOCAL_POSTGRES_BIN to run the real PostgreSQL lifecycle test")
class DatabaseTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("LOCAL_RELEASE_BUNDLE"), "Set LOCAL_RELEASE_BUNDLE to check resolved real service SQL")
    def test_real_packaged_service_migrations(self):
        bundle = Path(os.environ["LOCAL_RELEASE_BUNDLE"]).resolve()
        manifest = json.loads((bundle / "manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.close()
            pg = Postgres(directory, binaries=os.environ["LOCAL_POSTGRES_BIN"], identity=pwd.getpwuid(os.getuid()), port=port)
            try:
                pg.start()
                def sql(text):
                    return pg.psql(text)
                migrations.apply(sql, manifest, bundle)
                migrations.apply(sql, manifest, bundle)
                self.assertEqual(sql("SELECT count(*) FROM core.infra_migrations").strip(),
                                 str(len(manifest["histories"][0]["migrations"])))
                self.assertEqual(sql("SELECT to_regclass('public.kv_store')::text").strip(), "kv_store")
            finally:
                pg.stop()

    def test_bootstrap_migrations_role_isolation_restart_and_history_refusal(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            root = Path(directory)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.close()
            pg = Postgres(root, binaries=os.environ["LOCAL_POSTGRES_BIN"], identity=pwd.getpwuid(os.getuid()), port=port)
            bundle = root / "bundle"
            manifest = fixture_bundle(bundle)
            values = credentials(root)
            try:
                pg.start()
                configure_database(pg, values)
                sql = lambda text: pg.psql(text, database="typednotes")
                migrations.apply(sql, manifest, bundle)
                migrations.apply(sql, manifest, bundle)
                self.assertEqual(sql("SELECT count(*) FROM core.infra_migrations").strip(), "1")
                initial = copy.deepcopy(manifest)

                def seal():
                    manifest.pop("build_id", None)
                    manifest["build_id"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()

                def append(identifier, body):
                    migration = {key: value.replace("0001_initial", identifier + "_append") if isinstance(value, str) else value
                                 for key, value in manifest["histories"][0]["migrations"][0].items()}
                    migration.update(id=identifier, sha256=hashlib.sha256(body.encode()).hexdigest())
                    (bundle / migration["file"]).write_text(body)
                    manifest["histories"][0]["migrations"].append(migration)
                    seal()

                append("0002", "CREATE TABLE appended_fixture(value text);")
                migrations.apply(sql, manifest, bundle)
                with self.assertRaisesRegex(ValueError, "changed/removed"):
                    migrations.apply(sql, initial, bundle)
                appended = copy.deepcopy(manifest)
                append("0003", "CREATE TABLE rolled_back_fixture(value text); SELECT 1/0;")
                with self.assertRaises(subprocess.CalledProcessError):
                    migrations.apply(sql, manifest, bundle)
                self.assertEqual(sql("SELECT to_regclass('rolled_back_fixture')::text").strip(), "")
                self.assertEqual(sql("SELECT count(*) FROM core.infra_migrations").strip(), "2")
                manifest = appended
                # A publisher changing both SQL and its checksum still cannot
                # rewrite history already applied to this persistent database.
                first = manifest["histories"][0]["migrations"][0]
                original_sql = (bundle / first["file"]).read_text()
                (bundle / first["file"]).write_text(original_sql + " SELECT 1;")
                first["sha256"] = hashlib.sha256((original_sql + " SELECT 1;").encode()).hexdigest()
                seal()
                with self.assertRaisesRegex(ValueError, "changed/removed"):
                    migrations.apply(sql, manifest, bundle)
                (bundle / first["file"]).write_text(original_sql)
                first["sha256"] = hashlib.sha256(original_sql.encode()).hexdigest()
                seal()
                pg.psql("GRANT USAGE ON SCHEMA public TO typednotes_runtime; GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA public TO typednotes_runtime;",
                        database="typednotes")
                command = [str(pg.bin / "psql"), "-X", "-At", "-h", "127.0.0.1", "-p", str(port), "-U", "typednotes_runtime", "-d", "typednotes", "-v", "ON_ERROR_STOP=1"]
                env = os.environ | {"PGPASSWORD": values["POSTGRES_RUNTIME_PASSWORD"]}
                subprocess.run(command, input="INSERT INTO core_fixture VALUES ('persistent');", env=env, text=True, capture_output=True, check=True)
                denied = subprocess.run(command, input="CREATE TABLE forbidden(id int);", env=env, text=True, capture_output=True, check=False)
                self.assertNotEqual(denied.returncode, 0)
                # The compute administrator provisions actor roles/schemas but
                # does not get access to the application's logical database.
                compute = command.copy()
                compute[compute.index("typednotes_runtime")] = "typednotes_compute"
                compute[compute.index("typednotes")] = "compute"
                env_compute = os.environ | {"PGPASSWORD": values["COMPUTE_DB_PASSWORD"]}
                subprocess.run(compute, input="CREATE ROLE actor LOGIN; GRANT actor TO CURRENT_USER; CREATE SCHEMA actor AUTHORIZATION actor; REVOKE actor FROM CURRENT_USER;",
                               env=env_compute, text=True, capture_output=True, check=True)
                compute[compute.index("compute")] = "typednotes"
                denied = subprocess.run(compute, input="SELECT 1;", env=env_compute, text=True, capture_output=True, check=False)
                self.assertNotEqual(denied.returncode, 0)
                pg.stop()
                pg.start()
                self.assertEqual(sql("SELECT value FROM core_fixture").strip(), "persistent")
                migration = manifest["histories"][0]["migrations"][0]
                (bundle / migration["file"]).write_text("ALTERED SQL")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    migrations.apply(sql, manifest, bundle)
            finally:
                pg.stop()
