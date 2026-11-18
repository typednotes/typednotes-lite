#!/usr/bin/env python3
"""Check both Bake targets against the frozen newest-release build manifest."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images.assembly import contexts as artifact_contexts
from images.assembly import read_receipts
from images.releases import MANIFEST_LABEL, SERVICES, canonical, validate


def check(config, bake, receipts=None, build_run=None):
    validate(config)
    targets = bake["target"]
    expected = {"app", "ledger", "liaison", "vault", "lode", "lun"}
    contexts = targets["lite"]["contexts"]
    if targets["local"]["contexts"] != {"combined": "target:lite"}:
        raise ValueError("Local must extend the exact Lite build")
    platform = targets["lite"]["platforms"]
    if platform not in (["linux/amd64"], ["linux/arm64"]):
        raise ValueError("Expected one supported native build platform")
    expected_contexts = ({name: f"target:{name}" for name in expected} if receipts is None else
                         artifact_contexts(config, receipts, platform[0], build_run))
    if contexts != expected_contexts:
        raise ValueError("Lite's inputs must match all six resolved source builds or artifact digests")
    if receipts is not None and (set(targets) & (expected | {name + "-artifact" for name in expected})):
        raise ValueError("Final assembly must not rebuild service source targets on the same runner")
    for name in ({"lite", "local"} if receipts is not None else expected | {"lite", "local"}):
        if targets[name].get("platforms") != platform:
            raise ValueError(f"{name} uses a different platform")
    for name in (expected if receipts is None else set()):
        context = targets[name]["context"]
        if context != f"https://github.com/{SERVICES[name][0]}.git#{config['sources'][name]['commit']}":
            raise ValueError(f"{name} must build the resolved release commit")
    for name in ("lite", "local"):
        if targets[name]["labels"].get(MANIFEST_LABEL) != canonical(config):
            raise ValueError(f"{name} must embed its matching source/schema manifest")
    args = targets["lite"]["args"]
    for worker in ("lode", "lun"):
        if args.get(worker.upper() + "_LINEN_REF") != config["sources"][worker]["linen"]:
            raise ValueError(f"{worker}'s native build dependencies must match its release")
    if args.get("LODE_TOOLCHAIN") != config["sources"]["lode"]["toolchain"]:
        raise ValueError("Lode's generated-project toolchain must match its release")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("bake")
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--build-run")
    args = parser.parse_args()
    with open(args.manifest) as stream:
        config = json.load(stream)
    with open(args.bake) as stream:
        bake = json.load(stream)
    check(config, bake, receipts=read_receipts(args.receipts) if args.receipts else None, build_run=args.build_run)
    print("ok: both images use the same six resolved release commits and matching SQL")
