"""Private, persistent PostgreSQL owned by the Local supervisor."""
import os
import pwd
import signal
import subprocess
import time
from pathlib import Path


class Postgres:
    def __init__(self, directory, binaries="/usr/lib/postgresql/17/bin", identity=None, port=5432):
        self.root = Path(directory)
        self.data = self.root / "postgres"
        self.socket = self.root / "run"
        self.bin = Path(binaries)
        self.identity = identity or pwd.getpwnam("postgres")
        self.environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.identity.pw_dir, "LANG": "C"}
        self.port = port
        self.child = None

    def command(self, name, *args, **kwargs):
        return subprocess.run([str(self.bin / name), *args], check=True, text=True,
                              capture_output=True, user=self.identity.pw_uid, group=self.identity.pw_gid,
                              extra_groups=[] if os.geteuid() == 0 else None, env=self.environment,
                              cwd=str(self.root), **kwargs).stdout

    def start(self):
        for directory in (self.data, self.socket):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            os.chown(directory, self.identity.pw_uid, self.identity.pw_gid)
        if not (self.data / "PG_VERSION").exists():
            if any(self.data.iterdir()):
                raise ValueError("PostgreSQL directory is nonempty but has no PG_VERSION; refusing to reinitialize it")
            self.command("initdb", "-D", str(self.data), "-U", self.identity.pw_name,
                         "--auth-local=peer", "--auth-host=scram-sha-256", "--no-locale", "--encoding=UTF8")
        version = self.command("postgres", "--version").split()[2].split(".")[0]
        if (self.data / "PG_VERSION").read_text().strip() != version:
            raise ValueError("PostgreSQL major version mismatch; migrate the directory before using this image")
        self.child = subprocess.Popen([str(self.bin / "postgres"), "-D", str(self.data), "-k", str(self.socket),
                                       "-h", "127.0.0.1", "-p", str(self.port)], user=self.identity.pw_uid,
                                      group=self.identity.pw_gid, cwd=str(self.root), start_new_session=True,
                                      extra_groups=[] if os.geteuid() == 0 else None, env=self.environment)
        for _ in range(120):
            if self.child.poll() is not None:
                raise RuntimeError("PostgreSQL exited during startup")
            try:
                self.psql("SELECT 1")
                return
            except subprocess.CalledProcessError:
                time.sleep(0.25)
        raise RuntimeError("PostgreSQL startup timeout")

    def psql(self, sql, database="postgres"):
        return self.command("psql", "--no-psqlrc", "--tuples-only", "--no-align", "--quiet",
                            "--set", "ON_ERROR_STOP=1", "--host", str(self.socket), "--port", str(self.port),
                            "--username", self.identity.pw_name, "--dbname", database, input=sql)

    def stop(self):
        if self.child is None:
            return
        if self.child.poll() is None:
            try:
                self.command("pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "-t", "15", "stop")
            finally:
                if self.child.poll() is None:
                    try:
                        self.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.child.pid, signal.SIGKILL)
        self.child.wait()
