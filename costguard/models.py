"""Data models for CostGuard."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class ResourceChange:
    """A single resource change extracted from a Terraform plan."""

    address: str
    resource_type: str
    actions: list[str]
    before_state: Optional[dict]
    after_state: Optional[dict]
    after_unknown: Optional[dict] = None

    @property
    def action_label(self) -> str:
        """Human-readable action label."""
        actions_tuple = tuple(self.actions)
        mapping = {
            ("no-op",): "no-op",
            ("create",): "create",
            ("delete",): "delete",
            ("update",): "update",
            ("delete", "create"): "replace",
            ("create", "delete"): "replace",
        }
        return mapping.get(actions_tuple, "unknown")


@dataclass
class PriceRecord:
    """A validated price record from Azure or the local cache.

    For VMs, ``hourly_rate`` is the per-hour retail price.
    For managed disks, ``hourly_rate`` stores the per-month retail price
    (the field name is kept for cache-schema compatibility).
    """

    sku: str
    region: str
    currency: str
    resource_kind: str
    hourly_rate: Decimal
    unit_of_measure: str
    meter_name: str = ""
    product_name: str = ""


@dataclass
class CostLineItem:
    """One row in the cost-impact report."""

    address: str
    action: str
    resource_type: str
    region: str
    sku: str
    old_monthly: Decimal
    new_monthly: Decimal
    delta: Decimal
    pricing_status: str  # "priced" | "unpriced" | "skipped"
    warning: str = ""


@dataclass
class CostReport:
    """Aggregated cost-impact report."""

    line_items: list[CostLineItem] = field(default_factory=list)
    prior_total: Decimal = Decimal("0")
    projected_total: Decimal = Decimal("0")
    net_delta: Decimal = Decimal("0")
    currency: str = "USD"
    cache_hits: int = 0
    api_lookups: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    unpriced: int = 0
