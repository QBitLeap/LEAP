from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from quantums.gallery_yaml import GalleryYamlError, load_create, load_update


A = "a" * 64 + ":0"
B = "b" * 64 + ":1"


class GalleryYamlTests(unittest.TestCase):
    def test_documented_manifests_load(self):
        create = load_create(Path("gallery-examples/gallery-create.yaml"))
        update = load_update(Path("gallery-examples/gallery-update.yaml"))
        self.assertEqual(create.name, "Leap Example Gallery")
        self.assertEqual(create.ownership_bits, 4_000)
        self.assertTrue(update.add and update.remove)

    def test_creation_requires_name_and_members_but_allows_empty_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gallery.yaml"
            path.write_text("name: Future Gallery\nmembers: []\n")
            self.assertEqual(load_create(path).members, ())
            path.write_text("name: Missing Members\n")
            with self.assertRaises(GalleryYamlError):
                load_create(path)

    def test_update_requires_a_change_and_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.yaml"
            path.write_text(f"gallery: {A}\n")
            with self.assertRaises(GalleryYamlError):
                load_update(path)
            path.write_text(f"gallery: {A}\nadd: [{B}]\nremove: [{B}]\n")
            with self.assertRaises(GalleryYamlError):
                load_update(path)

    def test_unknown_and_duplicate_keys_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gallery.yaml"
            path.write_text("name: G\nname: H\nmembers: []\n")
            with self.assertRaises(GalleryYamlError):
                load_create(path)
            path.write_text("name: G\nmembers: []\nfee: 1\n")
            with self.assertRaises(GalleryYamlError):
                load_create(path)


if __name__ == "__main__":
    unittest.main()
