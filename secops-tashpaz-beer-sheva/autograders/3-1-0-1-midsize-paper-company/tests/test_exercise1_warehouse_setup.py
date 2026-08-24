import unittest
import tempfile
import time
from pathlib import Path

from warehouse_setup import WarehouseSetup
from warehouse import Warehouse

class TestWarehouseSetup(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "test_warehouse"
        
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_directories_with_path_object(self):
        ws = WarehouseSetup(self.root_path)

        self.assertTrue(self.root_path.exists(), "Root directory was not created")

        expected_dirs = ["shipments", "inventory", "images", "logs"]
        for d in expected_dirs:
            dir_path = self.root_path / d
            self.assertTrue(dir_path.exists(), f"Directory {d} was not created")
            self.assertTrue(dir_path.is_dir(), f"Path {d} exists but is not a directory")

    def test_get_dir_existing(self):
        ws = WarehouseSetup(self.root_path)
        
        result = ws.get_dir("shipments")
        self.assertIsInstance(result, Path, "Returned value is not a Path object")
        self.assertEqual(result, self.root_path / "shipments")

    def test_get_dir_non_existing(self):
        ws = WarehouseSetup(self.root_path)
        
        result = ws.get_dir("non_existent_folder")
        self.assertIsNone(result, "Function should return None for a non-existent directory")

    def test_stat_report(self):
        ws = WarehouseSetup(self.root_path)
        
        file1_path = self.root_path / "shipments" / "box1.txt"
        file1_content = b"Hello World"
        file1_path.write_bytes(file1_content)
        
        time.sleep(0.01)
        
        file2_path = self.root_path / "logs" / "system.log"
        file2_content = b"Error 404"
        file2_path.write_bytes(file2_content)

        report = ws.stat_report()
        
        self.assertEqual(len(report), 2, "Report should contain exactly 2 files")
        
        reported_paths = [item.get("relative_path") for item in report]
        
        expected_path1 = Path("shipments/box1.txt")
        expected_path2 = Path("logs/system.log")
        
        self.assertIn(expected_path1, reported_paths, "Relative path of the first file is missing")
        self.assertIn(expected_path2, reported_paths, "Relative path of the second file is missing")

        for item in report:
            self.assertIn("st_size", item, "Missing 'st_size' key")
            self.assertIsInstance(item["st_size"], int, "Size must be an int")
            self.assertIn("st_mtime", item, "Missing 'st_mtime' key")
            self.assertIsInstance(item["st_mtime"], float, "Last modified time must be a float")
            
            if item["relative_path"] == expected_path1:
                self.assertEqual(item["st_size"], 11)
            elif item["relative_path"] == expected_path2:
                self.assertEqual(item["st_size"], 9)

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

if __name__ == "__main__":
    unittest.main()