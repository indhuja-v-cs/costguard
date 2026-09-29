"""Terraform plan JSON parser and resource-identity extractor."""

from __future__ import annotations

import re
from typing import Optional

from .models import ResourceChange

# ---------------------------------------------------------------------------
# Supported resource type registry
# ---------------------------------------------------------------------------

RESOURCE_REGISTRY: dict[str, str] = {
    "azurerm_linux_virtual_machine": "vm",
    "azurerm_windows_virtual_machine": "vm",
    "azurerm_virtual_machine": "vm",
    "azurerm_managed_disk": "managed_disk",
}

SKIP_RESOURCE_TYPES: set[str] = {
    "azurerm_resource_group",
    "azurerm_virtual_network",
    "azurerm_subnet",
    "azurerm_network_security_group",
    "azurerm_route_table",
    "azurerm_network_interface",
    "azurerm_public_ip",
    "azurerm_availability_set",
    "azurerm_proximity_placement_group",
    "azurerm_storage_account",
    "azurerm_key_vault",
    "azurerm_log_analytics_workspace",
    "azurerm_monitor_diagnostic_setting",
    "azurerm_user_assigned_identity",
    "azurerm_role_assignment",
    "azurerm_dns_zone",
    "azurerm_private_dns_zone",
    "azurerm_application_security_group",
    "azurerm_network_watcher",
    "azurerm_nat_gateway",
    "azurerm_bastion_host",
    "azurerm_private_endpoint",
    "azurerm_private_dns_zone_virtual_network_link",
}


def normalize_region(region: str) -> str:
    """Normalize an Azure region name.

    ``"East US"`` → ``"eastus"``, ``"West Europe"`` → ``"westeurope"``, etc.
    """
    if not region:
        return ""
    return re.sub(r"\s+", "", region).lower()


def classify_action(actions: list[str]) -> str:
    """Map a Terraform change *actions* array to a single action label.

    Returns one of: ``"no-op"``, ``"create"``, ``"delete"``, ``"update"``,
    ``"replace"``, ``"unknown"``.
    """
    t = tuple(actions)
    if t == ("no-op",):
        return "no-op"
    if t == ("create",):
        return "create"
    if t == ("delete",):
        return "delete"
    if t == ("update",):
        return "update"
    if t in (("delete", "create"), ("create", "delete")):
        return "replace"
    return "unknown"


def get_resource_kind(resource_type: str) -> Optional[str]:
    """Return ``"vm"`` / ``"managed_disk"`` for supported types, else ``None``."""
    return RESOURCE_REGISTRY.get(resource_type)


def _field_is_unknown(after_unknown: Optional[dict], *keys: str) -> bool:
    """Return ``True`` if any of *keys* are marked ``true`` in *after_unknown*."""
    if after_unknown is None:
        return False
    return any(after_unknown.get(k) is True for k in keys)


def extract_vm_identity(
    state: Optional[dict],
    after_unknown: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Extract ``(sku, normalized_region)`` from a VM state dict.

    Returns ``(None, …)`` when the SKU is missing/unknown and
    ``(…, None)`` when the region is missing/unknown.
    """
    if state is None:
        return None, None

    sku = state.get("size") or state.get("vm_size")
    if not sku or _field_is_unknown(after_unknown, "size", "vm_size"):
        return None, None

    region_raw = state.get("location") or state.get("region") or ""
    if not region_raw or _field_is_unknown(after_unknown, "location", "region"):
        return sku, None

    return sku, normalize_region(region_raw)


def extract_disk_identity(
    state: Optional[dict],
    after_unknown: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Extract ``(storage_account_type, normalized_region, disk_size_gb)``
    from a managed-disk state dict.
    """
    if state is None:
        return None, None, None

    storage_type = state.get("storage_account_type")
    if not storage_type or _field_is_unknown(after_unknown, "storage_account_type"):
        return None, None, None

    region_raw = state.get("location") or state.get("region") or ""
    if not region_raw or _field_is_unknown(after_unknown, "location", "region"):
        return storage_type, None, None
    region = normalize_region(region_raw)

    disk_size_gb: Optional[int] = None
    raw_size = state.get("disk_size_gb")
    if raw_size is not None and not _field_is_unknown(after_unknown, "disk_size_gb"):
        try:
            disk_size_gb = int(raw_size)
        except (ValueError, TypeError):
            disk_size_gb = None

    return storage_type, region, disk_size_gb


def parse_plan(plan_json: dict) -> list[ResourceChange]:
    """Parse a Terraform JSON execution plan into ``ResourceChange`` objects.

    Raises ``ValueError`` when the plan structure is invalid.
    """
    if "resource_changes" not in plan_json:
        raise ValueError(
            "Invalid Terraform plan: 'resource_changes' key is missing"
        )

    resource_changes = plan_json["resource_changes"]
    if not isinstance(resource_changes, list):
        raise ValueError(
            "Invalid Terraform plan: 'resource_changes' must be a list"
        )

    results: list[ResourceChange] = []
    for rc in resource_changes:
        if not isinstance(rc, dict):
            continue
        change = rc.get("change", {})
        results.append(
            ResourceChange(
                address=rc.get("address", "unknown"),
                resource_type=rc.get("type", ""),
                actions=change.get("actions", []),
                before_state=change.get("before"),
                after_state=change.get("after"),
                after_unknown=change.get("after_unknown"),
            )
        )

    return results
