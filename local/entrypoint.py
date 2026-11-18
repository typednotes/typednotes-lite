#!/usr/bin/env python3
"""Bootstrap PostgreSQL and stable credentials, then run all Local services."""
import fcntl
import json
import os
import secrets
import signal
import sys
import tempfile
from pathlib import Path

from images import migrations
from lite.runtime import Supervisor, healthy, services
from local.postgres import Postgres

SECRET_KEYS = ("SECRETS_MASTER_KEY", "SECRETS_BOOTSTRAP_PASSWORD", "LIAISON_ROOT_KEY",
               "APP_SECRETS_PASSWORD", "LIAISON_SECRETS_PASSWORD", "LUN_SECRETS_PASSWORD",
               "LODE_TOKEN", "LUN_TOKEN", "LUN_ID_SALT", "POSTGRES_RUNTIME_PASSWORD", "COMPUTE_DB_PASSWORD")


def credentials(directory):
    path = Path(directory) / "secrets.json"
    if not path.exists():
        # A missing master key beside an existing cluster is not a fresh install.
        if (Path(directory) / "postgres" / "PG_VERSION").exists():
            raise ValueError("Missing secrets.json for an existing database; restore the original file")
        values = {key: secrets.token_hex(32) for key in SECRET_KEYS}
        fd, name = tempfile.mkstemp(prefix=".secrets-", dir=directory)
        temporary = Path(name)
        with os.fdopen(fd, "w") as stream:
            json.dump(values, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    os.chmod(path, 0o600)
    values = json.loads(path.read_text())
    if set(values) != set(SECRET_KEYS) or any(not isinstance(value, str) or len(value) != 64 or
                                             any(c not in "0123456789abcdef" for c in value) for value in values.values()):
        raise ValueError("Invalid persisted Local credentials; restore secrets.json")
    return values


def configure_database(pg, values):
    for role, key, attributes in (("typednotes_runtime", "POSTGRES_RUNTIME_PASSWORD", "NOCREATEROLE"),
                                   ("typednotes_compute", "COMPUTE_DB_PASSWORD", "CREATEROLE NOINHERIT")):
        exists = pg.psql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}'").strip() == "1"
        if not exists:
            pg.psql(f'CREATE ROLE "{role}" LOGIN {attributes}')
        pg.psql(f'ALTER ROLE "{role}" PASSWORD {migrations.literal(values[key])}')
    for database, owner in (("typednotes", pg.identity.pw_name), ("compute", "typednotes_compute")):
        if pg.psql(f"SELECT count(*) FROM pg_database WHERE datname='{database}'").strip() == "0":
            pg.psql(f'CREATE DATABASE "{database}" OWNER "{owner}"')
    pg.psql("REVOKE CONNECT ON DATABASE typednotes FROM PUBLIC; GRANT CONNECT ON DATABASE typednotes TO typednotes_runtime;")
    pg.psql("REVOKE ALL ON SCHEMA public FROM PUBLIC;", database="compute")


def environment(root, manifest, values):
    source = os.environ | values
    source.update(DATABASE_URL=f"postgres://typednotes_runtime:{values['POSTGRES_RUNTIME_PASSWORD']}@127.0.0.1:5432/typednotes",
                  SECRETS_SERVER_STORAGE_DATABASE_URL=f"postgres://typednotes_runtime:{values['POSTGRES_RUNTIME_PASSWORD']}@127.0.0.1:5432/typednotes",
                  COMPUTE_DB_URL=f"postgres://typednotes_compute:{values['COMPUTE_DB_PASSWORD']}@127.0.0.1:5432/compute",
                  SECRETS_SERVER_SERVICE_IDENTITIES=manifest["vaultIdentities"],
                  PUBLIC_URL=os.environ.get("PUBLIC_URL", "http://localhost:8080"))
    return services(source, state_root=root / "services", allow_compute=True)


class LocalSupervisor(Supervisor):
    def __init__(self, definitions, postgres):
        super().__init__(definitions)
        self.postgres = postgres

    def check_children(self):
        super().check_children()
        if self.postgres.child.poll() is not None:
            raise RuntimeError("PostgreSQL exited")


def main():
    root = Path(os.environ.get("TYPEDNOTES_DATA_DIR", "/data"))
    manifest_root = Path("/usr/local/share/typednotes")
    manifest = json.loads((manifest_root / "manifest.json").read_text())
    if sys.argv[1:] == ["--healthcheck"]:
        definitions = environment(root, manifest, credentials(root))
        return 0 if all(healthy(service) for service in definitions) else 1
    root.mkdir(parents=True, exist_ok=True)
    # Service UIDs can traverse the dedicated mount, but cannot list it or read
    # secrets.json/PGDATA. Each private service home has its own Unix owner.
    os.chmod(root, 0o711)
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another Local instance is already using this directory") from error
        values = credentials(root)
        pg = Postgres(root)
        supervisor = None

        def stop(*_):
            if supervisor is not None:
                supervisor.stop()
            else:
                raise InterruptedError("Local startup interrupted")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            pg.start()
            configure_database(pg, values)
            migrations.apply(lambda sql: pg.psql(sql, database="typednotes"), manifest, manifest_root)
            pg.psql("REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO typednotes_runtime; "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO typednotes_runtime; "
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO typednotes_runtime;", database="typednotes")
            supervisor = LocalSupervisor(environment(root, manifest, values), pg)
            return supervisor.run()
        finally:
            # Stop application processes before shutting down their database.
            pg.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"local: {error}", file=sys.stderr)
        sys.exit(1)
