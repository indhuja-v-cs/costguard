"""Tests for pricing module (Azure Retail Prices client validation & pagination)."""

import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from costguard.pricing import (
    AzurePricingClient,
    resolve_disk_tier,
)


class TestPricing(unittest.TestCase):
    def setUp(self):
        self.client = AzurePricingClient()

    def test_resolve_disk_tier(self):
        self.assertEqual(resolve_disk_tier("Premium_LRS", 128), "P10")
        self.assertEqual(resolve_disk_tier("Premium_LRS", 100), "P10")
        self.assertEqual(resolve_disk_tier("Premium_LRS", 4), "P1")
        self.assertEqual(resolve_disk_tier("Premium_LRS", 2048), "P40")
        self.assertEqual(resolve_disk_tier("StandardSSD_LRS", 128), "E10")
        self.assertEqual(resolve_disk_tier("Standard_LRS", 128), "S10")
        self.assertIsNone(resolve_disk_tier("Unknown_Type", 128))
        self.assertIsNone(resolve_disk_tier("Premium_LRS", None))

    def test_validate_vm_record_valid(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "priceType": "Consumption",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0416,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
            "skuName": "Standard_B2s",
        }
        self.assertTrue(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_price_type_field_only(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "priceType": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0416,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
            "skuName": "Standard_B2s",
        }
        self.assertTrue(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_type_field_only(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0416,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
            "skuName": "Standard_B2s",
        }
        self.assertTrue(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_price_type_precedence_over_type_rejected(self):
        # priceType is Reservation, but type is Consumption -> reject because priceType takes precedence
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "priceType": "Reservation",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0416,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
            "skuName": "Standard_B2s",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_reservation_pricing_rejected_both_fields(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "priceType": "Reservation",
            "type": "Reservation",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0200,
            "meterName": "B2s 1 Year Reserved",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_spot_pricing_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0100,
            "meterName": "B2s Spot",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_low_priority_pricing_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0100,
            "meterName": "B2s Low Priority",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_reservation_pricing_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Reservation",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0200,
            "meterName": "B2s 1 Year Reserved",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_devtest_pricing_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0200,
            "meterName": "B2s DevTest",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_windows_pricing_not_selected_for_linux(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0800,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series Windows",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_linux_pricing_not_selected_for_windows(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0416,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series Linux",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "windows")
        )

    def test_incorrect_currency_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "EUR",
            "type": "Consumption",
            "unitOfMeasure": "1 Hour",
            "retailPrice": 0.0385,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_non_hourly_vm_meters_rejected(self):
        item = {
            "armSkuName": "Standard_B2s",
            "armRegionName": "eastus",
            "currencyCode": "USD",
            "type": "Consumption",
            "unitOfMeasure": "1 Month",
            "retailPrice": 30.00,
            "meterName": "B2s",
            "productName": "Virtual Machines B Series",
        }
        self.assertFalse(
            self.client._validate_vm_record(item, "Standard_B2s", "eastus", "USD", "linux")
        )

    def test_pagination_followed(self):
        page1_response = {
            "Items": [
                {
                    "armSkuName": "Other_SKU",
                    "armRegionName": "eastus",
                    "currencyCode": "USD",
                    "type": "Consumption",
                    "unitOfMeasure": "1 Hour",
                    "retailPrice": 0.05,
                    "meterName": "Other",
                    "productName": "Virtual Machines",
                }
            ],
            "NextPageLink": "https://prices.azure.com/api/retail/prices?page=2",
        }
        page2_response = {
            "Items": [
                {
                    "armSkuName": "Standard_B2s",
                    "armRegionName": "eastus",
                    "currencyCode": "USD",
                    "type": "Consumption",
                    "unitOfMeasure": "1 Hour",
                    "retailPrice": 0.0416,
                    "meterName": "B2s",
                    "productName": "Virtual Machines B Series",
                }
            ],
            "NextPageLink": None,
        }

        import json

        def fake_urlopen(req, timeout=30):
            url = req.full_url if hasattr(req, "full_url") else req
            mock_resp = MagicMock()
            if "page=2" in url:
                mock_resp.read.return_value = json.dumps(page2_response).encode("utf-8")
            else:
                mock_resp.read.return_value = json.dumps(page1_response).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            price_rec = self.client.get_vm_price("Standard_B2s", "eastus", "USD", "linux")
            self.assertIsNotNone(price_rec)
            self.assertEqual(price_rec.sku, "Standard_B2s")
            self.assertEqual(price_rec.hourly_rate, Decimal("0.0416"))

    def test_managed_disk_pricing(self):
        disk_response = {
            "Items": [
                {
                    "armRegionName": "eastus",
                    "currencyCode": "USD",
                    "type": "Consumption",
                    "unitOfMeasure": "1 Month",
                    "retailPrice": 19.71,
                    "meterName": "P10 LRS Disk",
                    "productName": "Premium SSD Managed Disks",
                    "skuName": "P10 LRS",
                }
            ],
            "NextPageLink": None,
        }
        import json

        def fake_urlopen(req, timeout=30):
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(disk_response).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            disk_rec = self.client.get_disk_price("Premium_LRS", "eastus", "USD", 128)
            self.assertIsNotNone(disk_rec)
            self.assertEqual(disk_rec.sku, "Premium_LRS/P10")
            self.assertEqual(disk_rec.hourly_rate, Decimal("19.71"))


if __name__ == "__main__":
    unittest.main()
