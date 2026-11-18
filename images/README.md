# Combined image builds

The [Bake graph](../lite/docker-bake.hcl) builds six service repositories once
per architecture and assembles two variants:

- **Lite:** six application services, with separate application/vault PostgreSQL databases provisioned by Infra.
- **Local:** the exact Lite service bundle plus PostgreSQL 17 and persistent
  directory bootstrap. See [Local usage](../local/README.md).

## Newest service releases

`python3 -m images.releases --output .images` lists tags from all six repositories
and selects the greatest numeric stable `vMAJOR.MINOR.PATCH` for each. Prereleases,
aliases such as `latest`, and branch heads are excluded. Annotated tags are peeled
to the commit; lightweight tags already identify their commit. All versions are
resolved **once per build**, so both variants use the same source snapshot even
if another tag is published while they compile.
Tag discovery uses GitHub's paginated HTTPS API, independent of workstation Git
URL rewrites or SSH agents. CI supplies `GH_TOKEN`; set it locally too if making
enough resolver calls to reach GitHub's anonymous API limit.

The resolver reads each selected source archive without extracting untrusted
paths. It discovers the service's complete numbered SQL history, captures SQL
checksums, and records each worker's actual Lean/Linen dependencies. Outputs:

- `.images/manifest.json`: versions, immutable commits, migration paths/checksums,
  worker dependencies, vault service policies and a content-derived build ID.
- `.images/migrations/`: the SQL belonging to those commits.
- `.images/bake.json`: Bake overrides supplying peeled source commits, build
  arguments and identical manifest/provenance labels for both output images.

These generated files are ignored by Git and included selectively in the build
context. The manifest is packaged in each image and recorded in its OCI config
label `io.typednotes.build-manifest`.
New builds declare `runtimeVersion: 2`: split-vault configuration and standalone
service supervision. Cloud reconciliation refuses older bundles that would
silently ignore the separate vault URL or launch all services after promotion.

The policy snapshot `images/vault-identities.json` is generated from the owning
Lean declaration (`scripts/lite-config.lean` exports it). CI checks exact
correspondence before publishing; update the snapshot when changing those rules.

```sh
lake env lean --run scripts/lite-config.lean | jq '{vaultIdentities}' > images/vault-identities.json
```

## Build commands

From the repository root:

```sh
scripts/build-images.sh          # resolve newest tags, build and load both images
scripts/build-images.sh --print  # resolve and inspect only; no builder needed
```

The wrapper disables GitHub Actions cache exporters outside CI. It selects the
native platform by default; override `PLATFORM`, `LITE_IMAGE` or `LOCAL_IMAGE` as
needed. To reuse an already resolved snapshot directly:

```sh
docker buildx bake -f lite/docker-bake.hcl -f .images/bake.json --print
```

Bare `docker-bake.hcl` has no source-ref defaults: it cannot silently fall back to
an old release or `main`. `lite/check-build.py` verifies that every source build,
both images' manifest labels and the selected platform match the snapshot.

## Automatic rebuilds

`.github/workflows/images.yml` supports:

- A scheduled tag check every fifteen minutes. GitHub schedules are best-effort,
  so this is an interval, not a maximum propagation latency.
- Pushes to the packaging/build files on `main`.
- A manual run, with `force` to refresh base images even for unchanged services.
- `repository_dispatch` with event type `service-version-updated`.
- A reusable workflow call from other repositories.

Scheduled detection needs no token in the service repositories and covers all
six services. It lists only tags when nothing changes. A cache of successfully
published snapshots avoids repeated source downloads/builds. Cache eviction
merely causes another build; image digests are always read from the registry.

## Fleet verification

The fleet declaration, Lean project, service configuration scripts and policy
snapshot are owned by the private
[typednotes-infra repository](https://github.com/typednotes/typednotes-infra).
Before publishing, the workflow's verify job checks it out at `infra_ref`
(default `main`, overridable on manual runs), overlays this revision's
`images/`, `lite/` and `local/` packaging over it, and runs the full Lean fleet
checks plus the test suites against that declaration — so a published image is
always verified against the live fleet.

Because typednotes-infra is private, the checkout needs an `INFRA_TOKEN`
repository secret: a fine-grained token with Contents read permission for
`typednotes/typednotes-infra` (or remove the secret and make that repository
public). The check compares `images/vault-identities.json` here against the
declaration exported by infra's `scripts/lite-config.lean`; when infra changes
vault policies, regenerate and commit the snapshot from an infra checkout:

```sh
lake env lean --run scripts/lite-config.lean | jq '{vaultIdentities}' > images/vault-identities.json
```

For immediate notification, a release workflow/webhook can POST a repository
dispatch to this repository. The caller needs a GitHub App token or fine-grained
token with Contents write permission for `typednotes/typednotes-lite`; a service
repository's own `GITHUB_TOKEN` does not grant cross-repository access. Example:

```sh
gh api --method POST repos/typednotes/typednotes-lite/dispatches \
  -f event_type=service-version-updated
```

No sender is required for scheduled detection. Dispatch payloads are not used as
source refs or commands: the receiver independently resolves all newest tags.

Each rebuild uses native amd64 and arm64 runners. Lean checks and policy
correspondence run on a separate verification VM. A service/architecture matrix
then compiles **one service per fresh runner**, with at most four compiler jobs
in parallel. `images/artifact.Dockerfile` flattens only the binary, toolchain and
package-cache inputs needed by assembly into scratch OCI images. These are
internal `source-*` tags in the existing Lite package, not extra service packages
or updates to the services' own published tags.

Fresh assembly runners import all six artifacts **by digest**. Their receipts
must match the source commit, bundle ID, workflow run and architecture. The
assembly graph is checked to contain only Lite and Local, so it cannot silently
recompile all six services on the same VM. Each assembly runner builds both
variants, tests Lite against two logical databases in disposable PostgreSQL,
expands it into six separate service containers using the same image and stored
vault data (with compute enabled), and tests Local
through complete container recreation with its persisted database/key material.
Only after both architectures pass are multi-platform manifests published as
`ghcr.io/typednotes/typednotes-{lite,local}:latest` and `:build-BUILD_ID`.
The reusable workflow returns the exact image digests. Publication needs
`packages:write`, and both GHCR packages must be made public once for anonymous
pulls by users and Scaleway. Publication never applies cloud infrastructure.

The standalone publisher alone advances `latest`; reusable Apply calls consume
their own run-specific immutable digest. Their concurrency slots are separate
so a new scheduled check cannot cancel a queued manual Apply. Run-specific build
tags also prevent overlapping publishers from mixing architectures or returning
the other run's output. A cached snapshot's digest is read freshly from its
published build tag, including after a forced base-image refresh.
Successful jobs from an earlier attempt of the same workflow run can be reused
when retrying failed jobs; unrelated runs, changed snapshots and mutable image
tags are refused. Artifact uploads replace their own earlier-attempt receipts.

The verification VM exports the actual full-from-lite service environments and
secret-input bindings with `scripts/service-config.lean`. Assembly smoke tests
consume this artifact. The fixture uses provider-shaped HTTP endpoints on port
80, rather than replacing them with the binaries' internal listener ports.
Service commands, allowlists and required configuration are defined once in
`lite/runtime.py`; standalone supervision constructs only its selected service.
Worker preparation is stdlib-only and is tested with its scrubbed environment.

### Runner disk budget

The first v0.9.0 [Combined images run](https://github.com/typednotes/typednotes-infra/actions/runs/39017091446)
exhausted disk on both architectures. The monolithic compiler jobs accumulated
six source/build trees, multiple Lean installations and a host Lean toolchain;
amd64 failed unpacking Lean and arm64 failed installing the runtime packages.

The isolated source/artifact/assembly stages bound each VM's working set and
avoid transporting base OS layers, Cargo targets and ownership-copyup layers
into assembly. Host Lean is installed only on the verification VM. Image-build
VMs also reclaim the hosted image's unused .NET, Android, GHC, CodeQL and
PowerShell SDK directories, logging disk space before and after. The cleanup
script refuses local and self-hosted machines and never removes workspace or
persistent application data. Its dry-run path is exercised without deleting files.

## Schema alignment during Lite deployment

Newest service tags can include new migrations. Lite plan/apply therefore cannot
keep using the old statically listed schema versions independently of the image.

For a live Lite or full-from-lite plan/apply, `Main.lean` in
[typednotes-infra](https://github.com/typednotes/typednotes-infra) calls `python3 -m images.deployment` to
resolve the image selector, select the Linux amd64 manifest, verify registry
content digests and read the built source manifest. The helper fetches SQL only
from those immutable service commits and checks it against the packaged hashes.
The resulting frozen image and exact SQL are supplied to Infra's four migration
histories. Infra retains its append-only validation, ordering and rollout gate.

Thus SQL and executable versions stay aligned even if service tags or image
`latest` change during reconciliation. A pinned `TYPEDNOTES_LITE_IMAGE` keeps the
same image across CI plan/apply/convergence. Registry metadata and immutable
source hosting remain trusted execution boundaries, alongside the image itself.

Offline `check` uses the declared baseline without networking. Teardown does not
need registry metadata. Full-from-lite requires an explicit Lite digest and runs
that same bundle for all six services. Independent `full` mode retains its explicitly reviewed schema releases.
Local startup instead consumes the SQL packaged inside its image without network
access. Both paths compare applied history rather than replaying changed SQL.

## Verification

```sh
python3 -m unittest discover -s images/tests -v
python3 -m unittest discover -s local/tests -v
python3 -m unittest discover -s lite/tests -v
LOCAL_POSTGRES_BIN=/path/to/postgresql/bin python3 -m unittest discover -s local/tests -v
LOCAL_POSTGRES_BIN=/path/to/postgresql/bin python3 -m unittest discover -s lite/tests -v
```

Set `LOCAL_RELEASE_BUNDLE=.images` as well to apply the real resolved service SQL
in the native PostgreSQL test. After building the images, CI runs:

```sh
python3 lite/check-image.py "$LITE_IMAGE" .images/manifest.json
python3 local/check-image.py "$LOCAL_IMAGE"
```

Assembly receipt validation and both architecture/source-only build graphs are
covered by `images/tests/test_assembly.py`; graph inspection needs the Docker
Buildx CLI, but does not require a running Docker daemon.

### CI failure diagnosis and recovery

In [run 39046870693](https://github.com/typednotes/typednotes-infra/actions/runs/39046870693),
both native assemblies passed Lite's smoke test but failed the Local
exclusive-directory assertion. The Local runtime prints its refusal on stderr;
non-TTY `docker logs` forwards that stream to the CLI's stderr. The smoke helper
was only returning stdout, hiding the correct refusal and failing the test.
It now captures both streams for log commands while keeping stderr separate
for machine-readable commands such as `inspect`. Unexpected second-container
failures include that container's output before cleanup. Regression coverage in
`images/tests/test_smoke.py` exercises real subprocess streams without a Docker
daemon.

More recent jobs are refused before their first step with GitHub's annotation
“The job was not started because recent account payments have failed or your
spending limit needs to be increased.” An organization owner must resolve the
payment issue or set an approved Actions budget in
[Billing settings](https://github.com/organizations/typednotes/settings/billing).
Workflow edits cannot bypass this account-level restriction.

After billing is unblocked and the fix is available on `main`, start fresh
workflows at that revision; rerunning a historical run uses its old code:

```sh
gh workflow run images.yml --repo typednotes/typednotes-lite --ref main
```

Wait for that publisher run to succeed before starting a Lite live plan: `latest` is not
published until both architecture smoke suites pass, and the combined GHCR
packages must be public for Infra/Scaleway's anonymous pulls. An unpublished or
inaccessible image can cause the earlier live Plan's registry 403. Keep that
failure visible rather than skipping registry validation to make CI green.

Once publication and anonymous access are working:

```sh
gh workflow run plan.yml --repo typednotes/typednotes-infra --ref main
```

For a local image smoke run, generate its service fixture first:

```sh
lake env lean --run scripts/service-config.lean docker .images/manifest.json > .images/service-config.json
```
