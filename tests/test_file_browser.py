import tempfile
import unittest
from pathlib import Path

from app.file_browser import browse_directory, format_size


class FileBrowserTests(unittest.TestCase):
    def test_real_counts_are_direct_only_and_files_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Reports"
            folder.mkdir()
            (folder / "report.txt").write_bytes(b"hello")
            nested = folder / "2026"
            nested.mkdir()
            (nested / "nested.txt").write_bytes(b"excluded")
            (root / "notes.txt").write_bytes(b"notes")
            entries = {e.path.name: e for e in browse_directory(root, root).entries}
            self.assertEqual(entries["Reports"].file_count, 1)
            self.assertEqual(entries["Reports"].folder_count, 1)
            self.assertEqual(entries["Reports"].size, 5)
            self.assertEqual(entries["notes.txt"].size, 5)
            self.assertEqual((folder / "report.txt").read_bytes(), b"hello")

    def test_outside_root_and_links_are_not_browsed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "organized"
            root.mkdir()
            with self.assertRaises(ValueError):
                browse_directory(root, base)
            try:
                (root / "linked").symlink_to(base, target_is_directory=True)
            except OSError:
                return
            self.assertEqual(browse_directory(root, root).entries, ())
            with self.assertRaises(ValueError):
                browse_directory(root, root / "linked")

    def test_listing_limit_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a", "b", "c"):
                (root / name).write_bytes(b"1")
            result = browse_directory(root, root, limit=2)
            self.assertTrue(result.partial)
            self.assertEqual(len(result.entries), 2)

    def test_size_units(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(2048), "2.0 KB")
