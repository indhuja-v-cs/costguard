"""Tests for SQLite cache module."""

import os
import tempfile
import unittest
from decimal import Decimal
from costguard.cache import PricingCache
from costguard.models import PriceRecord


class TestPricingCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.cache = PricingCache(self.tmp.name)

    def tearDown(self):
        self.cache.close()
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_store_and_lookup(self):
        record = PriceRecord(
            sku="Standard_B2s",
            region="eastus",
            currency="USD",
            resource_kind="vm",
            hourly_rate=Decimal("0.0416"),
            unit_of_measure="1 Hour",
            meter_name="B2s",
            product_name="Virtual Machines B Series",
        )
        self.cache.store(record)
        self.assertEqual(self.cache.count(), 1)

        fetched = self.cache.lookup("Standard_B2s", "eastus", "USD", "vm")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.sku, "Standard_B2s")
        self.assertEqual(fetched.region, "eastus")
        self.assertEqual(fetched.currency, "USD")
        self.assertEqual(fetched.resource_kind, "vm")
        self.assertEqual(fetched.hourly_rate, Decimal("0.0416"))
        self.assertEqual(fetched.unit_of_measure, "1 Hour")
        self.assertEqual(fetched.meter_name, "B2s")
        self.assertEqual(fetched.product_name, "Virtual Machines B Series")

    def test_lookup_miss(self):
        fetched = self.cache.lookup("Unknown_SKU", "eastus", "USD", "vm")
        self.assertIsNone(fetched)

    def test_clear_cache(self):
        record = PriceRecord(
            sku="Standard_B2s",
            region="eastus",
            currency="USD",
            resource_kind="vm",
            hourly_rate=Decimal("0.0416"),
            unit_of_measure="1 Hour",
        )
        self.cache.store(record)
        self.assertEqual(self.cache.count(), 1)

        self.cache.clear()
        self.assertEqual(self.cache.count(), 0)
        self.assertIsNone(self.cache.lookup("Standard_B2s", "eastus", "USD", "vm"))

    def test_currency_isolation(self):
        rec_usd = PriceRecord(
            sku="Standard_B2s",
            region="eastus",
            currency="USD",
            resource_kind="vm",
            hourly_rate=Decimal("0.0416"),
            unit_of_measure="1 Hour",
        )
        rec_eur = PriceRecord(
            sku="Standard_B2s",
            region="eastus",
            currency="EUR",
            resource_kind="vm",
            hourly_rate=Decimal("0.0385"),
            unit_of_measure="1 Hour",
        )
        self.cache.store(rec_usd)
        self.cache.store(rec_eur)
        self.assertEqual(self.cache.count(), 2)

        fetched_usd = self.cache.lookup("Standard_B2s", "eastus", "USD", "vm")
        fetched_eur = self.cache.lookup("Standard_B2s", "eastus", "EUR", "vm")
        self.assertEqual(fetched_usd.hourly_rate, Decimal("0.0416"))
        self.assertEqual(fetched_eur.hourly_rate, Decimal("0.0385"))


if __name__ == "__main__":
    unittest.main()
