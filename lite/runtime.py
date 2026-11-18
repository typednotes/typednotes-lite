#!/usr/bin/env python3
"""Supervise the six Lite services; schemas are applied by Infra, never here."""

import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


@dataclass
class Service:
    name: str
    command: list[str]
    env: dict[str, str]
    health: str
    cwd: str = "/usr/local/app"
    token: str | None = None


OAUTH_PREFIXES = ("GITHUB_CLIENT_", "GOOGLE_CLIENT_", "GITLAB_CLIENT_", "DROPBOX_CLIENT_",
                  "SLACK_", "WHATSAPP_", "MICROSOFT_CLIENT_")
VAULT_CLIENT = ("SECRETS_HOST", "SECRETS_PORT", "SECRETS_INSECURE", "SECRETS_USERNAME", "SECRETS_PASSWORD")


@dataclass(frozen=True)
class ServiceDefinition:
    command: str
    port: int
    health_path: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    token: str | None = None
    credentials: tuple[tuple[str, str], ...] = ()


# One service contract for combined and standalone supervision. Only topology
# supplies different addresses and the mapping from bundle credentials.
DEFINITIONS = {
    "vault": ServiceDefinition("/usr/local/bin/secrets-server", 8200, "/v1/sys/health",
        ("SECRETS_SERVER_STORAGE_DATABASE_URL", "SECRETS_MASTER_KEY", "SECRETS_SERVER_BOOTSTRAP_PASSWORD",
         "SECRETS_SERVER_SERVICE_IDENTITIES", "APP_SECRETS_PASSWORD", "LIAISON_SECRETS_PASSWORD", "LUN_SECRETS_PASSWORD"),
        ("SECRETS_MASTER_KEY_RETIRED",), credentials=(("SECRETS_SERVER_BOOTSTRAP_PASSWORD", "SECRETS_BOOTSTRAP_PASSWORD"),)),
    "ledger": ServiceDefinition("/usr/local/bin/ledger", 8081, "/health", ("DATABASE_URL",),
        ("LEDGER_SWEEP_INTERVAL_SECONDS",)),
    "liaison": ServiceDefinition("/usr/local/bin/liaison", 8082, "/_health",
        ("DATABASE_URL", "LIAISON_ROOT_KEY", *VAULT_CLIENT), prefixes=OAUTH_PREFIXES,
        credentials=(("SECRETS_PASSWORD", "LIAISON_SECRETS_PASSWORD"),)),
    "lun": ServiceDefinition("/usr/local/bin/lun", 8083, "/_health",
        ("LUN_TOKEN", "LUN_ID_SALT", "LUN_LIAISON_URL", *VAULT_CLIENT),
        ("LUN_WORKERS", "LUN_BUILD_TIMEOUT", "LUN_FETCH_TIMEOUT", "LUN_CALL_TIMEOUT"), token="LUN_TOKEN",
        credentials=(("SECRETS_PASSWORD", "LUN_SECRETS_PASSWORD"),)),
    "lode": ServiceDefinition("/usr/local/bin/lode", 8084, "/_health",
        ("LODE_TOKEN", "LODE_LUN_TOKEN", "LODE_LUN_URL", "LODE_LIAISON_URL"),
        ("LODE_MAX_STEPS", "LODE_MODEL_TIMEOUT", "LODE_GIT_TIMEOUT", "LODE_CHECK_TIMEOUT", "LODE_LUN_BUILD_TIMEOUT", "LODE_LUN_CALL_TIMEOUT"),
        token="LODE_TOKEN", credentials=(("LODE_LUN_TOKEN", "LUN_TOKEN"),)),
    "app": ServiceDefinition("/usr/local/app/server", 8080, "/api/health",
        ("DATABASE_URL", "LIAISON_ROOT_KEY", "LODE_TOKEN", "LUN_TOKEN", "SECRETS_URL", "SECRETS_PASSWORD", "LIAISON_URL", "LODE_URL", "LUN_URL"),
        ("PUBLIC_URL", "SECRETS_USERNAME"), OAUTH_PREFIXES + ("TYPEDNOTES_",),
        credentials=(("SECRETS_PASSWORD", "APP_SECRETS_PASSWORD"),)),
}


def loopback_configuration(name, source):
    config = dict(source)
    for target, origin in DEFINITIONS[name].credentials:
        if not source.get(origin):
            raise ValueError("Missing configuration: " + origin)
        config[target] = source[origin]
    def url(service):
        return f"http://127.0.0.1:{DEFINITIONS[service].port}"
    if name in ("liaison", "lun"):
        config.update(SECRETS_HOST="127.0.0.1", SECRETS_PORT="8200", SECRETS_INSECURE="1", SECRETS_USERNAME=name)
    if name == "app":
        config.update(SECRETS_URL=url("vault"), SECRETS_USERNAME="typednotes-app",
                      LIAISON_URL=url("liaison"), LODE_URL=url("lode"), LUN_URL=url("lun"))
    if name in ("lode", "lun"):
        config[name.upper() + "_LIAISON_URL"] = url("liaison")
    if name == "lode":
        config["LODE_LUN_URL"] = url("lun")
    return config


def services(source, *, state_root=None, allow_compute=False, standalone=None):
    """Construct only selected services, with explicit topology and allowlists.
    Worker preparation stays stdlib-only, including under a scrubbed environment.
    """
    if standalone is not None and standalone not in DEFINITIONS:
        raise ValueError("Unknown standalone service")
    if source.get("COMPUTE_DB_URL") and not (allow_compute or standalone == "app"):
        raise ValueError("Lite uses Serverless SQL; COMPUTE_DB_URL must be unset")
    public_port = int(source.get("PORT", "8080"))
    internal_ports = {definition.port for name, definition in DEFINITIONS.items() if name != "app"}
    if not 1 <= public_port <= 65535 or (standalone is None and public_port in internal_ports):
        raise ValueError("PORT must be valid and distinct from Lite's internal ports")
    result = []
    for name in ([standalone] if standalone else DEFINITIONS):
        definition = DEFINITIONS[name]
        config = source if standalone else loopback_configuration(name, source)
        missing = [key for key in definition.required if not config.get(key)]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        keys = definition.required + definition.optional + ("PATH", "SSL_CERT_FILE", "SSL_CERT_DIR")
        env = {key: value for key, value in config.items()
               if key in keys or any(key.startswith(prefix) for prefix in definition.prefixes)}
        home = str(Path(state_root or "/var/lib") / name)
        env.update(HOME=home, LANG="C.UTF-8")
        port = public_port if standalone or name == "app" else definition.port
        if name == "vault":
            env.update(SECRETS_SERVER_LISTEN_ADDR=f"{'0.0.0.0' if standalone else '127.0.0.1'}:{port}",
                       SECRETS_SERVER_STORAGE_MIGRATE="false", SECRETS_SERVER_BOOTSTRAP_USERNAME="admin")
        elif name in ("app", "ledger"):
            env["PORT"] = str(port)
            if name == "app":
                env["IP"] = "0.0.0.0"
                if source.get("COMPUTE_DB_URL"):
                    env["COMPUTE_DB_URL"] = source["COMPUTE_DB_URL"]
        else:
            env[name.upper() + "_PORT"] = str(port)
        if name in ("lode", "lun"):
            env.update(ELAN_HOME=home + "/elan")
            env[name.upper() + "_WORKDIR"] = home + "/work"
            env[name.upper() + "_PACKAGE_CACHE"] = f"/opt/{name}/cache"
        if name == "lode":
            env.update(LODE_LINEN_REV=source.get("LODE_LINEN_REF", "v1.12.0"),
                       LODE_TOOLCHAIN=source.get("LODE_TOOLCHAIN", "leanprover/lean4:v4.34.0"))
        result.append(Service(name, [definition.command], env, f"http://127.0.0.1:{port}{definition.health_path}",
                              token=source.get(definition.token) if definition.token else None))
    return result


def healthy(service):
    headers = {"Authorization": f"Bearer {service.token}"} if service.token else {}
    try:
        # Loopback checks must not follow deployment HTTP proxy settings.
        with build_opener(ProxyHandler({})).open(Request(service.health, headers=headers), timeout=2) as reply:
            return reply.status == 200
    except (OSError, URLError):
        return False


def check_storage(source):
    """A legacy vault must be copied before any new vault can bootstrap.
    Local uses its own bootstrap and deliberately retains one logical DB.
    """
    # Worker preparation re-executes this module without PYTHONPATH. It does
    # not need database helpers, so keep this dependency on the DB-only path.
    from images.database import environment as database_environment
    result = subprocess.run(
        ["psql", "-X", "-w", "-At", "-v", "ON_ERROR_STOP=1", "-c",
         "SELECT to_regclass('public.kv_store') IS NOT NULL;"],
        env=database_environment(source["DATABASE_URL"]),
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError("Could not verify application/vault database separation")
    if result.stdout.strip() != "f":
        raise ValueError("Legacy vault still exists in the application database; follow docs/lite.md's vault-split procedure")


def prepare_home(service):
    identity = pwd.getpwnam(service.name)
    home = Path(service.env["HOME"])
    home.parent.mkdir(mode=0o711, parents=True, exist_ok=True)
    home.mkdir(mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    os.chown(home, identity.pw_uid, identity.pw_gid)
    return identity


def prepare_worker(name):
    # Run AFTER dropping privileges. Persistent worker files must never direct
    # privileged filesystem writes through user-created symlinks on restart.
    tools = Path(os.environ["ELAN_HOME"]) / "toolchains"
    tools.mkdir(parents=True, exist_ok=True)
    for entry in tools.iterdir():
        if entry.is_symlink() and not entry.exists():
            entry.unlink()
    settings = tools.parent / "settings.toml"
    if settings.is_symlink():
        settings.unlink()
    shutil.copyfile(f"/opt/typednotes/{name}-settings.toml", settings)
    for toolchain in Path("/opt/elan/toolchains").iterdir():
        target = tools / toolchain.name
        if not target.exists():
            target.symlink_to(toolchain, target_is_directory=True)


class Supervisor:
    def __init__(self, definitions, startup_timeout=180, shutdown_timeout=10):
        self.definitions = definitions
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.children = []
        self.stopping = threading.Event()

    def stop(self, *_):
        self.stopping.set()

    def check_children(self):
        for service, child in self.children:
            code = child.poll()
            if code is not None:
                raise RuntimeError(f"{service.name} exited ({code})")

    def start(self, service):
        identity = prepare_home(service)
        if service.name in ("lode", "lun"):
            subprocess.run([sys.executable, __file__, "--prepare-worker", service.name],
                           env=service.env, user=identity.pw_uid, group=identity.pw_gid,
                           extra_groups=[], umask=0o077, check=True)
        child = subprocess.Popen(service.command, cwd=service.cwd, env=service.env,
                                 user=identity.pw_uid, group=identity.pw_gid,
                                 extra_groups=[], umask=0o077, start_new_session=True)
        self.children.append((service, child))
        print(f"lite: started {service.name}", flush=True)

    def shutdown(self):
        # Signal whole groups, including compiler and generated-code children.
        for _, child in reversed(self.children):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + self.shutdown_timeout
        while time.monotonic() < deadline:
            if all(child.poll() is not None for _, child in self.children):
                break
            time.sleep(0.1)
        # Kill any remaining group members even if their parent already exited.
        for _, child in reversed(self.children):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()

    def run(self):
        try:
            for service in self.definitions:
                if self.stopping.is_set():
                    return 0
                self.start(service)
                deadline = time.monotonic() + self.startup_timeout
                while not healthy(service):
                    self.check_children()
                    if self.stopping.wait(0.2):
                        return 0
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"{service.name} did not become healthy")
            print("lite: all services ready", flush=True)
            while not self.stopping.wait(0.5):
                self.check_children()
            return 0
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            # Do not stringify exceptions that could include environment values.
            print(f"lite: {error}" if isinstance(error, RuntimeError) else "lite: could not start service", file=sys.stderr)
            return 1
        finally:
            self.shutdown()


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--prepare-worker" and sys.argv[2] in ("lode", "lun"):
            prepare_worker(sys.argv[2])
            return 0
        arguments = sys.argv[1:]
        standalone = os.environ.get("TYPEDNOTES_SERVICE")
        definitions = services(os.environ, standalone=standalone)
        if arguments == ["--healthcheck"]:
            return 0 if all(healthy(service) for service in definitions) else 1
        if arguments:
            raise ValueError("Usage: runtime.py [--healthcheck]")
        if standalone in (None, "app"):
            check_storage(os.environ)
        supervisor = Supervisor(definitions, startup_timeout=int(os.environ.get("LITE_STARTUP_TIMEOUT", "180")))
        signal.signal(signal.SIGTERM, supervisor.stop)
        signal.signal(signal.SIGINT, supervisor.stop)
        return supervisor.run()
    except ValueError as error:
        print(f"lite: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
