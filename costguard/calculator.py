"""Cost calculation engine for CostGuard."""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Optional

from .cache import PricingCache
from .models import CostLineItem, CostReport, PriceRecord, ResourceChange
from .parser import (
    classify_action,
    extract_disk_identity,
    extract_vm_identity,
    get_resource_kind,
    normalize_region,
)
from .pricing import AzurePricingClient, resolve_disk_tier

MONTHLY_HOURS = Decimal("730")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_os_type(resource_type: str, state: Optional[dict] = None) -> str:
    """Infer ``"linux"`` or ``"windows"`` from the resource type / state."""
    if resource_type == "azurerm_windows_virtual_machine":
        return "windows"
    if resource_type == "azurerm_linux_virtual_machine":
        return "linux"
    if resource_type == "azurerm_virtual_machine" and state:
        if state.get("os_profile_windows_config") is not None:
            return "windows"
    return "linux"


def _is_metadata_only_update(
    before: Optional[dict],
    after: Optional[dict],
    resource_kind: str,
) -> bool:
    """Return ``True`` when cost-relevant fields are unchanged."""
    if before is None or after is None:
        return False

    if resource_kind == "vm":
        b_sku = before.get("size") or before.get("vm_size")
        a_sku = after.get("size") or after.get("vm_size")
        b_reg = normalize_region(before.get("location", "") or before.get("region", ""))
        a_reg = normalize_region(after.get("location", "") or after.get("region", ""))
        return b_sku == a_sku and b_reg == a_reg

    if resource_kind == "managed_disk":
        return (
            before.get("storage_account_type") == after.get("storage_account_type")
            and before.get("disk_size_gb") == after.get("disk_size_gb")
            and normalize_region(before.get("location", "") or before.get("region", ""))
            == normalize_region(after.get("location", "") or after.get("region", ""))
        )

    return False


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class CostCalculator:
    """Compute the projected monthly cost delta for a set of resource changes."""

    def __init__(
        self,
        cache: PricingCache,
        pricing_client: AzurePricingClient,
        currency: str = "USD",
        allow_unpriced: bool = False,
    ) -> None:
        self.cache = cache
        self.client = pricing_client
        self.currency = currency
        self.allow_unpriced = allow_unpriced

        self._cache_hits = 0
        self._api_lookups = 0
        self._skipped = 0
        self._warnings: list[str] = []
        self._unpriced = 0

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    def calculate(self, changes: list[ResourceChange]) -> CostReport:
        items: list[CostLineItem] = []
        for ch in changes:
            item = self._process(ch)
            if item is not None:
                items.append(item)

        prior = sum(
            (i.old_monthly for i in items if i.pricing_status == "priced"),
            Decimal("0"),
        )
        projected = sum(
            (i.new_monthly for i in items if i.pricing_status == "priced"),
            Decimal("0"),
        )

        return CostReport(
            line_items=items,
            prior_total=prior,
            projected_total=projected,
            net_delta=projected - prior,
            currency=self.currency,
            cache_hits=self._cache_hits,
            api_lookups=self._api_lookups,
            skipped=self._skipped,
            warnings=list(self._warnings),
            unpriced=self._unpriced,
        )

    # ------------------------------------------------------------------ #
    # Per-resource processing
    # ------------------------------------------------------------------ #

    def _process(self, ch: ResourceChange) -> Optional[CostLineItem]:
        kind = get_resource_kind(ch.resource_type)
        if kind is None:
            self._skipped += 1
            return None

        action = classify_action(ch.actions)
        if action == "no-op":
            return None
        if action == "unknown":
            self._warnings.append(
                f"[WARN] Unknown action {ch.actions} for {ch.address}. Skipping."
            )
            self._skipped += 1
            return None

        # ---- metadata-only update → delta ≡ 0 ----
        if action == "update" and _is_metadata_only_update(
            ch.before_state, ch.after_state, kind
        ):
            cost, sku, region, status = self._cost_for(
                ch.resource_type, ch.before_state, None, kind
            )
            if cost is None:
                self._unpriced += 1
                return CostLineItem(
                    address=ch.address,
                    action="update (metadata)",
                    resource_type=ch.resource_type,
                    region=region,
                    sku=sku,
                    old_monthly=Decimal("0"),
                    new_monthly=Decimal("0"),
                    delta=Decimal("0"),
                    pricing_status="unpriced",
                )
            return CostLineItem(
                address=ch.address,
                action="update (metadata)",
                resource_type=ch.resource_type,
                region=region,
                sku=sku,
                old_monthly=cost,
                new_monthly=cost,
                delta=Decimal("0"),
                pricing_status="priced",
            )

        # ---- cost-impacting actions ----
        old = new = Decimal("0")
        sku = region = ""
        status = "priced"

        if action == "create":
            val, sku, region, st = self._cost_for(
                ch.resource_type, ch.after_state, ch.after_unknown, kind
            )
            if val is None:
                status = "unpriced"
                self._unpriced += 1
            else:
                new = val
                status = st

        elif action == "delete":
            val, sku, region, st = self._cost_for(
                ch.resource_type, ch.before_state, None, kind
            )
            if val is None:
                status = "unpriced"
                self._unpriced += 1
            else:
                old = val
                status = st

        elif action in ("update", "replace"):
            oval, osku, oreg, ost = self._cost_for(
                ch.resource_type, ch.before_state, None, kind
            )
            nval, nsku, nreg, nst = self._cost_for(
                ch.resource_type, ch.after_state, ch.after_unknown, kind
            )
            sku = nsku or osku
            region = nreg or oreg
            if oval is None or nval is None:
                status = "unpriced"
                self._unpriced += 1
                old = oval or Decimal("0")
                new = nval or Decimal("0")
            else:
                old, new, status = oval, nval, "priced"

        return CostLineItem(
            address=ch.address,
            action=action,
            resource_type=ch.resource_type,
            region=region,
            sku=sku,
            old_monthly=old,
            new_monthly=new,
            delta=new - old,
            pricing_status=status,
        )

    # ------------------------------------------------------------------ #
    # Price resolution (cache → API → store)
    # ------------------------------------------------------------------ #

    def _cost_for(
        self,
        resource_type: str,
        state: Optional[dict],
        after_unknown: Optional[dict],
        kind: str,
    ) -> tuple[Optional[Decimal], str, str, str]:
        """Return ``(monthly_cost | None, sku, region, status)``."""
        if kind == "vm":
            return self._vm_cost(resource_type, state, after_unknown)
        if kind == "managed_disk":
            return self._disk_cost(state, after_unknown)
        return None, "", "", "unpriced"

    def _vm_cost(
        self,
        resource_type: str,
        state: Optional[dict],
        after_unknown: Optional[dict],
    ) -> tuple[Optional[Decimal], str, str, str]:
        sku, region = extract_vm_identity(state, after_unknown)

        if not sku:
            self._warnings.append(
                f"[WARN] SKU for {resource_type} is unknown. Skipping."
            )
            return None, "", "", "unpriced"
        if not region:
            self._warnings.append(
                f"[WARN] Region for {resource_type} is unknown. Skipping."
            )
            return None, sku, "", "unpriced"

        os_type = _detect_os_type(resource_type, state)

        # cache check
        rec = self.cache.lookup(sku, region, self.currency, "vm")
        if rec is not None:
            self._cache_hits += 1
            return rec.hourly_rate * MONTHLY_HOURS, sku, region, "priced"

        # API call
        self._api_lookups += 1
        rec = self.client.get_vm_price(sku, region, self.currency, os_type)
        if rec is None:
            self._warnings.append(
                f"[WARN] SKU '{sku}' was not found in the Azure Retail Prices API. Skipping."
            )
            return None, sku, region, "unpriced"

        self.cache.store(rec)
        return rec.hourly_rate * MONTHLY_HOURS, sku, region, "priced"

    def _disk_cost(
        self,
        state: Optional[dict],
        after_unknown: Optional[dict],
    ) -> tuple[Optional[Decimal], str, str, str]:
        storage_type, region, disk_gb = extract_disk_identity(state, after_unknown)

        if not storage_type:
            self._warnings.append(
                "[WARN] Storage type for managed disk is unknown. Skipping."
            )
            return None, "", "", "unpriced"
        if not region:
            self._warnings.append(
                "[WARN] Region for managed disk is unknown. Skipping."
            )
            return None, storage_type or "", "", "unpriced"
        if disk_gb is None:
            self._warnings.append(
                "[WARN] Disk size for managed disk is unknown. Cannot determine tier. Skipping."
            )
            return None, storage_type, region, "unpriced"

        tier = resolve_disk_tier(storage_type, disk_gb)
        if tier is None:
            self._warnings.append(
                f"[WARN] Cannot map disk size {disk_gb}GB with type "
                f"'{storage_type}' to a tier. Skipping."
            )
            return None, storage_type, region, "unpriced"

        cache_sku = f"{storage_type}/{tier}"

        rec = self.cache.lookup(cache_sku, region, self.currency, "managed_disk")
        if rec is not None:
            self._cache_hits += 1
            return rec.hourly_rate, cache_sku, region, "priced"

        self._api_lookups += 1
        rec = self.client.get_disk_price(storage_type, region, self.currency, disk_gb)
        if rec is None:
            self._warnings.append(
                f"[WARN] Managed disk '{cache_sku}' pricing not found in Azure API. Skipping."
            )
            return None, cache_sku, region, "unpriced"

        self.cache.store(rec)
        return rec.hourly_rate, cache_sku, region, "priced"
