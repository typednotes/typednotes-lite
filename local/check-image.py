#!/usr/bin/env python3
"""Verify one self-contained Local image across complete container recreation."""
import json
import secrets
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images.smoke import VAULT_CHECK, WORKER_CHECK, docker, wait_for


def main():
    image = sys.argv[1]
    name = "typednotes-local-smoke-" + secrets.token_hex(4)
    volume = name + "-data"
    try:
        docker("volume", "create", volume)

        def start():
            docker("run", "--detach", "--name", name, "--publish", "127.0.0.1::8080", "--volume", volume + ":/data", image)
            return "http://" + docker("port", name, "8080/tcp")

        address = start()

        def ready():
            if not json.loads(docker("inspect", name))[0]["State"]["Running"]:
                raise RuntimeError("Local image exited during startup")
            try:
                with urlopen(address + "/api/health", timeout=5) as response:
                    health = json.load(response)
                    return all(health.get(key) is True for key in
                               ("database", "schema", "ledger", "vault", "liaison", "computations", "lode", "lun", "compute"))
            except (OSError, URLError):
                return False

        def sql(text):
            return docker("exec", "--user", "postgres", "-i", name, "/usr/lib/postgresql/17/bin/psql", "-X", "-q", "-At",
                          "-h", "/data/run", "-U", "postgres", "-d", "typednotes", "-v", "ON_ERROR_STOP=1", data=text)

        wait_for(ready, "Local app, PostgreSQL and all services")
        conflict = name + "-conflict"
        try:
            docker("run", "--detach", "--name", conflict, "--volume", volume + ":/data", image)
            code = int(docker("wait", conflict, timeout=30))
            output = docker("logs", conflict, check=False)
            if code == 0:
                raise RuntimeError(f"A second Local instance must refuse the same data directory\n{output}")
            if "already using" not in output:
                raise RuntimeError(f"Second instance failed without the expected exclusive-directory refusal (exit {code})\n{output}")
        finally:
            docker("rm", "--force", conflict, check=False)
        docker("exec", name, "python3", "/usr/local/lib/typednotes/local/entrypoint.py", "--healthcheck")
        docker("exec", "-i", name, "python3", "-", data=WORKER_CHECK)
        docker("exec", "--env", "SMOKE_WRITE=1", "-i", name, "python3", "-", data=VAULT_CHECK)
        sql("CREATE TABLE local_smoke(value text); INSERT INTO local_smoke VALUES ('persistent');")
        original_secrets = docker("exec", name, "sha256sum", "/data/secrets.json")
        # Recreate, rather than merely restart, to prove that /data contains
        # every durable database, vault-key and worker-state dependency.
        docker("stop", "--time", "30", name)
        docker("rm", name)
        address = start()
        wait_for(ready, "Local recovery from persistent directory")
        if sql("SELECT value FROM local_smoke;") != "persistent":
            raise RuntimeError("Database data did not survive container recreation")
        if docker("exec", name, "sha256sum", "/data/secrets.json") != original_secrets:
            raise RuntimeError("Local credentials changed across container recreation")
        docker("exec", "-i", name, "python3", "-", data=VAULT_CHECK)
        docker("exec", "-i", name, "python3", "-", data='''
import os, signal
from pathlib import Path
os.kill(int(Path("/data/postgres/postmaster.pid").read_text().splitlines()[0]), signal.SIGKILL)
''')
        if int(docker("wait", name, timeout=30)) == 0:
            raise RuntimeError("Local must fail when its database exits")
        print("ok: self-contained PostgreSQL, all services, exclusive directory lock, compute configuration, durable data/keys and database failure propagation")
    except Exception:
        print(docker("logs", name, check=False), file=sys.stderr)
        raise
    finally:
        docker("rm", "--force", name, check=False)
        docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    main()
