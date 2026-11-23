import csv
import tempfile
import unittest
from pathlib import Path

from emolabel2.intake.manifest import ManifestValidationError, read_video_manifest
from emolabel2.paths import UnsafePathError


HEADERS = [
    "video_id", "local_path", "source_name", "rights_status", "rights_basis",
    "allowed_use", "duration_s", "notes",
]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inbox = self.root / "data" / "inbox"
        self.inbox.mkdir(parents=True)
        (self.inbox / "sample.bin").write_bytes(b"synthetic")
        self.manifest = self.root / "manifest.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, rows):
        with self.manifest.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)

    def valid_row(self):
        return {
            "video_id": "v1", "local_path": "data/inbox/sample.bin",
            "source_name": "synthetic", "rights_status": "approved_research",
            "rights_basis": "test fixture", "allowed_use": "unit tests",
            "duration_s": "10.5", "notes": "not research data",
        }

    def test_reads_approved_manifest(self):
        self.write_rows([self.valid_row()])
        rows = read_video_manifest(self.manifest, self.root)
        self.assertEqual(rows[0].video_id, "v1")

    def test_unapproved_rights_are_rejected(self):
        row = self.valid_row()
        row["rights_status"] = "pending"
        self.write_rows([row])
        with self.assertRaises(ManifestValidationError):
            read_video_manifest(self.manifest, self.root)

    def test_duplicate_id_is_rejected(self):
        self.write_rows([self.valid_row(), self.valid_row()])
        with self.assertRaises(ManifestValidationError):
            read_video_manifest(self.manifest, self.root)

    def test_outside_path_is_rejected(self):
        row = self.valid_row()
        row["local_path"] = "../outside.bin"
        self.write_rows([row])
        with self.assertRaises(UnsafePathError):
            read_video_manifest(self.manifest, self.root)


if __name__ == "__main__":
    unittest.main()
