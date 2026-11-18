"""Apply packaged SQL with append-only histories compatible with Infra."""
import hashlib
import json
from pathlib import Path

from images.releases import validate


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def apply(psql, manifest, root, *, schemas=None):
    """`psql(sql)` returns tuples-only stdout and raises on SQL failure.

    Local startup holds an exclusive data-directory lock; the image smoke test
    owns a disposable database. Cloud reconciliation uses Infra's own locking.
    """
    validate(manifest)
    pending = []
    for history in manifest["histories"]:
        schema = history["schema"]
        if schemas is not None and schema not in schemas:
            continue
        table = f'"{schema}".infra_migrations'
        psql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"; CREATE TABLE IF NOT EXISTS {table} '
             '(id text PRIMARY KEY, sql text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());')
        rows = json.loads(psql(f"SELECT coalesce(json_agg(h), '[]'::json) FROM (SELECT id, sql FROM {table} ORDER BY id) h;").strip())
        applied = {row["id"]: row["sql"] for row in rows}
        declared = {}
        for migration in history["migrations"]:
            data = (Path(root) / migration["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != migration["sha256"]:
                raise ValueError(f"Packaged SQL checksum mismatch: {migration['file']}")
            sql = data.decode("utf-8")
            declared[migration["id"]] = sql
        for key, sql in applied.items():
            if declared.get(key) != sql:
                raise ValueError(f"Refusing changed/removed applied migration: {schema}/{key}")
        for key, sql in declared.items():
            if key not in applied:
                if applied and key <= max(applied):
                    raise ValueError(f"Refusing non-append migration: {schema}/{key}")
                pending.append((schema, key, sql))
    # Check every history before applying any new service SQL.
    for schema, key, sql in pending:
        psql("BEGIN;\n" + sql + f'\nINSERT INTO "{schema}".infra_migrations(id, sql) VALUES '
             f"({literal(key)}, {literal(sql)});\nCOMMIT;")
