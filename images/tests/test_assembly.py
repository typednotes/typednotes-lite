import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from images.assembly import contexts, variants
from images.releases import SERVICES
from images.tests.fixtures import fixture_bundle

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_build", ROOT / "lite/check-build.py")
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def receipts(manifest, platform="linux/amd64"):
    return [{"service": service, "build_id": manifest["build_id"], "build_run": "123-1",
             "commit": manifest["sources"][service]["commit"], "platform": platform,
             "image": "ghcr.io/typednotes/typednotes-lite@sha256:" + f"{index:064x}"}
            for index, service in enumerate(SERVICES)]


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.manifest = fixture_bundle(self.work.name)

    def test_six_immutable_inputs_preserve_sources_platform_and_run(self):
        inputs = contexts(self.manifest, receipts(self.manifest), "linux/amd64", "123-1")
        self.assertEqual(set(inputs), set(SERVICES))
        self.assertTrue(all(value.startswith("docker-image://ghcr.io/typednotes/typednotes-lite@sha256:") for value in inputs.values()))

    def test_missing_duplicate_mutable_and_cross_run_artifacts_are_refused(self):
        original = receipts(self.manifest)
        invalid = [original[:-1], original + [original[0]]]
        for field, value in (("commit", "b" * 40), ("build_id", "different"), ("build_run", "123-2"), ("build_run", "456-1"),
                             ("platform", "linux/arm64"), ("image", "ghcr.io/typednotes/typednotes-lite:latest"),
                             ("service", "unexpected")):
            changed = copy.deepcopy(original)
            changed[0][field] = value
            invalid.append(changed)
        for changed in invalid:
            with self.subTest(receipts=changed), self.assertRaises(ValueError):
                contexts(self.manifest, changed, "linux/amd64", "123-1")

    def test_retry_reuses_only_successful_outputs_of_the_same_run_and_snapshot(self):
        inputs = receipts(self.manifest)
        inputs[0]["build_run"] = "123-2"
        self.assertEqual(len(contexts(self.manifest, inputs, "linux/amd64", "123-2")), 6)
        with self.assertRaises(ValueError):
            contexts(self.manifest, inputs, "linux/amd64", "456-2")

    def test_promotion_requires_both_tested_variants_and_architectures_by_digest(self):
        outputs = [{"build_id": self.manifest["build_id"], "build_run": "123-1", "platform": platform,
                    "images": {variant: f"ghcr.io/typednotes/typednotes-{variant}@sha256:{index:064x}"
                               for variant in ("lite", "local")}}
                   for index, platform in enumerate(("linux/amd64", "linux/arm64"))]
        outputs[1]["build_run"] = "123-2"
        self.assertEqual(len(variants(self.manifest, outputs, "123-2")["lite"]), 2)
        invalid = [outputs[:-1], outputs + [outputs[0]]]
        for field, value in (("build_run", "456-1"), ("build_id", "different"), ("platform", "linux/amd64")):
            changed = copy.deepcopy(outputs)
            changed[1][field] = value
            invalid.append(changed)
        changed = copy.deepcopy(outputs)
        changed[0]["images"]["local"] = "ghcr.io/typednotes/typednotes-local:latest"
        invalid.append(changed)
        changed = copy.deepcopy(outputs)
        del changed[0]["images"]["local"]
        invalid.append(changed)
        for changed in invalid:
            with self.subTest(outputs=changed), self.assertRaises(ValueError):
                variants(self.manifest, changed, "123-2")


@unittest.skipUnless(shutil.which("docker"), "Docker Buildx CLI is required to inspect Bake graphs (no daemon needed)")
class BakeGraphTests(unittest.TestCase):
    def test_each_source_job_builds_only_one_service_and_assembly_builds_none(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = fixture_bundle(directory)
            override = Path(directory) / "bake.json"
            for platform in ("linux/amd64", "linux/arm64"):
                def bake(*files, target=None, requested_platform=platform):
                    args = ["docker", "buildx", "bake", "-f", "lite/docker-bake.hcl", "-f", str(override)]
                    for file in files:
                        args += ["-f", str(file)]
                    args += ["--print"]
                    if target:
                        args.append(target)
                    output = subprocess.run(args, cwd=ROOT, env=os.environ | {"PLATFORM": requested_platform},
                                            text=True, capture_output=True, check=True)
                    return json.loads(output.stdout)

                for service, source in manifest["sources"].items():
                    graph = bake(target=service + "-artifact")["target"]
                    self.assertEqual(set(graph), {service, service + "-artifact"})
                    self.assertEqual(graph[service]["context"], f"https://github.com/{source['repository']}.git#{source['commit']}")
                    artifact = graph[service + "-artifact"]
                    self.assertEqual(artifact["contexts"], {"service": "target:" + service})
                    self.assertEqual(artifact["target"], service)
                    self.assertEqual(artifact["platforms"], [platform])

                inputs = receipts(manifest, platform)
                assembled = Path(directory) / "assembly.json"
                assembled.write_text(json.dumps({"target": {"lite": {"contexts": contexts(manifest, inputs, platform, "123-1")}}}))
                graph = bake(assembled)
                self.assertEqual(set(graph["target"]), {"lite", "local"})
                CHECK.check(manifest, graph, inputs, "123-1")
                # A stale/missing override must not compile six services again
                # on the fresh assembly VM, recreating the disk-exhaustion bug.
                with self.assertRaises(ValueError):
                    CHECK.check(manifest, bake(), inputs, "123-1")


class RunnerCleanupTests(unittest.TestCase):
    def test_cleanup_refuses_local_and_self_hosted_machines(self):
        for environment in ("", "self-hosted"):
            result = subprocess.run(["bash", str(ROOT / "scripts/prepare-image-runner.sh")],
                                    env=os.environ | {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": environment, "RUNNER_OS": "Linux"},
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("GitHub-hosted", result.stderr)

    def test_hosted_dry_run_does_not_invoke_sudo(self):
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "sudo"
            forbidden.write_text("#!/bin/sh\nexit 99\n")
            forbidden.chmod(0o755)
            result = subprocess.run(["bash", str(ROOT / "scripts/prepare-image-runner.sh"), "--dry-run"],
                                    env=os.environ | {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
                                                      "PATH": directory + ":" + os.environ["PATH"]},
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Would remove unused hosted SDK", result.stdout)
