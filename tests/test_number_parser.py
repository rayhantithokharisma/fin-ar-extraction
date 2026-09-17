"""
Unit tests for number parser, scale application, and grounding checks.
"""
import unittest
from decimal import Decimal
from src.normalization.number_parser import apply_scale, grounded, parse_reported_number


class TestNumberParser(unittest.TestCase):
    def test_parse_locale_en(self):
        self.assertEqual(parse_reported_number("14,159,490", "en"), Decimal("14159490"))
        self.assertEqual(parse_reported_number("(19,941,401)", "en"), Decimal("-19941401"))
        self.assertEqual(parse_reported_number("1,234.56", "en"), Decimal("1234.56"))
        self.assertEqual(parse_reported_number("-", "en"), Decimal("0"))
        self.assertEqual(parse_reported_number("–", "en"), Decimal("0"))
        self.assertEqual(parse_reported_number("nil", "en"), Decimal("0"))

    def test_parse_locale_id(self):
        self.assertEqual(parse_reported_number("29.632.799", "id"), Decimal("29632799"))
        self.assertEqual(parse_reported_number("(1.513.694)", "id"), Decimal("-1513694"))
        self.assertEqual(parse_reported_number("1.234,56", "id"), Decimal("1234.56"))
        self.assertEqual(parse_reported_number("nihil", "id"), Decimal("0"))

    def test_apply_scale(self):
        parsed = Decimal("14159490")
        scaled = apply_scale(parsed, 1_000_000)
        self.assertEqual(scaled, Decimal("14159490000000"))

    def test_grounding(self):
        page_text = "Penjualan dan pendapatan usaha 29,632,799 26,965,262 Sales and revenue"
        self.assertTrue(grounded("29,632,799", page_text))
        self.assertTrue(grounded("26,965,262", page_text))
        self.assertFalse(grounded("99,999,999", page_text))


if __name__ == "__main__":
    unittest.main()
