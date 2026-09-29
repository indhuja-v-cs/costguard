"""Tests for calculator module (cost computation, actions, metadata updates, cache hit/miss)."""

import os
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import MagicMock
from costguard.cache import PricingCache
from costguard.calculator import CostCalculator, MONTHLY_HOURS
from costguard.models import PriceRecord, ResourceChange
from costguard.pricing import AzurePricingClient


class TestCalculator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.cache = PricingCache(self.tmp.name)
        self.client = AzurePricingClient()

    def tearDown(self):
        self.cache.close()
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_create_cost(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["create"],
            before_state=None,
            after_state={"size": "Standard_B2s", "location": "eastus"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        expected_cost = Decimal("0.0416") * MONTHLY_HOURS
        self.assertEqual(len(report.line_items), 1)
        self.assertEqual(report.line_items[0].old_monthly, Decimal("0"))
        self.assertEqual(report.line_items[0].new_monthly, expected_cost)
        self.assertEqual(report.line_items[0].delta, expected_cost)
        self.assertEqual(report.prior_total, Decimal("0"))
        self.assertEqual(report.projected_total, expected_cost)
        self.assertEqual(report.net_delta, expected_cost)
        self.assertEqual(report.cache_hits, 1)
        self.assertEqual(report.api_lookups, 0)

    def test_delete_savings(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["delete"],
            before_state={"size": "Standard_B2s", "location": "eastus"},
            after_state=None,
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        cost = Decimal("0.0416") * MONTHLY_HOURS
        self.assertEqual(report.line_items[0].old_monthly, cost)
        self.assertEqual(report.line_items[0].new_monthly, Decimal("0"))
        self.assertEqual(report.line_items[0].delta, -cost)
        self.assertEqual(report.prior_total, cost)
        self.assertEqual(report.projected_total, Decimal("0"))
        self.assertEqual(report.net_delta, -cost)

    def test_update_cost_difference(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        self.cache.store(
            PriceRecord(
                sku="Standard_D4s_v3",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.192"),
                unit_of_measure="1 Hour",
            )
        )
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["update"],
            before_state={"size": "Standard_B2s", "location": "eastus"},
            after_state={"size": "Standard_D4s_v3", "location": "eastus"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        old_cost = Decimal("0.0416") * MONTHLY_HOURS
        new_cost = Decimal("0.192") * MONTHLY_HOURS
        self.assertEqual(report.line_items[0].old_monthly, old_cost)
        self.assertEqual(report.line_items[0].new_monthly, new_cost)
        self.assertEqual(report.line_items[0].delta, new_cost - old_cost)
        self.assertEqual(report.net_delta, new_cost - old_cost)

    def test_replacement_cost_difference(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        self.cache.store(
            PriceRecord(
                sku="Standard_D2s_v3",
                region="westus2",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.096"),
                unit_of_measure="1 Hour",
            )
        )
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["delete", "create"],
            before_state={"size": "Standard_B2s", "location": "eastus"},
            after_state={"size": "Standard_D2s_v3", "location": "westus2"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        old_cost = Decimal("0.0416") * MONTHLY_HOURS
        new_cost = Decimal("0.096") * MONTHLY_HOURS
        self.assertEqual(report.line_items[0].action, "replace")
        self.assertEqual(report.line_items[0].delta, new_cost - old_cost)

    def test_metadata_only_update_produces_zero_delta(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["update"],
            before_state={
                "size": "Standard_B2s",
                "location": "eastus",
                "tags": {"env": "dev"},
            },
            after_state={
                "size": "Standard_B2s",
                "location": "eastus",
                "tags": {"env": "prod"},
            },
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        cost = Decimal("0.0416") * MONTHLY_HOURS
        self.assertEqual(report.line_items[0].action, "update (metadata)")
        self.assertEqual(report.line_items[0].old_monthly, cost)
        self.assertEqual(report.line_items[0].new_monthly, cost)
        self.assertEqual(report.line_items[0].delta, Decimal("0"))
        self.assertEqual(report.line_items[0].pricing_status, "priced")
        self.assertEqual(report.net_delta, Decimal("0"))
        self.assertEqual(report.unpriced, 0)

    def test_metadata_only_update_unknown_sku(self):
        self.client.get_vm_price = MagicMock(return_value=None)
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["update"],
            before_state={
                "size": "Custom_Unknown_SKU",
                "location": "eastus",
                "tags": {"env": "dev"},
            },
            after_state={
                "size": "Custom_Unknown_SKU",
                "location": "eastus",
                "tags": {"env": "prod"},
            },
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(len(report.line_items), 1)
        self.assertEqual(report.line_items[0].action, "update (metadata)")
        self.assertEqual(report.line_items[0].pricing_status, "unpriced")
        self.assertEqual(report.line_items[0].old_monthly, Decimal("0"))
        self.assertEqual(report.line_items[0].new_monthly, Decimal("0"))
        self.assertEqual(report.line_items[0].delta, Decimal("0"))
        self.assertEqual(report.unpriced, 1)
        self.assertTrue(any("Custom_Unknown_SKU" in w for w in report.warnings))

    def test_metadata_only_update_unknown_region(self):
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["update"],
            before_state={
                "size": "Standard_B2s",
                "tags": {"env": "dev"},
            },
            after_state={
                "size": "Standard_B2s",
                "tags": {"env": "prod"},
            },
            after_unknown={"location": True},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(len(report.line_items), 1)
        self.assertEqual(report.line_items[0].pricing_status, "unpriced")
        self.assertEqual(report.unpriced, 1)
        self.assertTrue(any("Region" in w for w in report.warnings))

    def test_unknown_resource_types_are_skipped(self):
        changes = [
            ResourceChange(
                address="azurerm_resource_group.rg",
                resource_type="azurerm_resource_group",
                actions=["create"],
                before_state=None,
                after_state={"location": "eastus", "name": "test"},
            ),
            ResourceChange(
                address="azurerm_virtual_network.vnet",
                resource_type="azurerm_virtual_network",
                actions=["create"],
                before_state=None,
                after_state={"location": "eastus", "name": "test-vnet"},
            ),
        ]
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate(changes)
        self.assertEqual(len(report.line_items), 0)
        self.assertEqual(report.skipped, 2)
        self.assertEqual(report.net_delta, Decimal("0"))

    def test_unknown_sku_warning_and_safe_handling(self):
        self.client.get_vm_price = MagicMock(return_value=None)
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["create"],
            before_state=None,
            after_state={"size": "Custom_Unknown_SKU", "location": "eastus"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(len(report.line_items), 1)
        self.assertEqual(report.line_items[0].pricing_status, "unpriced")
        self.assertEqual(report.unpriced, 1)
        self.assertTrue(any("Custom_Unknown_SKU" in w for w in report.warnings))

    def test_unknown_region_safe_handling(self):
        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["create"],
            before_state=None,
            after_state={"size": "Standard_B2s"},
            after_unknown={"location": True},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(report.unpriced, 1)
        self.assertTrue(any("Region" in w for w in report.warnings))

    def test_cache_miss_calls_api_and_writes_result(self):
        mock_rec = PriceRecord(
            sku="Standard_B2s",
            region="eastus",
            currency="USD",
            resource_kind="vm",
            hourly_rate=Decimal("0.0416"),
            unit_of_measure="1 Hour",
        )
        self.client.get_vm_price = MagicMock(return_value=mock_rec)

        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["create"],
            before_state=None,
            after_state={"size": "Standard_B2s", "location": "eastus"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(report.api_lookups, 1)
        self.assertEqual(report.cache_hits, 0)
        self.client.get_vm_price.assert_called_once_with(
            "Standard_B2s", "eastus", "USD", "linux"
        )
        # Check SQLite was written
        cached = self.cache.lookup("Standard_B2s", "eastus", "USD", "vm")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.hourly_rate, Decimal("0.0416"))

    def test_cache_hit_prevents_api_call(self):
        self.cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        self.client.get_vm_price = MagicMock()

        change = ResourceChange(
            address="azurerm_linux_virtual_machine.test",
            resource_type="azurerm_linux_virtual_machine",
            actions=["create"],
            before_state=None,
            after_state={"size": "Standard_B2s", "location": "eastus"},
        )
        calc = CostCalculator(self.cache, self.client)
        report = calc.calculate([change])

        self.assertEqual(report.cache_hits, 1)
        self.assertEqual(report.api_lookups, 0)
        self.client.get_vm_price.assert_not_called()


if __name__ == "__main__":
    unittest.main()
