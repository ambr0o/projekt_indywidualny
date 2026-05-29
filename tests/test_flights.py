import unittest
from pathlib import Path

from flights import (
    extract_offer,
    parse_date,
    parse_display_date,
    parse_price_text,
    pick_total_price,
    price_amount,
)


FIXTURE = Path(__file__).parent / "fixtures" / "azair_result.html"


class TestFlightsParsing(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.text = (
            "There Sat 20/06/26 17:40 Warsaw WMI 20:00 Milan MXP €16.27\n"
            "Back Wed 24/06/26 12:10 Milan LIN 14:05 Warsaw WMI €14.99\n"
            "€31.26"
        )

    def test_parse_iso_date(self):
        self.assertEqual(parse_date("2026-05-24"), "2026-05-24")

    def test_parse_display_date(self):
        self.assertEqual(parse_display_date("Sat 20/06/26"), "2026-06-20")

    def test_pick_total_price(self):
        self.assertEqual(price_amount(pick_total_price(self.text)), 31.26)

    def test_extract_offer_from_fixture(self):
        offer = extract_offer(self.text, html=self.html)
        self.assertIsNotNone(offer)
        self.assertEqual(offer["origin"], "WMI")
        self.assertEqual(offer["destination"], "MXP")
        self.assertEqual(offer["departure_date"], "2026-06-20")
        self.assertEqual(offer["return_date"], "2026-06-24")
        self.assertEqual(offer["airline"], "FR")
        self.assertAlmostEqual(price_amount(offer["price_text"]), 31.26)

    def test_parse_price_text_eur(self):
        amount, currency = parse_price_text("€31.26")
        self.assertAlmostEqual(amount, 31.26)
        self.assertEqual(currency, "EUR")

    def test_parse_price_text_pln(self):
        amount, currency = parse_price_text("1 234,50 PLN")
        self.assertAlmostEqual(amount, 1234.50)
        self.assertEqual(currency, "PLN")

    def test_parse_price_text_czk(self):
        amount, currency = parse_price_text("899 CZK")
        self.assertAlmostEqual(amount, 899.0)
        self.assertEqual(currency, "CZK")


if __name__ == "__main__":
    unittest.main()
