import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from images.deployment import deployment, read_manifest
from images.releases import (
    MANIFEST_LABEL,
    canonical,
    latest_tag,
    resolve_refs,
    tag_listing,
    validate,
)
from images.tests.fixtures import fixture_bundle


class ReleaseTests(unittest.TestCase):
    def test_empty_sql_cannot_publish_an_image_that_infra_would_reject(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "Empty migration"):
            fixture_bundle(directory, empty_sql=True)

    def test_tag_api_pagination_checks_every_page(self):
        pages = {1: [{"name": f"v0.{number}.0", "commit": {"sha": "a" * 40}} for number in range(100)],
                 2: [{"name": "v1.0.0", "commit": {"sha": "b" * 40}}]}
        self.assertEqual(latest_tag(tag_listing("typednotes/typednotes", fetch_page=pages.__getitem__)),
                         ("v1.0.0", "b" * 40))
        with self.assertRaises(TypeError):
            tag_listing("typednotes/typednotes", fetch_page=lambda _: {"message": "not a tag list"})

    def test_highest_numeric_stable_tag_and_annotated_peeling(self):
        listing = "\n".join(f"{sha * 40}\trefs/tags/{tag}" for sha, tag in
                            (("a", "v0.9.9"), ("b", "v0.10.0"), ("c", "v0.10.0^{}"),
                             ("d", "v99.0.0-rc.1"), ("e", "latest")))
        self.assertEqual(latest_tag(listing), ("v0.10.0", "c" * 40))

    def test_lightweight_tags_and_refusal_when_no_stable_version_exists(self):
        self.assertEqual(latest_tag(f"{'a' * 40}\trefs/tags/v1.0.0"), ("v1.0.0", "a" * 40))
        with self.assertRaises(ValueError):
            latest_tag(f"{'a' * 40}\trefs/tags/v1.0.0-beta")

    def test_any_service_change_changes_polling_key(self):
        baseline = resolve_refs("packaging", lambda _: f"{'a' * 40}\trefs/tags/v1.0.0")
        updated = resolve_refs("packaging", lambda repo: f"{('b' if repo == 'typednotes/secrets' else 'a') * 40}\trefs/tags/v1.0.0")
        self.assertNotEqual(baseline["key"], updated["key"])

    def test_all_sources_and_sql_are_frozen_to_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = fixture_bundle(directory)
            self.assertEqual(validate(manifest), manifest)
            for history in manifest["histories"]:
                migration = history["migrations"][0]
                self.assertIn("a" * 40, migration["url"])
                self.assertTrue((Path(directory) / migration["file"]).is_file())
            changed = copy.deepcopy(manifest)
            changed["sources"]["app"]["commit"] = "b" * 40
            with self.assertRaises(ValueError):
                validate(changed)

    def test_image_index_freezes_amd64_and_verifies_registry_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = fixture_bundle(directory)
            config = canonical({"architecture": "amd64", "os": "linux", "config": {"Labels": {MANIFEST_LABEL: canonical(manifest)}}}).encode()
            config_id = "sha256:" + hashlib.sha256(config).hexdigest()
            image = canonical({"config": {"digest": config_id}}).encode()
            image_id = "sha256:" + hashlib.sha256(image).hexdigest()
            index = canonical({"manifests": [{"digest": image_id, "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
            content = {"manifests/latest": index, "manifests/" + image_id: image, "blobs/" + config_id: config}
            reference, actual = read_manifest("ghcr.io/typednotes/typednotes-lite:latest", get=content.__getitem__)
            self.assertTrue(reference.endswith("@" + image_id))
            self.assertEqual(actual, manifest)
            content["blobs/" + config_id] = b"{}"
            with self.assertRaisesRegex(ValueError, "immutable digest"):
                read_manifest("ghcr.io/typednotes/typednotes-lite:latest", get=content.__getitem__)

    def test_deployment_uses_image_sql_and_rejects_checksum_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = fixture_bundle(directory)
            files = {migration["url"]: (Path(directory) / migration["file"]).read_bytes()
                     for history in manifest["histories"] for migration in history["migrations"]}
            result = deployment("image", read=lambda _: ("immutable-image", manifest), fetch=files.__getitem__)
            self.assertEqual(result["image"], "immutable-image")
            self.assertEqual(result["runtimeVersion"], 2)
            self.assertEqual([history["schema"] for history in result["histories"]], ["core", "ledger", "liaison", "secrets"])
            files[next(iter(files))] = b"altered SQL"
            with self.assertRaisesRegex(ValueError, "does not match"):
                deployment("image", read=lambda _: ("immutable-image", manifest), fetch=files.__getitem__)
