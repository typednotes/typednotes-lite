# Typednotes Local

Local is the self-contained counterpart of [Lite](../lite/README.md): the same
six application services **plus PostgreSQL in the same image**. A dedicated host
directory holds the database, vault key, service credentials and worker state.

## Launch

Install Podman or Docker, then run from the repository root:

```sh
local/run.sh ./typednotes-data
```

Open **http://localhost:8080**. The script pulls
`ghcr.io/typednotes/typednotes-local:latest`, prefers Podman when installed,
and falls back to Docker when Podman is absent. Both amd64 and arm64 images
are published; the engine selects its native architecture.

On macOS, Podman requires a running Linux VM. For a new installation:

```sh
podman machine init --cpus 4 --memory 8192
podman machine start
```

If a machine already exists, start that machine instead of initializing another.
The 8 GiB setting is a practical starting allocation for notebook compilation,
not a measured minimum. Container execution with Podman does not need Docker
Desktop. Image builds in CI use Docker Buildx/Bake.

Useful launcher options:

```sh
local/run.sh ./typednotes-data --engine podman --port 9000
local/run.sh ./typednotes-data --engine docker
local/run.sh ./typednotes-data --env-file ./local.env
local/run.sh ./typednotes-data --image typednotes-local:dev --pull never
```

`CONTAINER_ENGINE` and `TYPEDNOTES_LOCAL_IMAGE` also select the engine and image.
Pulling defaults to `always`; use `--pull missing` or `--pull never` for offline
or locally built images. Stop the foreground process with Ctrl-C. The container
is removed, and the directory is retained for the next launch. The directory
must be dedicated to this instance; only one instance can use it at a time.

Only `127.0.0.1:PORT` is published. PostgreSQL and the supporting HTTP services
are internal to the container. The `:Z` volume label gives the dedicated mount
the appropriate private SELinux label on hosts that enforce SELinux.

## Sign-in and integrations

Internal service credentials are generated automatically. Human sign-in still
uses the application's existing OAuth providers; Local does not bypass login.
Supply a GitHub or Google OAuth client registered for the local callback URL:

```dotenv
GITHUB_CLIENT_ID=your-client-id
GITHUB_CLIENT_SECRET=your-client-secret
```

For the default port, the GitHub callback is
`http://localhost:8080/auth/github/callback`. Google uses
`http://localhost:8080/auth/google/callback`. Update provider registrations when
changing the public port. A `config.env` file inside the data directory is read
automatically, or use `--env-file`. The launcher sets `PUBLIC_URL` to the chosen
localhost port. See the root README for the other provider variables.

## Persistent data and startup

The mounted directory contains:

- `postgres/`: PostgreSQL 17 data, including the app/vault database `typednotes`
  and the logical `compute` database on the **same PostgreSQL server**.
- `secrets.json`: generated vault master key, service tokens/passwords and
  database passwords; private permissions and stable across restarts.
- `services/`: private service homes, Lode workspaces and Lun builds/caches.
- `run/` and `.lock`: private PostgreSQL socket and exclusive instance lock.

Startup initializes an empty cluster, provisions runtime/compute roles, applies
the SQL packaged with this exact image, then starts the six application
processes. The app/vault runtime has DML-only access. Only the app receives the
compute provisioning credential. Unlike Lite's Serverless SQL database, this
PostgreSQL server can create actor roles, so built-in PostgreSQL notebook
connectors and `db` sinks can be provisioned.

Migrations use the same `schema.infra_migrations` table shape as Infra. Applied
SQL is compared exactly on every startup; changed/removed history, SQL checksum
drift and downgrades that remove applied migrations are refused. New migrations
are applied transactionally before serving. Images carry all SQL they need;
startup does not download migrations or contact a cloud provider.

The supervisor checks PostgreSQL as well as the six service processes. A required
process exiting fails the container. Graceful shutdown stops the application
processes before PostgreSQL. PostgreSQL major-version mismatches are refused;
a future image changing the server major version requires a database migration.

Back up the entire directory with the instance stopped, preserving ownership and
permissions. In particular, preserve `secrets.json`: an existing database with
a missing key file is refused rather than silently creating a new vault key.
Rootless Podman maps container users into host user namespaces, so some files
can have subordinate host UIDs; use engine-aware backup tooling as appropriate.

## Builds and updates

Both Local and Lite resolve the **newest stable `vMAJOR.MINOR.PATCH` tag of each
service** for every new build, then freeze its peeled Git commit and migrations
in one build manifest. No manually maintained service-version list is needed.

The [Combined images workflow](../.github/workflows/images.yml) checks for new
service tags every fifteen minutes and rebuilds **both images** when a service
commit/version or the packaging revision changes. It also supports manual runs,
packaging pushes and `repository_dispatch` notifications. Native amd64/arm64
builds and both smoke tests must succeed before the multi-platform `latest` tags
are promoted. See [the shared image build guide](../images/README.md).

To build locally with a Docker Buildx builder, from the repository root:

```sh
scripts/build-images.sh
# Resolve newest releases and inspect the build graph without running a builder:
scripts/build-images.sh --print
```

The default build platform is the machine's native amd64/arm64 architecture.
Use `PLATFORM=linux/amd64` or `PLATFORM=linux/arm64` to select it explicitly.
For Podman-only use, pull the published image with the launcher; Bake itself is
a Docker Buildx tool, not a Podman runtime dependency.

After publication of a new image, stop the current instance and launch against
the same directory. Pulling `latest` does not modify a running container.
