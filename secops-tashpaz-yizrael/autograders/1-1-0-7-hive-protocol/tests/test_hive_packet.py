import unittest
from hive_packet import HivePacket

class TestHivePacket(unittest.TestCase):

    def setUp(self):
        # The golden packet from the specification: 10 01 2C C5 2A D2
        # Bee ID: 300, Nectar: 1, Field: 2, Internal Bee ID: 5, Honey: 42
        self.valid_raw_packet = bytes([0x10, 0x01, 0x2C, 0xC5, 0x2A, 0xD2])
        self.packet = HivePacket(self.valid_raw_packet)

    def test_header_parsing(self):
        """Test that the Header (Byte 0) is parsed correctly."""
        self.assertEqual(self.packet.packet_type, 0, "Packet type should be 00 (daily_report)")
        self.assertEqual(self.packet.version, 1, "Version should be 1")
        self.assertEqual(self.packet.reserved_bits, 0, "Reserved bits should be 0")
        self.assertEqual(self.packet.more_packets, 0, "More packets flag should be 0")

    def test_bee_id_parsing(self):
        """Test that the Big Endian Bee ID (Bytes 1-2) is parsed correctly."""
        self.assertEqual(self.packet.bee_id, 300, "Bee ID (Bytes 1-2) should equal 300")

    def test_honey_weight_parsing(self):
        """Test that Honey Weight (Byte 4) is parsed correctly."""
        self.assertEqual(self.packet.honey_weight, 42, "Honey weight should be 42")

    def test_checksum_parsing(self):
        """Test that the Checksum (Byte 5) is extracted correctly."""
        self.assertEqual(self.packet.checksum, 0xD2, "Checksum should be 0xD2")

    def test_verify_packet_valid(self):
        """Test that verify_packet() returns True for a valid packet."""
        self.assertTrue(self.packet.verify_packet(), "verify_packet should return True for valid checksum and version")

    def test_verify_packet_invalid_checksum(self):
        """Test that verify_packet() catches a corrupted payload."""
        # Corrupting the honey weight byte (changing 0x2A to 0x2B)
        invalid_raw_packet = bytes([0x10, 0x01, 0x2C, 0xC5, 0x2B, 0xD2])
        bad_packet = HivePacket(invalid_raw_packet)
        self.assertFalse(bad_packet.verify_packet(), "verify_packet should return False if checksum fails")

    def test_verify_packet_invalid_version(self):
        """Test that verify_packet() catches a bad version number."""
        # Changing version from 01 to 10 (Byte 0 becomes 0x20 instead of 0x10)
        invalid_raw_packet = bytes([0x20, 0x01, 0x2C, 0xC5, 0x2A, 0xD2])
        bad_packet = HivePacket(invalid_raw_packet)
        self.assertFalse(bad_packet.verify_packet(), "verify_packet should return False if version is not 1")

if __name__ == '__main__':
    unittest.main(verbosity=2)