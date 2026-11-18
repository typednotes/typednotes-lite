"""Shared container smoke helpers; never contact deployed cloud services."""
import subprocess
import time


def docker(*args, data=None, timeout=60, check=True, env=None):
    # Non-TTY `docker logs` preserves the container's stdout/stderr streams.
    # Runtime refusals are printed on stderr and must reach smoke assertions
    # and failure reports. Keep stderr separate for JSON/SQL CLI commands.
    logs = args[:1] == ("logs",) or args[:2] == ("container", "logs")
    result = subprocess.run(["docker", *args], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if logs else subprocess.PIPE,
                            text=True, timeout=timeout, env=env, check=False)
    if check and result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr or result.stdout}")
    return result.stdout.strip()


def wait_for(check, description, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {description}")


def service_environment(declared, values):
    """Bind fixture values using the fleet's actual secret-input mapping.
    Optional integrations stay absent; runtime contracts reject missing core values.
    """
    return declared["env"] | {key: values[origin] for key, origin in declared["secretInputs"].items() if origin in values}


WORKER_CHECK = '''
import json, os, pathlib, pwd, subprocess
from lite.runtime import services
if "TYPEDNOTES_DATA_DIR" in os.environ:
    from local.entrypoint import environment
    root = pathlib.Path(os.environ["TYPEDNOTES_DATA_DIR"])
    manifest = json.loads(pathlib.Path("/usr/local/share/typednotes/manifest.json").read_text())
    definitions = environment(root, manifest, json.loads((root / "secrets.json").read_text()))
else:
    definitions = services(os.environ, standalone=os.environ.get("TYPEDNOTES_SERVICE"))
for service in definitions:
    if service.name not in ("lode", "lun"):
        continue
    identity = pwd.getpwnam(service.name)
    subprocess.run(["lake", "--version"], cwd=service.env["HOME"], env=service.env,
                   user=identity.pw_uid, group=identity.pw_gid, check=True)
    script = pathlib.Path(service.env["HOME"]) / "Smoke.lean"
    script.write_text('def main : IO Unit := IO.println "local-runtime-ok"\\n')
    subprocess.run(["lean", "--run", str(script)], cwd=service.env["HOME"], env=service.env,
                   user=identity.pw_uid, group=identity.pw_gid, check=True)
'''

VAULT_CHECK = '''
import json, os
from pathlib import Path
from urllib.request import Request, urlopen
def request(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    port = os.environ["PORT"] if os.environ.get("TYPEDNOTES_SERVICE") == "vault" else "8200"
    with urlopen(Request("http://127.0.0.1:" + port + "/v1/" + path,
                         data=None if body is None else json.dumps(body).encode(), headers=headers)) as response:
        return json.loads(response.read() or b"{}")
password = os.environ.get("SECRETS_BOOTSTRAP_PASSWORD") or os.environ.get("SECRETS_SERVER_BOOTSTRAP_PASSWORD")
if not password:
    password = json.loads((Path(os.environ["TYPEDNOTES_DATA_DIR"]) / "secrets.json").read_text())["SECRETS_BOOTSTRAP_PASSWORD"]
token = request("auth/userpass/login", {"username": "admin", "password": password})["auth"]["client_token"]
if os.environ.get("SMOKE_WRITE"):
    request("secret/data/local-smoke", {"data": {"value": "survives-restart"}}, token)
else:
    assert request("secret/data/local-smoke", token=token)["data"]["data"]["value"] == "survives-restart"
'''
