import hashlib
import tempfile
import unittest
from pathlib import Path

from emolabel2.intake.hashing import sha256_file


class HashingTests(unittest.TestCase):
    def test_streaming_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.bin"
            content = b"synthetic-media-not-research-data"
            path.write_bytes(content)
            self.assertEqual(sha256_file(path, chunk_size=3), hashlib.sha256(content).hexdigest())

    def test_invalid_source_and_chunk_size(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                sha256_file(Path(directory))
            path = Path(directory) / "sample.bin"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                sha256_file(path, chunk_size=0)


if __name__ == "__main__":
    unittest.main()
