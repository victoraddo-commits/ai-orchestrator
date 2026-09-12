import unittest
from src.verification import get_expected_sha256, get_current_sha256, is_deterministic

class TestVerification(unittest.TestCase):
    def test_get_expected_sha256(self):
        expected_sha256 = get_expected_sha256()
        self.assertIsNotNone(expected_sha256)
        self.assertIsInstance(expected_sha256, str)

    def test_get_current_sha256(self):
        current_sha256 = get_current_sha256()
        self.assertIsNotNone(current_sha256)
        self.assertIsInstance(current_sha256, str)

    def test_is_deterministic(self):
        is_det = is_deterministic()
        self.assertIsInstance(is_det, bool)

if __name__ == '__main__':
    unittest.main()
