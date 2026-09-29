"""Tests for parser module (region normalization, action classification, identity extraction)."""

import unittest
from costguard.parser import (
    classify_action,
    extract_disk_identity,
    extract_vm_identity,
    get_resource_kind,
    normalize_region,
    parse_plan,
)


class TestParser(unittest.TestCase):
    def test_region_normalization(self):
        self.assertEqual(normalize_region("East US"), "eastus")
        self.assertEqual(normalize_region("West Europe"), "westeurope")
        self.assertEqual(normalize_region("South India"), "southindia")
        self.assertEqual(normalize_region("eastus2"), "eastus2")
        self.assertEqual(normalize_region("   Central  US  "), "centralus")
        self.assertEqual(normalize_region(""), "")

    def test_action_classification(self):
        self.assertEqual(classify_action(["no-op"]), "no-op")
        self.assertEqual(classify_action(["create"]), "create")
        self.assertEqual(classify_action(["delete"]), "delete")
        self.assertEqual(classify_action(["update"]), "update")
        self.assertEqual(classify_action(["delete", "create"]), "replace")
        self.assertEqual(classify_action(["create", "delete"]), "replace")
        self.assertEqual(classify_action(["read"]), "unknown")

    def test_get_resource_kind(self):
        self.assertEqual(get_resource_kind("azurerm_linux_virtual_machine"), "vm")
        self.assertEqual(get_resource_kind("azurerm_windows_virtual_machine"), "vm")
        self.assertEqual(get_resource_kind("azurerm_virtual_machine"), "vm")
        self.assertEqual(get_resource_kind("azurerm_managed_disk"), "managed_disk")
        self.assertIsNone(get_resource_kind("azurerm_resource_group"))
        self.assertIsNone(get_resource_kind("azurerm_virtual_network"))

    def test_extract_vm_identity(self):
        state = {"size": "Standard_B2s", "location": "East US"}
        sku, region = extract_vm_identity(state)
        self.assertEqual(sku, "Standard_B2s")
        self.assertEqual(region, "eastus")

        # vm_size fallback
        state2 = {"vm_size": "Standard_D2s_v3", "region": "West Europe"}
        sku2, region2 = extract_vm_identity(state2)
        self.assertEqual(sku2, "Standard_D2s_v3")
        self.assertEqual(region2, "westeurope")

        # unknown field
        after_unknown = {"size": True}
        sku3, region3 = extract_vm_identity(state, after_unknown)
        self.assertIsNone(sku3)
        self.assertIsNone(region3)

        # missing region
        state_no_reg = {"size": "Standard_B2s"}
        sku4, region4 = extract_vm_identity(state_no_reg)
        self.assertEqual(sku4, "Standard_B2s")
        self.assertIsNone(region4)

    def test_extract_disk_identity(self):
        state = {
            "storage_account_type": "Premium_LRS",
            "location": "East US",
            "disk_size_gb": 128,
        }
        st_type, region, size = extract_disk_identity(state)
        self.assertEqual(st_type, "Premium_LRS")
        self.assertEqual(region, "eastus")
        self.assertEqual(size, 128)

        # unknown storage account type
        st_type2, region2, size2 = extract_disk_identity(state, {"storage_account_type": True})
        self.assertIsNone(st_type2)
        self.assertIsNone(region2)
        self.assertIsNone(size2)

    def test_parse_plan_valid(self):
        plan = {
            "resource_changes": [
                {
                    "address": "azurerm_linux_virtual_machine.test",
                    "type": "azurerm_linux_virtual_machine",
                    "change": {
                        "actions": ["create"],
                        "before": None,
                        "after": {"size": "Standard_B2s", "location": "eastus"},
                    },
                }
            ]
        }
        changes = parse_plan(plan)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].address, "azurerm_linux_virtual_machine.test")
        self.assertEqual(changes[0].action_label, "create")

    def test_parse_plan_missing_resource_changes(self):
        with self.assertRaises(ValueError):
            parse_plan({})

    def test_parse_plan_invalid_resource_changes_type(self):
        with self.assertRaises(ValueError):
            parse_plan({"resource_changes": "not-a-list"})


if __name__ == "__main__":
    unittest.main()
