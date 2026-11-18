"""Validate per-service build receipts and replace source builds with OCI inputs."""
import argparse
import json
import re
from pathlib import Path

from images.releases import SERVICES, validate

ARTIFACT_REPOSITORY = "ghcr.io/typednotes/typednotes-lite"
DIGEST_REFERENCE = re.compile(re.escape(ARTIFACT_REPOSITORY) + r"@sha256:[0-9a-f]{64}\Z")


def matches_run(actual, expected):
    """Reuse successful jobs on a retry, never another workflow run's output."""
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9]+-[0-9]+", expected):
        raise ValueError("Expected the workflow run ID and attempt for service artifacts")
    if not isinstance(actual, str) or not re.fullmatch(r"[0-9]+-[0-9]+", actual):
        return False
    run, attempt = actual.split("-")
    expected_run, expected_attempt = expected.split("-")
    return run == expected_run and int(attempt) <= int(expected_attempt)


def contexts(manifest, receipts, platform, build_run):
    validate(manifest)
    matches_run(build_run, build_run)
    if platform not in ("linux/amd64", "linux/arm64"):
        raise ValueError("Unsupported artifact platform")
    inputs = {}
    for receipt in receipts:
        service = receipt.get("service")
        if service not in SERVICES or service in inputs:
            raise ValueError("Unknown or duplicate service artifact")
        if (receipt.get("build_id") != manifest["build_id"] or not matches_run(receipt.get("build_run"), build_run) or
                receipt.get("platform") != platform or receipt.get("commit") != manifest["sources"][service]["commit"]):
            raise ValueError(f"{service} artifact does not match this build, source commit and platform")
        reference = receipt.get("image", "")
        if not isinstance(reference, str) or not DIGEST_REFERENCE.fullmatch(reference):
            raise ValueError(f"{service} artifact must use an immutable digest in the combined-image repository")
        inputs[service] = "docker-image://" + reference
    if set(inputs) != set(SERVICES):
        raise ValueError("Assembly requires exactly one artifact for each of the six services")
    return inputs


def variants(manifest, receipts, build_run):
    validate(manifest)
    matches_run(build_run, build_run)
    platforms = {}
    for receipt in receipts:
        platform = receipt.get("platform")
        if platform not in ("linux/amd64", "linux/arm64") or platform in platforms:
            raise ValueError("Expected exactly one tested variant pair per architecture")
        if receipt.get("build_id") != manifest["build_id"] or not matches_run(receipt.get("build_run"), build_run):
            raise ValueError("Variant images do not belong to this build/run")
        images = receipt.get("images", {})
        if not isinstance(images, dict) or set(images) != {"lite", "local"}:
            raise ValueError("Each architecture must supply both tested variants")
        for variant, image in images.items():
            if not isinstance(image, str) or not re.fullmatch(rf"ghcr\.io/typednotes/typednotes-{variant}@sha256:[0-9a-f]{{64}}", image):
                raise ValueError("Variant output must be an immutable digest in its own repository")
        platforms[platform] = images
    if set(platforms) != {"linux/amd64", "linux/arm64"}:
        raise ValueError("Both tested architectures are required before promotion")
    return {variant: [platforms[platform][variant] for platform in ("linux/amd64", "linux/arm64")]
            for variant in ("lite", "local")}


def read_receipts(directory):
    return [json.loads(path.read_text()) for path in sorted(Path(directory).glob("*.json"))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("--platform")
    parser.add_argument("--variants", action="store_true", help="Validate tested variant outputs for multi-platform promotion")
    parser.add_argument("--build-run", required=True)
    parser.add_argument("--output", type=Path, default=Path(".images/assembly.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    receipts = read_receipts(args.receipts)
    if args.variants:
        output = variants(manifest, receipts, args.build_run)
    else:
        output = {"target": {"lite": {"contexts": contexts(manifest, receipts, args.platform, args.build_run)}}}
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print("ok: digest-pinned artifacts match this source snapshot, architecture and workflow run")


if __name__ == "__main__":
    main()
