import tempfile
import unittest
from pathlib import Path

from emolabel2.paths import (
    MissingPathError,
    NotRegularFileError,
    PathOutsideProjectError,
    SymlinkPathError,
    UnsafePathError,
    resolve_inside_project,
    resolve_manifest_input,
)


class PathTests(unittest.TestCase):
    def test_inside_project_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "data" / "inbox" / "sample.bin"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"synthetic")
            self.assertEqual(resolve_manifest_input(root, "data/inbox/sample.bin"), target.resolve())

    def test_parent_escape_and_absolute_outside_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            with self.assertRaises(PathOutsideProjectError):
                resolve_inside_project(root, "../outside.bin")
            with self.assertRaises(PathOutsideProjectError):
                resolve_inside_project(root, Path(directory) / "outside.bin")

    def test_missing_in_project_path_has_distinct_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(MissingPathError) as caught:
                resolve_inside_project(root, "data/inbox/missing.bin", must_exist=True)
            self.assertNotIsInstance(caught.exception, UnsafePathError)

    def test_existing_directory_is_not_a_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "data" / "inbox"
            target.mkdir(parents=True)
            with self.assertRaises(NotRegularFileError):
                resolve_inside_project(root, "data/inbox", must_exist=True, require_file=True)

    def test_manifest_file_must_be_in_inbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "inbox").mkdir(parents=True)
            other = root / "data" / "work" / "sample.bin"
            other.parent.mkdir()
            other.write_bytes(b"synthetic")
            with self.assertRaises(UnsafePathError):
                resolve_manifest_input(root, "data/work/sample.bin")

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            inbox = root / "data" / "inbox"
            outside = Path(directory) / "outside"
            inbox.mkdir(parents=True)
            outside.mkdir()
            (outside / "sample.bin").write_bytes(b"synthetic")
            link = inbox / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable in this environment")
            with self.assertRaises(SymlinkPathError):
                resolve_manifest_input(root, "data/inbox/link/sample.bin")


if __name__ == "__main__":
    unittest.main()
