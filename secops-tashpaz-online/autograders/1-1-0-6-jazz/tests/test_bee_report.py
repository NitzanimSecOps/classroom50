import unittest
from bee_report import BeeReport


class TestBeeReport(unittest.TestCase):
    """A daily report byte: bit 7 = found_pollen, bits 5-6 = field_id, bits 0-4 = hive_id."""

    def test_pollen_field3_hive10(self):
        """0b11101010: pollen found, field 3, hive 10 (the spec's worked example)."""
        report = BeeReport(0b11101010)
        self.assertEqual(report.found_pollen, 1, "bit 7 should give found_pollen = 1")
        self.assertEqual(report.field_id, 3, "bits 5-6 should give field_id = 3")
        self.assertEqual(report.hive_id, 10, "bits 0-4 should give hive_id = 10")

    def test_no_pollen_field0_hive16(self):
        """0b00010000: no pollen, field 0, hive 16 (the spec's second example)."""
        report = BeeReport(0b00010000)
        self.assertEqual(report.found_pollen, 0, "bit 7 clear should give found_pollen = 0")
        self.assertEqual(report.field_id, 0, "bits 5-6 clear should give field_id = 0")
        self.assertEqual(report.hive_id, 16, "bits 0-4 should give hive_id = 16")

    def test_field_id_is_isolated_and_shifted(self):
        """0xA4 (0b10100100): field_id must be shifted down to 1, not left as 0b0100000."""
        self.assertEqual(BeeReport(0xA4).field_id, 1, "field_id must be masked AND shifted right by 5")

    def test_all_ones(self):
        """0xFF: pollen 1, field 3, hive 31 (every field at its maximum)."""
        report = BeeReport(0xFF)
        self.assertEqual(report.found_pollen, 1)
        self.assertEqual(report.field_id, 3)
        self.assertEqual(report.hive_id, 31)

    def test_all_zeros(self):
        """0x00: every field is zero."""
        report = BeeReport(0x00)
        self.assertEqual(report.found_pollen, 0)
        self.assertEqual(report.field_id, 0)
        self.assertEqual(report.hive_id, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
