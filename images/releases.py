#!/usr/bin/env python3
"""Resolve newest stable service tags once, freezing their commits and SQL.

Run from the repository root: python3 -m images.releases --output .images
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen

SERVICES = {
    "app": ("typednotes/typednotes", "core", "migrations"),
    "ledger": ("typednotes/ledger", "ledger", "sql"),
    "liaison": ("typednotes/liaison", "liaison", "sql"),
    "vault": ("typednotes/secrets", "secrets", "crates/secrets-storage-postgres/src/migrations"),
    "lode": ("typednotes/lode", None, None),
    "lun": ("typednotes/lun", None, None),
}
TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SQL_FILE = re.compile(r"(\d+)_[-a-zA-Z0-9_]+\.sql\Z")
MANIFEST_LABEL = "io.typednotes.build-manifest"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def latest_tag(listing):
    """Numeric semver order, ignoring aliases/prereleases and peeling tags."""
    refs = {}
    for line in listing.splitlines():
        sha, ref = line.split()
        if COMMIT.fullmatch(sha) and ref.startswith("refs/tags/"):
            refs[ref.removeprefix("refs/tags/")] = sha
    tags = [name for name in refs if TAG.fullmatch(name)]
    if not tags:
        raise ValueError("No stable vMAJOR.MINOR.PATCH release tag found")
    tag = max(tags, key=lambda name: tuple(map(int, name[1:].split("."))))
    return tag, refs.get(tag + "^{}", refs[tag])


def tag_listing(repository, fetch_page=None):
    """GitHub's tags API supplies peeled commits for annotated tags too.

    Use public HTTPS, independent of workstation Git URL rewrites/SSH agents.
    CI supplies its read-only GH_TOKEN for a higher API rate limit.
    """
    if fetch_page is None:
        def read_page(page):
            headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
            if os.environ.get("GH_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
            request = Request(f"https://api.github.com/repos/{repository}/tags?per_page=100&page={page}", headers=headers)
            with urlopen(request, timeout=60) as response:
                return json.load(response)
        fetch_page = read_page
    refs = []
    page = 1
    while True:
        tags = fetch_page(page)
        if not isinstance(tags, list):
            raise TypeError("GitHub did not return a tag list")
        for tag in tags:
            refs.append(f"{tag['commit']['sha']}\trefs/tags/{tag['name']}")
        if len(tags) < 100:
            return "\n".join(refs)
        page += 1


def archive(repository, commit):
    with urlopen(f"https://codeload.github.com/{repository}/tar.gz/{commit}", timeout=120) as response:
        data = response.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024:
        raise ValueError(f"Source archive too large: {repository}")
    files = {}
    # Read selected regular members only: never extract paths or symlinks.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        for member in source:
            path = member.name.partition("/")[2]
            wanted = path in ("lake-manifest.json", "lean-toolchain", "Dockerfile") or path.endswith(".sql")
            if member.isfile() and wanted:
                if member.size > 8 * 1024 * 1024:
                    raise ValueError(f"Source member too large: {repository}/{path}")
                stream = source.extractfile(member)
                assert stream is not None
                files[path] = stream.read()
    return files


def validate(manifest):
    if manifest.get("version") != 1 or set(manifest.get("sources", {})) != set(SERVICES):
        raise ValueError("Expected a version-1 manifest with all six services")
    for name, (repository, _, _) in SERVICES.items():
        source = manifest["sources"][name]
        if source.get("repository") != repository or not TAG.fullmatch(source.get("tag", "")) or not COMMIT.fullmatch(source.get("commit", "")):
            raise ValueError(f"Invalid release source for {name}")
    histories = manifest.get("histories", [])
    if [entry.get("schema") for entry in histories] != ["core", "ledger", "liaison", "secrets"]:
        raise ValueError("Expected all four migration histories in dependency order")
    for history in histories:
        name = history["service"]
        if name not in SERVICES or SERVICES[name][1] != history["schema"]:
            raise ValueError("Migration history does not match its service")
        repo, _, directory = SERVICES[name]
        commit = manifest["sources"][name]["commit"]
        ids = []
        for migration in history["migrations"]:
            match = SQL_FILE.fullmatch(migration["path"].removeprefix(directory + "/"))
            if not match or not migration["path"].startswith(directory + "/"):
                raise ValueError("Invalid migration source path")
            if migration["id"] != match[1] or migration["file"] != f"migrations/{history['schema']}/{Path(migration['path']).name}":
                raise ValueError("Invalid packaged migration path or ID")
            if migration["url"] != f"https://raw.githubusercontent.com/{repo}/{commit}/{migration['path']}":
                raise ValueError("Migration must come from the built service commit")
            if not re.fullmatch(r"[0-9a-f]{64}", migration["sha256"]):
                raise ValueError("Invalid SQL checksum")
            ids.append(migration["id"])
        if not ids or ids != sorted(set(ids)):
            raise ValueError("Migration IDs must be nonempty, unique and ordered")
    expected_id = hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "build_id"}).encode()).hexdigest()
    if manifest.get("build_id") != expected_id:
        raise ValueError("Build manifest checksum mismatch")
    return manifest


def resolve_refs(revision, listing=None):
    sources = {}
    for name, (repository, _, _) in SERVICES.items():
        if listing is None:
            text = tag_listing(repository)
        else:
            text = listing(repository)
        tag, commit = latest_tag(text)
        sources[name] = {"repository": repository, "tag": tag, "commit": commit}
    refs = {"revision": revision, "sources": sources}
    refs["key"] = hashlib.sha256(canonical(refs).encode()).hexdigest()
    return refs


def resolve(output, revision, listing=None, download=archive, refs=None):
    output = Path(output)
    refs = refs or resolve_refs(revision, listing)
    if refs["revision"] != revision or set(refs["sources"]) != set(SERVICES):
        raise ValueError("Release snapshot does not match this packaging revision")
    manifest = {"version": 1, "runtimeVersion": 2, "bundleRevision": revision, "sources": {}, "histories": []}
    for name, (repository, schema, directory) in SERVICES.items():
        source = dict(refs["sources"][name])
        tag, commit = source["tag"], source["commit"]
        if source["repository"] != repository or not COMMIT.fullmatch(commit) or not TAG.fullmatch(tag):
            raise ValueError("Invalid resolved source")
        manifest["sources"][name] = source
        files = download(repository, commit)
        if schema:
            migrations = []
            for path, sql in sorted(files.items()):
                match = SQL_FILE.fullmatch(path.removeprefix(directory + "/"))
                if not path.startswith(directory + "/") or not match:
                    continue
                if not sql.strip():
                    raise ValueError(f"Empty migration is not a sound Infra history: {repository}/{path}")
                relative = f"migrations/{schema}/{Path(path).name}"
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(sql)
                migrations.append({"id": match[1], "path": path, "file": relative,
                                   "url": f"https://raw.githubusercontent.com/{repository}/{commit}/{path}",
                                   "sha256": hashlib.sha256(sql).hexdigest()})
            manifest["histories"].append({"service": name, "schema": schema, "migrations": migrations})
        if name in ("lode", "lun"):
            lock = json.loads(files["lake-manifest.json"])
            linen = next(package for package in lock["packages"] if package["name"] == "linen")
            if linen.get("url") != "https://github.com/typednotes/linen" or not COMMIT.fullmatch(linen.get("rev", "")):
                raise ValueError(f"{name} must pin a first-party Linen commit")
            manifest["sources"][name].update(toolchain=files["lean-toolchain"].decode().strip(), linen=linen["rev"])
        print(f"{name}: {tag} ({commit})")
    manifest["vaultIdentities"] = json.loads(Path("images/vault-identities.json").read_text())["vaultIdentities"]
    manifest["build_id"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
    validate(manifest)
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(canonical(manifest) + "\n")
    # Later Bake file overrides all sources with immutable peeled commits.
    variables = {f"{('SECRETS' if name == 'vault' else name.upper())}_REF": {"default": source["commit"]}
                 for name, source in manifest["sources"].items()}
    labels = {MANIFEST_LABEL: canonical(manifest), "io.typednotes.build-id": manifest["build_id"]}
    for name, source in manifest["sources"].items():
        labels[f"com.typednotes.{name}.ref"] = source["tag"]
        labels[f"com.typednotes.{name}.commit"] = source["commit"]
    args = {"LODE_LINEN_REF": manifest["sources"]["lode"]["linen"], "LUN_LINEN_REF": manifest["sources"]["lun"]["linen"],
            "LODE_TOOLCHAIN": manifest["sources"]["lode"]["toolchain"]}
    override = {"variable": variables, "target": {"lite": {"labels": labels, "args": args}, "local": {"labels": labels}}}
    (output / "bake.json").write_text(json.dumps(override, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".images")
    parser.add_argument("--revision")
    parser.add_argument("--refs-only", action="store_true", help="List newest tags without downloading archives (CI change detection)")
    parser.add_argument("--refs-file", type=Path, help="Reuse the exact tag snapshot chosen by change detection")
    args = parser.parse_args()
    revision = args.revision or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if args.refs_only:
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "refs.json").write_text(canonical(resolve_refs(revision)) + "\n")
    else:
        refs = json.loads(args.refs_file.read_text()) if args.refs_file else None
        resolve(args.output, revision, refs=refs)


if __name__ == "__main__":
    main()
