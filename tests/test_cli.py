"""Tests for CLI module (exit codes, flags, budget enforcement, fail-closed)."""

import io
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from costguard.cache import PricingCache
from costguard.cli import main
from costguard.models import PriceRecord


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()

    def tearDown(self):
        if os.path.exists(self.tmp_db.name):
            os.remove(self.tmp_db.name)

    def _seed_cache(self):
        cache = PricingCache(self.tmp_db.name)
        # B2s VM
        cache.store(
            PriceRecord(
                sku="Standard_B2s",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.0416"),
                unit_of_measure="1 Hour",
            )
        )
        # D4s_v3 VM
        cache.store(
            PriceRecord(
                sku="Standard_D4s_v3",
                region="eastus",
                currency="USD",
                resource_kind="vm",
                hourly_rate=Decimal("0.192"),
                unit_of_measure="1 Hour",
            )
        )
        # P10 Managed Disk
        cache.store(
            PriceRecord(
                sku="Premium_LRS/P10",
                region="eastus",
                currency="USD",
                resource_kind="managed_disk",
                hourly_rate=Decimal("19.71"),
                unit_of_measure="1 Month",
            )
        )
        cache.close()

    def test_invalid_max_increase_negative(self):
        code = main(["--max-increase", "-10", "--plan", "test-plans/create_vm.json"])
        self.assertEqual(code, 2)

    def test_invalid_max_increase_non_numeric(self):
        code = main(["--max-increase", "abc", "--plan", "test-plans/create_vm.json"])
        self.assertEqual(code, 2)

    def test_invalid_currency(self):
        code = main(["--max-increase", "50", "--currency", "XYZ", "--plan", "test-plans/create_vm.json"])
        self.assertEqual(code, 2)

    def test_invalid_json_plan(self):
        tmp_bad = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp_bad.write("not-valid-json { [")
        tmp_bad.close()
        try:
            code = main(["--max-increase", "50", "--plan", tmp_bad.name])
            self.assertEqual(code, 2)
        finally:
            os.remove(tmp_bad.name)

    def test_missing_input_no_stdin(self):
        with patch("sys.stdin.isatty", return_value=True):
            code = main(["--max-increase", "50"])
            self.assertEqual(code, 2)

    def test_budget_pass(self):
        self._seed_cache()
        # create_vm: B2s VM (0.0416 * 730 = 30.368) + P10 Disk (19.71) = 50.078
        # max-increase 100 -> pass
        code = main([
            "--plan", "test-plans/create_vm.json",
            "--max-increase", "100",
            "--cache-path", self.tmp_db.name,
        ])
        self.assertEqual(code, 0)

    def test_budget_breach(self):
        self._seed_cache()
        # max-increase 10 -> breach (cost delta is ~50.08)
        code = main([
            "--plan", "test-plans/create_vm.json",
            "--max-increase", "10",
            "--cache-path", self.tmp_db.name,
        ])
        self.assertEqual(code, 1)

    def test_negative_delta_passes_with_zero_threshold(self):
        self._seed_cache()
        # delete_vm produces negative delta (-50.08)
        code = main([
            "--plan", "test-plans/delete_vm.json",
            "--max-increase", "0",
            "--cache-path", self.tmp_db.name,
        ])
        self.assertEqual(code, 0)

    def test_metadata_update_zero_delta(self):
        self._seed_cache()
        code = main([
            "--plan", "test-plans/metadata_update.json",
            "--max-increase", "0",
            "--cache-path", self.tmp_db.name,
        ])
        self.assertEqual(code, 0)

    def test_metadata_update_unknown_sku_fails_closed_by_default(self):
        # Metadata-only update with unknown SKU
        plan = {
            "resource_changes": [
                {
                    "address": "azurerm_linux_virtual_machine.meta",
                    "type": "azurerm_linux_virtual_machine",
                    "change": {
                        "actions": ["update"],
                        "before": {"size": "Custom_Unknown_SKU", "location": "eastus", "tags": {"env": "dev"}},
                        "after": {"size": "Custom_Unknown_SKU", "location": "eastus", "tags": {"env": "prod"}},
                    },
                }
            ]
        }
        tmp_plan = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(plan, tmp_plan)
        tmp_plan.close()
        try:
            with patch("costguard.pricing.AzurePricingClient.get_vm_price", return_value=None):
                code = main([
                    "--plan", tmp_plan.name,
                    "--max-increase", "10000",
                    "--cache-path", self.tmp_db.name,
                ])
                # Must fail closed with exit code 1 even with high budget
                self.assertEqual(code, 1)
        finally:
            os.remove(tmp_plan.name)

    def test_metadata_update_unknown_sku_with_allow_unpriced(self):
        plan = {
            "resource_changes": [
                {
                    "address": "azurerm_linux_virtual_machine.meta",
                    "type": "azurerm_linux_virtual_machine",
                    "change": {
                        "actions": ["update"],
                        "before": {"size": "Custom_Unknown_SKU", "location": "eastus", "tags": {"env": "dev"}},
                        "after": {"size": "Custom_Unknown_SKU", "location": "eastus", "tags": {"env": "prod"}},
                    },
                }
            ]
        }
        tmp_plan = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(plan, tmp_plan)
        tmp_plan.close()
        try:
            with patch("costguard.pricing.AzurePricingClient.get_vm_price", return_value=None):
                code = main([
                    "--plan", tmp_plan.name,
                    "--max-increase", "10000",
                    "--allow-unpriced",
                    "--cache-path", self.tmp_db.name,
                ])
                # Passes with exit code 0 when --allow-unpriced is provided
                self.assertEqual(code, 0)
        finally:
            os.remove(tmp_plan.name)

    def test_metadata_update_unknown_region_fails_closed(self):
        plan = {
            "resource_changes": [
                {
                    "address": "azurerm_linux_virtual_machine.meta",
                    "type": "azurerm_linux_virtual_machine",
                    "change": {
                        "actions": ["update"],
                        "before": {"size": "Standard_B2s", "tags": {"env": "dev"}},
                        "after": {"size": "Standard_B2s", "tags": {"env": "prod"}},
                        "after_unknown": {"location": True},
                    },
                }
            ]
        }
        tmp_plan = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        json.dump(plan, tmp_plan)
        tmp_plan.close()
        try:
            code = main([
                "--plan", tmp_plan.name,
                "--max-increase", "10000",
                "--cache-path", self.tmp_db.name,
            ])
            # Must fail closed with exit code 1
            self.assertEqual(code, 1)
        finally:
            os.remove(tmp_plan.name)

    def test_noise_plan_produces_zero_impact_and_no_crash(self):
        self._seed_cache()
        code = main([
            "--plan", "test-plans/noise_plan.json",
            "--max-increase", "0",
            "--cache-path", self.tmp_db.name,
        ])
        self.assertEqual(code, 0)

    def test_unpriced_fail_closed_by_default(self):
        # unknown_sku.json has an unpriced SKU
        with patch("costguard.pricing.AzurePricingClient.get_vm_price", return_value=None):
            code = main([
                "--plan", "test-plans/unknown_sku.json",
                "--max-increase", "1000",
                "--cache-path", self.tmp_db.name,
            ])
            # Even with huge max-increase, unpriced billable resource causes exit 1
            self.assertEqual(code, 1)

    def test_allow_unpriced_override(self):
        with patch("costguard.pricing.AzurePricingClient.get_vm_price", return_value=None):
            code = main([
                "--plan", "test-plans/unknown_sku.json",
                "--max-increase", "1000",
                "--allow-unpriced",
                "--cache-path", self.tmp_db.name,
            ])
            # With --allow-unpriced, since net delta of priced items is 0 <= 1000, passes
            self.assertEqual(code, 0)

    def test_clear_cache_flag(self):
        self._seed_cache()
        cache = PricingCache(self.tmp_db.name)
        self.assertGreater(cache.count(), 0)
        cache.close()

        with patch("costguard.pricing.AzurePricingClient.get_vm_price", return_value=None):
            main([
                "--plan", "test-plans/noise_plan.json",
                "--max-increase", "10",
                "--cache-path", self.tmp_db.name,
                "--clear-cache",
            ])
        cache = PricingCache(self.tmp_db.name)
        self.assertEqual(cache.count(), 0)
        cache.close()

    def test_stdin_input(self):
        self._seed_cache()
        with open("test-plans/create_vm.json", "r", encoding="utf-8") as f:
            plan_content = f.read()

        with patch("sys.stdin", io.StringIO(plan_content)):
            with patch("sys.stdin.isatty", return_value=False):
                code = main([
                    "--max-increase", "100",
                    "--cache-path", self.tmp_db.name,
                ])
                self.assertEqual(code, 0)

    def test_json_output(self):
        self._seed_cache()
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main([
                "--plan", "test-plans/create_vm.json",
                "--max-increase", "100",
                "--cache-path", self.tmp_db.name,
                "--json",
            ])
        self.assertEqual(code, 0)
        output_str = captured.getvalue()
        parsed = json.loads(output_str)
        self.assertIn("line_items", parsed)
        self.assertIn("summary", parsed)
        self.assertIn("policy", parsed)
        self.assertTrue(parsed["policy"]["passed"])

    def test_markdown_output(self):
        self._seed_cache()
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main([
                "--plan", "test-plans/create_vm.json",
                "--max-increase", "100",
                "--cache-path", self.tmp_db.name,
                "--markdown",
            ])
        self.assertEqual(code, 0)
        output_str = captured.getvalue()
        self.assertIn("## CostGuard: Azure Infrastructure Cost Impact Report", output_str)
        self.assertIn("| Resource | Action |", output_str)


if __name__ == "__main__":
    unittest.main()
