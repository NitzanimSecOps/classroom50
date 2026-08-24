import unittest
import tempfile
import hashlib
from pathlib import Path

from integrity_checker import IntegrityChecker

class TestIntegrityChecker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "warehouse"
        self.root_path.mkdir()

        self.file1 = self.root_path / "data.txt"
        self.file2 = self.root_path / "logs" / "system.log"
        self.file2.parent.mkdir()

        self.file1.write_bytes(b"hello world")
        self.file2.write_bytes(b"test log")

        self.checker = IntegrityChecker(self.root_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute(self):
        hashes = self.checker.compute()
        
        self.assertEqual(len(hashes), 2, "Should compute exactly 2 hashes")

        expected_hash1 = hashlib.sha256(b"hello world").hexdigest()
        expected_hash2 = hashlib.sha256(b"test log").hexdigest()

        key1 = str(Path("data.txt"))
        key2 = str(Path("logs") / "system.log")

        self.assertEqual(hashes[key1], expected_hash1, "Hash for first file is incorrect")
        self.assertEqual(hashes[key2], expected_hash2, "Hash for nested file is incorrect")

    def test_save(self):
        dest_file = Path(self.temp_dir.name) / "checksums.txt"
        self.checker.save(dest_file)

        self.assertTrue(dest_file.exists(), "Hash file was not created")

        content = dest_file.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.strip().split("\n")]
        
        self.assertEqual(len(lines), 2, "Hash file should contain exactly 2 lines")

        expected_hash = hashlib.sha256(b"hello world").hexdigest()
        expected_line = f"{expected_hash}  {str(Path('data.txt'))}"
        
        self.assertIn(expected_line, lines, "Properly formatted hash line is missing")

    def test_verify_no_changes(self):
        dest_file = Path(self.temp_dir.name) / "checksums.txt"
        self.checker.save(dest_file)

        discrepancies = self.checker.verify(dest_file)
        self.assertEqual(len(discrepancies), 0, "Discrepancies should be empty when no files changed")

    def test_verify_with_all_discrepancy_types(self):
        dest_file = Path(self.temp_dir.name) / "checksums.txt"
        self.checker.save(dest_file)

        self.file1.write_bytes(b"hello world mutated")

        self.file2.unlink()

        new_file = self.root_path / "new_data.txt"
        new_file.write_bytes(b"brand new file")

        discrepancies = self.checker.verify(dest_file)

        self.assertEqual(len(discrepancies), 3, "Should detect exactly 3 discrepancies")

        key_changed = str(Path("data.txt"))
        key_missing = str(Path("logs") / "system.log")
        key_new = str(Path("new_data.txt"))

        self.assertEqual(discrepancies.get(key_changed), "CHANGED", "Did not detect CHANGED file")
        self.assertEqual(discrepancies.get(key_missing), "MISSING", "Did not detect MISSING file")
        self.assertEqual(discrepancies.get(key_new), "NEW", "Did not detect NEW file")

if __name__ == "__main__":
    unittest.main()