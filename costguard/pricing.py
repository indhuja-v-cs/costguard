"""Azure Retail Prices API client with validation and pagination."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import PriceRecord

# ---------------------------------------------------------------------------
# Managed-disk tier tables
# ---------------------------------------------------------------------------

DISK_TIERS: dict[str, list[tuple[int, str]]] = {
    "Premium_LRS": [
        (4, "P1"), (8, "P2"), (16, "P3"), (32, "P4"), (64, "P6"),
        (128, "P10"), (256, "P15"), (512, "P20"), (1024, "P30"),
        (2048, "P40"), (4096, "P50"), (8192, "P60"), (16384, "P70"),
        (32767, "P80"),
    ],
    "StandardSSD_LRS": [
        (4, "E1"), (8, "E2"), (16, "E3"), (32, "E4"), (64, "E6"),
        (128, "E10"), (256, "E15"), (512, "E20"), (1024, "E30"),
        (2048, "E40"), (4096, "E50"), (8192, "E60"), (16384, "E70"),
        (32767, "E80"),
    ],
    "Standard_LRS": [
        (32, "S4"), (64, "S6"), (128, "S10"), (256, "S15"), (512, "S20"),
        (1024, "S30"), (2048, "S40"), (4096, "S50"), (8192, "S60"),
        (16384, "S70"), (32767, "S80"),
    ],
}

STORAGE_TYPE_TO_PRODUCT: dict[str, str] = {
    "Premium_LRS": "Premium SSD Managed Disks",
    "StandardSSD_LRS": "Standard SSD Managed Disks",
    "Standard_LRS": "Standard HDD Managed Disks",
}

# ---------------------------------------------------------------------------
# Rejection terms
# ---------------------------------------------------------------------------

_REJECT_TERMS = frozenset(
    {"spot", "low priority", "low_priority", "reservation", "reserved", "devtest"}
)


def _contains_reject_term(text: str) -> bool:
    lower = text.lower()
    return any(t in lower for t in _REJECT_TERMS)


# ---------------------------------------------------------------------------
# Disk tier resolver
# ---------------------------------------------------------------------------


def resolve_disk_tier(
    storage_type: str, disk_size_gb: Optional[int]
) -> Optional[str]:
    """Map ``(storage_type, disk_size_gb)`` → tier name (e.g. ``"P10"``)."""
    tiers = DISK_TIERS.get(storage_type)
    if tiers is None or disk_size_gb is None:
        return None
    for max_gb, tier in tiers:
        if disk_size_gb <= max_gb:
            return tier
    return None


# ---------------------------------------------------------------------------
# Azure Retail Prices client
# ---------------------------------------------------------------------------


class AzurePricingClient:
    """Client for the public Azure Retail Prices REST API."""

    BASE_URL = "https://prices.azure.com/api/retail/prices"

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    # ---- VM pricing -------------------------------------------------------

    def get_vm_price(
        self,
        sku: str,
        region: str,
        currency: str,
        os_type: str = "linux",
    ) -> Optional[PriceRecord]:
        """Fetch validated Consumption/hourly VM pricing."""
        odata = (
            f"serviceName eq 'Virtual Machines'"
            f" and armRegionName eq '{region}'"
            f" and armSkuName eq '{sku}'"
            f" and priceType eq 'Consumption'"
        )
        items = self._fetch_all_pages(odata, currency)

        for item in items:
            if self._validate_vm_record(item, sku, region, currency, os_type):
                try:
                    rate = Decimal(str(item.get("retailPrice", 0)))
                except (InvalidOperation, TypeError):
                    continue
                if rate < 0:
                    continue
                return PriceRecord(
                    sku=sku,
                    region=region,
                    currency=currency,
                    resource_kind="vm",
                    hourly_rate=rate,
                    unit_of_measure=item.get("unitOfMeasure", ""),
                    meter_name=item.get("meterName", ""),
                    product_name=item.get("productName", ""),
                )
        return None

    # ---- Managed-disk pricing ---------------------------------------------

    def get_disk_price(
        self,
        storage_type: str,
        region: str,
        currency: str,
        disk_size_gb: Optional[int],
    ) -> Optional[PriceRecord]:
        """Fetch validated managed-disk pricing (monthly rate)."""
        tier = resolve_disk_tier(storage_type, disk_size_gb)
        if tier is None:
            return None
        product_fragment = STORAGE_TYPE_TO_PRODUCT.get(storage_type)
        if product_fragment is None:
            return None

        odata = (
            f"serviceName eq 'Storage'"
            f" and armRegionName eq '{region}'"
            f" and priceType eq 'Consumption'"
            f" and contains(productName, '{product_fragment}')"
        )
        items = self._fetch_all_pages(odata, currency)

        for item in items:
            if self._validate_disk_record(item, tier, region, currency):
                try:
                    rate = Decimal(str(item.get("retailPrice", 0)))
                except (InvalidOperation, TypeError):
                    continue
                if rate < 0:
                    continue
                return PriceRecord(
                    sku=f"{storage_type}/{tier}",
                    region=region,
                    currency=currency,
                    resource_kind="managed_disk",
                    hourly_rate=rate,  # monthly rate stored in same field
                    unit_of_measure=item.get("unitOfMeasure", ""),
                    meter_name=item.get("meterName", ""),
                    product_name=item.get("productName", ""),
                )
        return None

    # ---- HTTP layer -------------------------------------------------------

    def _fetch_all_pages(self, odata_filter: str, currency: str) -> list[dict]:
        params = urllib.parse.urlencode(
            {"$filter": odata_filter, "currencyCode": currency}
        )
        url: Optional[str] = f"{self.BASE_URL}?{params}"
        all_items: list[dict] = []

        while url:
            try:
                req = urllib.request.Request(url)
                req.add_header("Accept", "application/json")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                json.JSONDecodeError,
                TimeoutError,
                OSError,
            ) as exc:
                print(f"[WARN] Azure API request failed: {exc}", file=sys.stderr)
                break

            all_items.extend(data.get("Items", []))
            next_link = data.get("NextPageLink")
            url = next_link if isinstance(next_link, str) and next_link else None

        return all_items

    # ---- Validators -------------------------------------------------------

    @staticmethod
    def _validate_vm_record(
        item: dict,
        sku: str,
        region: str,
        currency: str,
        os_type: str,
    ) -> bool:
        if item.get("armSkuName") != sku:
            return False
        if item.get("armRegionName") != region:
            return False
        if item.get("currencyCode") != currency:
            return False

        price_type = item.get("priceType") or item.get("type", "")
        if price_type != "Consumption":
            return False

        unit = item.get("unitOfMeasure", "")
        if "Hour" not in unit:
            return False

        try:
            if Decimal(str(item.get("retailPrice", -1))) < 0:
                return False
        except (InvalidOperation, TypeError):
            return False

        meter = item.get("meterName", "")
        product = item.get("productName", "")
        sku_name = item.get("skuName", "")
        if _contains_reject_term(meter) or _contains_reject_term(product) or _contains_reject_term(sku_name):
            return False

        product_lower = product.lower()
        if os_type == "linux" and "windows" in product_lower:
            return False
        if os_type == "windows" and "linux" in product_lower:
            return False

        return True

    @staticmethod
    def _validate_disk_record(
        item: dict,
        tier: str,
        region: str,
        currency: str,
    ) -> bool:
        if item.get("armRegionName") != region:
            return False
        if item.get("currencyCode") != currency:
            return False

        price_type = item.get("priceType") or item.get("type", "")
        if price_type != "Consumption":
            return False

        unit = item.get("unitOfMeasure", "")
        if "Month" not in unit:
            return False

        # Tier must appear as an exact token in both skuName and meterName
        sku_name = item.get("skuName", "")
        meter_name = item.get("meterName", "")
        if tier not in sku_name.split():
            return False
        if tier not in meter_name.split():
            return False

        try:
            if Decimal(str(item.get("retailPrice", -1))) < 0:
                return False
        except (InvalidOperation, TypeError):
            return False

        product = item.get("productName", "")
        if _contains_reject_term(meter_name) or _contains_reject_term(product):
            return False

        return True
