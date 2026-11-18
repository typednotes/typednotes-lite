# Building Typednotes Lite and Local

Lite packages the Typednotes app, Ledger, Liaison, Secrets, Lode and Lun into
one application image, backed by separate application and vault PostgreSQL databases. This directory
contains the shared build graph, Lite image assembly, process supervisor and checks.
The [Local image](../local/README.md) extends Lite with PostgreSQL in the same
container and a persistent directory launcher.
See [the deployment guide](https://github.com/typednotes/typednotes-infra/blob/main/docs/lite.md)
in typednotes-infra for CI, configuration, database limitations and recovery.

## What is `docker-bake.hcl`?

[`docker-bake.hcl`](docker-bake.hcl) is the **build recipe that connects the six
service builds to the final Lite image**. It is read by Docker Buildx Bake;
HCL is the configuration language used to describe its build targets.

The file defines:

- **Sources:** each service's Git repository and the immutable commit of its
  newest stable release tag, supplied by the release resolver.
- **Service builds:** each repository builds through its own Dockerfile.
- **Dependencies:** the six build results become named inputs to the `lite`
  target, which assembles the combined image using this directory's Dockerfile.
- **Shared settings:** the native amd64/arm64 platform, GitHub Actions build
  caches, manifest/provenance labels and output image names.

The default group selects **both `lite` and `local`**. Local's `combined` context
is `target:lite`, so it extends the exact same service bundle. Direct local Bake
builds schedule all dependencies without intermediate registry uploads. CI
instead compiles each service on its own runner and passes thin OCI artifacts
by digest to fresh assembly runners, keeping compiler working sets off those
machines. Sources come from remote release commits, not sibling worktrees.

## How the Dockerfiles connect

The final target declares named contexts:

```hcl
target "lite" {
  context = "."
  dockerfile = "lite/Dockerfile"
  contexts = {
    app = "target:app"
    # The other five services are connected in the same way.
  }
}
```

`target:app` means “use the build result of the `app` Bake target.” This allows
[`Dockerfile`](Dockerfile) to copy the already-built application:

```dockerfile
COPY --from=app /usr/local/app /usr/local/app
```

The same mechanism supplies the other service executables, Lean toolchains
and warm package caches. Each service repository owns its compilation steps;
the Lite Dockerfile owns the final filesystem layout and runtime dependencies.

## Build locally

Run these commands **from the repository root**, with Python 3, Git and Docker
Buildx installed. Resolve the newest service releases and inspect the build graph
without building images:

```sh
scripts/build-images.sh --print
```

Build all six services and load both final images into a running Docker daemon:

```sh
scripts/build-images.sh
```

The wrapper disables GitHub Actions caches outside CI. Override `LITE_IMAGE`,
`LOCAL_IMAGE` or `PLATFORM` to choose output names or architecture. Building does
not start the services or deploy cloud resources. Podman can run the published
OCI images directly; Bake is only a dependency of source builds.

The resolver writes `.images/manifest.json`, packaged SQL and `.images/bake.json`.
Include that generated Bake file when invoking Bake directly. It freezes all
six newest stable release tags to their peeled commits and records the exact
migrations belonging to them. There are no stale source-ref defaults. See
[the shared build guide](../images/README.md) for schema alignment and rebuilds.

## Build, runtime and deployment responsibilities

- **`docker-bake.hcl`** coordinates the source builds and their dependencies.
- **`Dockerfile`** assembles Lite; `local/Dockerfile` adds PostgreSQL.
- **[`runtime.py`](runtime.py)** supervises the six processes in Lite, or one
  selected service (`TYPEDNOTES_SERVICE`) per container after promotion.
- **The fleet topology** — Lite and promoted full-from-lite, the bundle
  snapshot and the migration conversion — is declared in the
  [typednotes-infra repository](https://github.com/typednotes/typednotes-infra)
  (`Fleet/Shared.lean`, `Fleet/Lite.lean`, `Fleet/Promotion.lean`,
  `Fleet/Bundle.lean`, `Fleet/Topology.lean`).
- **[`storage.py`](https://github.com/typednotes/typednotes-infra/blob/main/lite/storage.py)**
  (in typednotes-infra) copies and verifies a legacy single-DB vault before
  archiving the original table and migration history; the infra repository
  operates the running deployment.
- **[`images.yml`](../.github/workflows/images.yml)** checks service tags every
  fifteen minutes, compiles each service on its own native runner, then
  assembles/tests both variants from digest-pinned artifacts on fresh amd64
  and arm64 runners before publishing.
- **Deployment** stays manual in the
  [typednotes-infra repository](https://github.com/typednotes/typednotes-infra):
  its Apply workflow deploys the exact published Lite digest with the SQL
  identified by its build manifest.

Bake is a build-time tool; it does not run inside the deployed container.
For more background, see [Docker's documentation on linking Bake targets](https://docs.docker.com/build/bake/contexts/).
