"""Small source archives for exercising the real release/SQL boundary offline."""
import contextlib
import io
import json

from images.releases import SERVICES, resolve

SHA = "a" * 40


def fixture_bundle(directory, *, empty_sql=False):
    def download(repository, commit):
        name = next(name for name, spec in SERVICES.items() if spec[0] == repository)
        schema, path = SERVICES[name][1:]
        if schema:
            return {path + "/0001_initial.sql": b"" if empty_sql else f"CREATE TABLE {schema}_fixture(value text);".encode()}
        lock = {"packages": [{"name": "linen", "url": "https://github.com/typednotes/linen", "rev": "b" * 40}]}
        return {"lake-manifest.json": json.dumps(lock).encode(), "lean-toolchain": b"leanprover/lean4:v4.34.0\n"}
    with contextlib.redirect_stdout(io.StringIO()):
        return resolve(directory, "fixture", listing=lambda _: f"{SHA}\trefs/tags/v1.2.3\n", download=download)
