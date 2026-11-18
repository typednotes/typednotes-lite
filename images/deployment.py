"""Read an immutable image's source manifest and matching SQL for Infra.

Registry reads are anonymous, like Infra and Scaleway pulls. Only the combined
Lite package is accepted; no host credentials or arbitrary registry URLs.
"""
import hashlib
import json
import re
import sys
from urllib.request import Request, urlopen

from images.releases import MANIFEST_LABEL, validate

REPOSITORY = "typednotes/typednotes-lite"
ACCEPT = ("application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, "
          "application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json")


def read_manifest(image, get=None):
    match = re.fullmatch(r"ghcr\.io/typednotes/typednotes-lite(?::([-\w.]+)|@(sha256:[0-9a-f]{64}))", image)
    if not match:
        raise ValueError("Expected a tag or sha256 digest of ghcr.io/typednotes/typednotes-lite")
    reference = match[1] or match[2]
    if get is None:
        with urlopen(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{REPOSITORY}:pull", timeout=30) as response:
            token = json.load(response)["token"]

        def registry_get(path):
            request = Request(f"https://ghcr.io/v2/{REPOSITORY}/{path}",
                              headers={"Authorization": "Bearer " + token, "Accept": ACCEPT})
            with urlopen(request, timeout=60) as response:
                return response.read(2 * 1024 * 1024)
        get = registry_get

    def content(path, expected=None):
        data = get(path)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            raise ValueError("Registry content does not match its immutable digest")
        return json.loads(data), digest

    manifest, digest = content("manifests/" + reference, reference if reference.startswith("sha256:") else None)
    if "manifests" in manifest:
        selected = next((entry for entry in manifest["manifests"] if entry.get("platform", {}).get("os") == "linux"
                         and entry.get("platform", {}).get("architecture") == "amd64"), None)
        if selected is None:
            raise ValueError("Lite image index has no Linux amd64 manifest")
        manifest, digest = content("manifests/" + selected["digest"], selected["digest"])
    config_digest = manifest["config"]["digest"]
    config, _ = content("blobs/" + config_digest, config_digest)
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ValueError("Scaleway requires the Linux amd64 Lite image")
    label = config.get("config", {}).get("Labels", {}).get(MANIFEST_LABEL)
    if not label:
        raise ValueError("Lite image has no build manifest; build/publish the combined images first")
    return "ghcr.io/" + REPOSITORY + "@" + digest, validate(json.loads(label))


def deployment(image, read=read_manifest, fetch=None):
    image, manifest = read(image)
    if fetch is None:
        def fetch_sql(url):
            with urlopen(url, timeout=30) as response:
                return response.read(8 * 1024 * 1024)
        fetch = fetch_sql
    histories = []
    for history in manifest["histories"]:
        migrations = []
        for migration in history["migrations"]:
            data = fetch(migration["url"])
            if hashlib.sha256(data).hexdigest() != migration["sha256"]:
                raise ValueError(f"SQL does not match the built image: {history['schema']}/{migration['id']}")
            migrations.append({"id": migration["id"], "sql": data.decode("utf-8")})
        histories.append({"schema": history["schema"], "migrations": migrations})
    return {"image": image, "runtimeVersion": manifest.get("runtimeVersion", 1),
            "vaultIdentities": manifest["vaultIdentities"], "histories": histories}


if __name__ == "__main__":
    try:
        print(json.dumps(deployment(sys.argv[1])))
    except (OSError, ValueError, KeyError) as error:
        print(f"Lite image metadata: {error}", file=sys.stderr)
        sys.exit(1)
