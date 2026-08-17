import unittest
from solutions.suite_1_99_0.exercise_2.solution import parse_transmission


class TestMajorTomParser(unittest.TestCase):

    def setUp(self):
        # Transmission 0: ID=10 (0x000A), Flags=1 (0x01), Oxygen=500 (0xF401 in Little Endian)
        self.transmission_0_raw = bytes([0x00, 0x0A, 0x01, 0xF4, 0x01])

        # Transmission 1: ID=258 (0x0102 in Big Endian), Flags=0 (0x00), Oxygen=0 (0x0000)
        self.transmission_1_raw = bytes([0x01, 0x02, 0x00, 0x00, 0x00])

        # Transmission 2: ID=10 (0x000A), Flags=3 (0x03 -> both bits set), Oxygen=12 (0x0C00 in Little Endian)
        self.transmission_2_raw = bytes([0x00, 0x0A, 0x03, 0x0C, 0x00])

    def test_return_type(self):
        """Verify that the parser function returns a dictionary."""
        result = parse_transmission(self.transmission_0_raw)
        self.assertIsInstance(result, dict, "The function must return a dictionary")

    def test_transmission_0_parsing(self):
        """Test parsing of transmission 0 using official reference values."""
        result = parse_transmission(self.transmission_0_raw)

        self.assertEqual(result.get('signal_id'), 10, "Signal ID should be 10")
        self.assertEqual(result.get('am_i_floating'), 1, "am_i_floating flag should be 1")
        self.assertEqual(result.get('circuit_dead'), 0, "circuit_dead flag should be 0")
        self.assertEqual(result.get('oxygen_level'), 500, "Oxygen level should be 500")

    def test_transmission_1_endianness_and_zeroes(self):
        """Verify Big Endian bounds handling (258) and zero values."""
        result = parse_transmission(self.transmission_1_raw)

        self.assertEqual(result.get('signal_id'), 258,
                         "Signal ID should handle Big Endian conversion correctly (0x0102 -> 258)")
        self.assertEqual(result.get('am_i_floating'), 0, "am_i_floating flag should be 0")
        self.assertEqual(result.get('circuit_dead'), 0, "circuit_dead flag should be 0")
        self.assertEqual(result.get('oxygen_level'), 0, "Oxygen level should be 0")

    def test_transmission_2_bitmask_combination(self):
        """Verify compound bitmask logic when multiple flags are concurrently set (value 3)."""
        result = parse_transmission(self.transmission_2_raw)

        self.assertEqual(result.get('signal_id'), 10, "Signal ID should be 10")
        self.assertEqual(result.get('am_i_floating'), 1, "am_i_floating flag should be 1 when Bit 0 is set")
        self.assertEqual(result.get('circuit_dead'), 1, "circuit_dead flag should be 1 when Bit 1 is set")
        self.assertEqual(result.get('oxygen_level'), 12, "Oxygen level should be 12 (0x0C00 in Little Endian)")

    def test_missing_fields(self):
        """Ensure all mandatory schema keys exist within the output dictionary structure."""
        result = parse_transmission(self.transmission_0_raw)
        required_keys = {'signal_id', 'am_i_floating', 'circuit_dead', 'oxygen_level'}

        for key in required_keys:
            self.assertIn(key, result, f"The returned dictionary is missing the required key: '{key}'")


if __name__ == '__main__':
    unittest.main(verbosity=2)