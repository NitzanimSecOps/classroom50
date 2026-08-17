import struct
import unittest
import tempfile
from pathlib import Path
from datetime import datetime

from solutions.suite_3_1_0.exercise_1.warehouse_setup import WarehouseSetup
from solutions.suite_3_1_0.exercise_2.warehouse_log import WarehouseLog
from solutions.suite_3_1_0.exercise_3.inventory_record import InventoryRecord
from solutions.suite_3_1_0.exercise_3.inventory_db import InventoryDB
from solutions.suite_3_1_0.exercise_3.warehouse import Warehouse

RECORD_FORMAT = "<4s16sIfd"


def pack_record(sku: str, quantity: int, price: float, timestamp: datetime) -> bytes:
    """Build a raw INVR record exactly as it is written to disk."""
    return struct.pack(
        RECORD_FORMAT, b"INVR", sku.encode("ascii"), quantity, price, timestamp.timestamp()
    )


def make_record(sku: str, quantity: int, price: float, timestamp: datetime) -> InventoryRecord:
    """Construct an InventoryRecord through its bytes-based constructor."""
    return InventoryRecord(pack_record(sku, quantity, price, timestamp))


def record_filename(sku: str, timestamp: datetime) -> str:
    """The on-disk name add_record is expected to produce for a record."""
    return f"{sku}_{timestamp.strftime('%Y-%m-%d %H-%M-%S')}.invr"


class TestInventoryRecord(unittest.TestCase):
    def setUp(self):
        self.sample_time = datetime(2026, 5, 5, 12, 0, 0)
        self.sample_bytes = pack_record("TEST-SKU", 100, 9.99, self.sample_time)
        self.sample_record = InventoryRecord(self.sample_bytes)

    def test_init_from_bytes(self):
        record = InventoryRecord(self.sample_bytes)

        self.assertEqual(record.sku, "TEST-SKU", "SKU did not unpack correctly")
        self.assertEqual(record.quantity, 100, "Quantity did not unpack correctly")
        self.assertAlmostEqual(
            record.price, 9.99, places=4, msg="Price did not unpack correctly due to precision"
        )
        self.assertEqual(
            record.timestamp, self.sample_time, "Timestamp did not unpack correctly"
        )

    def test_to_bytes(self):
        data = self.sample_record.to_bytes()

        self.assertIsInstance(data, bytes, "to_bytes must return a bytes object")
        self.assertEqual(len(data), 36, "Binary record must be exactly 36 bytes long")
        self.assertTrue(data.startswith(b"INVR"), "Record must start with INVR magic bytes")

    def test_to_bytes_round_trip(self):
        # Re-serializing a record must reproduce the exact bytes it was built from.
        self.assertEqual(
            self.sample_record.to_bytes(),
            self.sample_bytes,
            "to_bytes/__init__ do not round-trip to identical bytes",
        )


class TestInventoryDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir = Path(self.temp_dir.name) / "inventory"
        self.db = InventoryDB(self.db_dir)

        self.older_time = datetime(2026, 5, 5, 12, 0, 0)
        self.newer_time = datetime(2026, 5, 5, 13, 30, 0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_directory(self):
        self.assertTrue(
            self.db_dir.is_dir(), "InventoryDB must create its path as a directory of records"
        )

    def test_add_record_writes_named_file(self):
        record = make_record("PAPER-A4-500", 500, 4.25, self.older_time)
        self.db.add_record(record)

        expected = self.db_dir / record_filename("PAPER-A4-500", self.older_time)
        self.assertTrue(
            expected.exists(),
            f"add_record must create a file named '{expected.name}'",
        )

    def test_add_record_file_contains_record_bytes(self):
        record = make_record("PAPER-A4-500", 500, 4.25, self.older_time)
        self.db.add_record(record)

        expected = self.db_dir / record_filename("PAPER-A4-500", self.older_time)
        self.assertEqual(
            expected.read_bytes(),
            record.to_bytes(),
            "The stored file must contain the packed record bytes",
        )

    def test_find_record_returns_inventory_record(self):
        record = make_record("SKU-FIND", 42, 1.50, self.older_time)
        self.db.add_record(record)

        found = self.db.find_record("SKU-FIND")
        self.assertIsInstance(
            found, InventoryRecord, "find_record must return an InventoryRecord instance"
        )
        self.assertEqual(found.sku, "SKU-FIND", "find_record returned the wrong SKU")
        self.assertEqual(found.quantity, 42, "find_record returned the wrong quantity")

    def test_find_record_returns_newest(self):
        self.db.add_record(make_record("SKU-NEW", 100, 1.0, self.older_time))
        self.db.add_record(make_record("SKU-NEW", 200, 1.0, self.newer_time))

        found = self.db.find_record("SKU-NEW")
        self.assertIsInstance(found, InventoryRecord, "find_record must return an InventoryRecord")
        self.assertEqual(
            found.quantity, 200, "find_record must return the newest record for the SKU"
        )

    def test_find_record_missing_returns_none(self):
        self.assertIsNone(
            self.db.find_record("DOES-NOT-EXIST"),
            "find_record must return None when no record matches",
        )

    def test_remove_record_removes_newest(self):
        self.db.add_record(make_record("SKU-DEL", 100, 1.0, self.older_time))
        self.db.add_record(make_record("SKU-DEL", 200, 1.0, self.newer_time))

        self.db.remove_record("SKU-DEL")

        newest_file = self.db_dir / record_filename("SKU-DEL", self.newer_time)
        oldest_file = self.db_dir / record_filename("SKU-DEL", self.older_time)
        self.assertFalse(newest_file.exists(), "remove_record must delete the newest record file")
        self.assertTrue(oldest_file.exists(), "remove_record must keep older record files")

    def test_remove_record_missing_is_noop(self):
        # Removing a SKU that has no files must not raise.
        try:
            self.db.remove_record("NOTHING-HERE")
        except Exception as exc:  # pragma: no cover - failure path
            self.fail(f"remove_record raised on a missing SKU: {exc!r}")

    def test_detect_file_type(self):
        signatures = {
            "INVR": b'INVR\x00\x00\x00\x00',
            "JPEG": b'\xff\xd8\xff\xe0\x00\x10JFIF',
            "PNG": b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR',
            "BMP": b'BM\x8a\x00\x00\x00\x00\x00',
            "UNKNOWN": b'MZ\x90\x00\x03\x00\x00\x00',
        }

        for expected_type, magic_bytes in signatures.items():
            test_file = self.db_dir / f"test_{expected_type.lower()}.bin"
            test_file.write_bytes(magic_bytes)

            detected = InventoryDB.detect_file_type(test_file)
            self.assertEqual(
                detected, expected_type, f"Failed to detect {expected_type} correctly"
            )


class TestWarehouse(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "main_warehouse"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_warehouse_initialization(self):
        warehouse = Warehouse(self.root_path)

        self.assertTrue(hasattr(warehouse, 'setup'), "Warehouse is missing 'setup' attribute")
        self.assertIsInstance(warehouse.setup, WarehouseSetup, "'setup' is not a WarehouseSetup instance")
        self.assertTrue(self.root_path.exists(), "WarehouseSetup failed to create the root directory")

        self.assertTrue(hasattr(warehouse, 'log'), "Warehouse is missing 'log' attribute")
        self.assertIsInstance(warehouse.log, WarehouseLog, "'log' is not a WarehouseLog instance")

        expected_log_path = self.root_path / "logs" / "warehouse.log"
        if hasattr(warehouse.log, 'path'):
            self.assertEqual(warehouse.log.path, expected_log_path, "Log path is incorrect")

        self.assertTrue(hasattr(warehouse, 'db'), "Warehouse is missing 'db' attribute")
        self.assertIsInstance(warehouse.db, InventoryDB, "'db' is not an InventoryDB instance")


if __name__ == "__main__":
    unittest.main()
