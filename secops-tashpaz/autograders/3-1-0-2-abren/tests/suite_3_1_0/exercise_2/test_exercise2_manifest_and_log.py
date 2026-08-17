import unittest
import tempfile
import re
from pathlib import Path

from solutions.suite_3_1_0.exercise_1.warehouse_setup import WarehouseSetup
from solutions.suite_3_1_0.exercise_2.shipment_manifest import ShipmentManifest
from solutions.suite_3_1_0.exercise_2.warehouse_log import WarehouseLog
from solutions.suite_3_1_0.exercise_2.warehouse import Warehouse

class TestShipmentManifest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temp_dir.name) / "manifest.txt"
        
        manifest_content = (
            "# Warehouse Shipment Manifest - May 2026\n"
            "# Format: SKU, Product Name, Quantity, Category\n"
            "\n"
            "PAPER-A4-500, Standard A4 Paper, 150, Office Supplies\n"
            "INK-BLK-01, Black Ink Cartridge, 45, Printer Consumables\n"
            "\n"
            "CHAIR-ERG-09, Ergonomic Desk Chair, 12, Furniture\n"
        )
        self.manifest_path.write_text(manifest_content, encoding="utf-8")
        self.manifest = ShipmentManifest(self.manifest_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_skips_comments_and_blanks(self):
        records = self.manifest.load()
        self.assertEqual(len(records), 3, "Should parse exactly 3 valid records")
        for record in records:
            self.assertIsInstance(record, dict, "Each record must be a dictionary")

    def test_load_content_parsing(self):
        records = self.manifest.load()
        values_string = str(records[0].values())
        self.assertIn("PAPER-A4-500", values_string, "First record should contain the correct SKU")
        self.assertIn("150", values_string, "First record should contain the correct quantity")

    def test_find_existing_sku(self):
        record = self.manifest.find("INK-BLK-01")
        self.assertIsNotNone(record, "Should return a record for an existing SKU")
        self.assertIn("INK-BLK-01", str(record.values()), "Returned record should contain the requested SKU")

    def test_find_non_existing_sku(self):
        record = self.manifest.find("MISSING-SKU-99")
        self.assertIsNone(record, "Should return None for a missing SKU")


class TestWarehouseLog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "warehouse.log"
        self.log = WarehouseLog(self.log_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_format_and_append(self):
        self.log.write("INFO", "System initialized")
        self.log.write("ERROR", "Connection failed")
        
        content = self.log_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        
        self.assertEqual(len(lines), 2, "Log should contain exactly 2 lines")
        
        log_pattern = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] INFO: System initialized$")
        self.assertTrue(log_pattern.match(lines[0]), "First log line format is incorrect")
        
        log_pattern_error = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ERROR: Connection failed$")
        self.assertTrue(log_pattern_error.match(lines[1]), "Second log line format is incorrect")

    def test_read_all(self):
        self.log_path.write_text("[2026-05-01 10:00:00] INFO: Test 1\n[2026-05-01 10:05:00] WARN: Test 2\n", encoding="utf-8")
        
        lines = self.log.read_all()
        self.assertIsInstance(lines, list, "read_all should return a list")
        self.assertEqual(len(lines), 2, "read_all should return 2 lines")
        self.assertIn("INFO: Test 1", lines[0], "First line content is incorrect")
        self.assertIn("WARN: Test 2", lines[1], "Second line content is incorrect")
        
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


if __name__ == "__main__":
    unittest.main()