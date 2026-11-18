"""Pass PostgreSQL URI credentials through libpq environment, never argv."""
import os
from urllib.parse import parse_qsl, unquote, urlsplit


def environment(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.path.startswith("/"):
        raise ValueError("Expected a PostgreSQL connection URI")
    env = {"PATH": os.environ.get("PATH", ""), "PGDATABASE": unquote(parsed.path[1:]), "PGCONNECT_TIMEOUT": "10"}
    for name, value in (("PGHOST", parsed.hostname), ("PGPORT", parsed.port),
                        ("PGUSER", parsed.username), ("PGPASSWORD", parsed.password)):
        if value is not None:
            env[name] = unquote(str(value))
    parameters = {"host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD",
                  "dbname": "PGDATABASE", "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
                  "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY", "options": "PGOPTIONS",
                  "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME"}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key not in parameters:
            raise ValueError("Unsupported PostgreSQL connection parameter")
        env[parameters[key]] = value
    return env
