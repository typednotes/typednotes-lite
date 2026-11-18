# typednotes-lite

This repository builds and publishes the combined Typednotes images from the
newest stable release tags of the six service repositories
([app](https://github.com/typednotes/typednotes),
[ledger](https://github.com/typednotes/ledger),
[liaison](https://github.com/typednotes/liaison),
[secrets](https://github.com/typednotes/secrets),
[lode](https://github.com/typednotes/lode) and
[lun](https://github.com/typednotes/lun)):

- **`ghcr.io/typednotes/typednotes-lite`** — the six application services in
  one image, deployed by [typednotes-infra](https://github.com/typednotes/typednotes-infra)
  to one Scaleway Serverless Container and two Serverless SQL databases.
- **`ghcr.io/typednotes/typednotes-local`** — the exact same service bundle
  plus PostgreSQL 17 and persistent directory bootstrap, for self-hosted use
  via [`local/run.sh`](local/run.sh).

Deployment, plan/apply/teardown and the fleet declaration live in the
[typednotes-infra repository](https://github.com/typednotes/typednotes-infra);
this repository only builds, verifies and publishes images.

## How it builds

The [Combined images workflow](.github/workflows/images.yml):

- checks all six service repositories for new stable tags every fifteen
  minutes, on pushes to the packaging files, on `service-version-updated`
  repository dispatches, and on manual runs (with `force` to rebuild even
  unchanged versions),
- verifies the packaging against the typednotes-infra fleet declaration
  (`infra_ref`, default `main`) — see
  [Fleet verification](images/README.md#fleet-verification),
- compiles one service per fresh native amd64/arm64 runner, assembles both
  variants from digest-pinned artifacts, smoke-tests them, and only then
  promotes the multi-platform `latest` and `build-<BUILD_ID>` tags.

Build details, the manifest/metadata boundary and failure recovery are in
[images/README.md](images/README.md); the build graph and runtime layout are
documented in [lite/README.md](lite/README.md) and
[local/README.md](local/README.md).

## Building locally

From the repository root, with Python 3, Git and Docker Buildx installed:

```sh
scripts/build-images.sh --print   # resolve newest tags, inspect the graph only
scripts/build-images.sh           # resolve, build and load both images
```

Run the offline test suites with:

```sh
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s local/tests -v
python3 -m unittest discover -s lite/tests -v   # needs a typednotes-infra checkout for the Lean steps
```

## Required setup

- **`INFRA_TOKEN` repository secret**: a fine-grained token with Contents read
  permission for the private `typednotes/typednotes-infra` repository, used by
  the verify job's fleet-declaration checkout.
- **GHCR package access**: the `ghcr.io/typednotes/typednotes-lite` and
  `typednotes-local` packages were created by typednotes-infra. Grant this
  repository write access to them in each package's *Manage Actions access*
  settings (or the first publish will fail).
- Both packages must stay **public** for anonymous pulls by users, Scaleway and
  typednotes-infra's live-plan registry reads.
