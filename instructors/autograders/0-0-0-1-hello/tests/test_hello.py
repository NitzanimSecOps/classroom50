import unittest

from hello import hello


class TestHello(unittest.TestCase):
    """Hello, Autograder — your first exercise: implement hello() in hello.py."""

    def test_returns_hello_world(self):
        """hello() returns exactly the string "Hello, World!"."""
        self.assertEqual(hello(), "Hello, World!")

    def test_returns_a_string(self):
        """hello() returns a str value (not None, and not printed to stdout)."""
        self.assertIsInstance(hello(), str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
