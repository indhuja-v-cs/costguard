"""Output formatters for CostGuard (text, JSON, Markdown)."""

from __future__ import annotations

import json as _json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import CostReport

_TWO_PLACES = Decimal("0.01")


def _fmt(amount: Decimal, symbol: str = "") -> str:
    """Format a Decimal to two-place currency string."""
    rounded = amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if rounded >= 0:
        return f"{symbol}{rounded:,.2f}"
    return f"-{symbol}{abs(rounded):,.2f}"


def _delta_fmt(amount: Decimal, symbol: str = "") -> str:
    """Like ``_fmt`` but with explicit ``+`` / ``-`` sign."""
    rounded = amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if rounded > 0:
        return f"+{symbol}{rounded:,.2f}"
    if rounded < 0:
        return f"-{symbol}{abs(rounded):,.2f}"
    return f"{symbol}0.00"


def _trunc(s: str, width: int) -> str:
    return s if len(s) <= width else s[: width - 2] + ".."


def _currency_symbol(code: str) -> str:
    return {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(code, "")


# -----------------------------------------------------------------------
# Text output
# -----------------------------------------------------------------------

def format_text(report: CostReport, budget: Decimal, passed: bool) -> str:
    sym = _currency_symbol(report.currency)
    lines: list[str] = []

    lines.append("")
    lines.append("=" * 78)
    lines.append("  COSTGUARD: Azure Infrastructure Cost Impact Report")
    lines.append("=" * 78)
    lines.append("")

    # --- warnings first ---
    for w in report.warnings:
        lines.append(f"  {w}")
    if report.warnings:
        lines.append("")

    # --- table ---
    hdr = (
        f"  {'Resource':<40} {'Action':<12} {'Region':<12} "
        f"{'SKU':<20} {'Old':<12} {'New':<12} {'Delta':<12} {'Status':<8}"
    )
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))

    for item in report.line_items:
        addr = _trunc(item.address, 38)
        act = _trunc(item.action, 10)
        reg = _trunc(item.region, 10)
        sku = _trunc(item.sku, 18)
        old_s = _fmt(item.old_monthly, sym)
        new_s = _fmt(item.new_monthly, sym)
        dlt_s = _delta_fmt(item.delta, sym)
        st = item.pricing_status
        lines.append(
            f"  {addr:<40} {act:<12} {reg:<12} {sku:<20} "
            f"{old_s:<12} {new_s:<12} {dlt_s:<12} {st:<8}"
        )

    lines.append("")
    lines.append("-" * 78)
    lines.append("  FINANCIAL SUMMARY")
    lines.append("-" * 78)
    non_usd = report.currency != "USD"
    note = " (estimated reference price)" if non_usd else ""
    lines.append(f"  Prior Monthly Total:      {_fmt(report.prior_total, sym)}{note}")
    lines.append(f"  Projected Monthly Total:  {_fmt(report.projected_total, sym)}{note}")
    lines.append(f"  Net Monthly Impact:       {_delta_fmt(report.net_delta, sym)}{note}")
    lines.append(f"  Currency:                 {report.currency}")
    lines.append(f"  Monthly baseline:         730 hours")
    lines.append(f"  Cache Hits:               {report.cache_hits}")
    lines.append(f"  API Lookups:              {report.api_lookups}")
    lines.append(f"  Skipped Resources:        {report.skipped}")
    lines.append(f"  Warnings:                 {len(report.warnings)}")
    lines.append(f"  Unpriced Resources:       {report.unpriced}")
    lines.append("")

    lines.append("-" * 78)
    lines.append("  POLICY VERDICT")
    lines.append("-" * 78)
    lines.append(f"  Budget Threshold:         {_fmt(budget, sym)}")
    if passed:
        lines.append(f"  Status:                   PASSED")
    else:
        lines.append(f"  Status:                   FAILED")
        lines.append("")
        lines.append(
            "  [CIRCUIT BREAKER] CostGuard: Budget threshold breached. "
            "Deployment blocked."
        )

    lines.append("")
    lines.append(
        "  * Costs are estimated monthly retail prices based on 730 hours."
    )
    lines.append(
        "  * This is NOT a complete Azure invoice prediction."
    )
    lines.append("")
    return "\n".join(lines)


# -----------------------------------------------------------------------
# JSON output
# -----------------------------------------------------------------------

def format_json(report: CostReport, budget: Decimal, passed: bool) -> str:
    def _d(v: Decimal) -> str:
        return str(v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))

    items = [
        {
            "address": i.address,
            "action": i.action,
            "resource_type": i.resource_type,
            "region": i.region,
            "sku": i.sku,
            "old_monthly": _d(i.old_monthly),
            "new_monthly": _d(i.new_monthly),
            "delta": _d(i.delta),
            "pricing_status": i.pricing_status,
        }
        for i in report.line_items
    ]

    doc: dict[str, Any] = {
        "report": "CostGuard Azure Infrastructure Cost Impact",
        "line_items": items,
        "summary": {
            "prior_total": _d(report.prior_total),
            "projected_total": _d(report.projected_total),
            "net_delta": _d(report.net_delta),
            "currency": report.currency,
            "monthly_baseline_hours": 730,
            "cache_hits": report.cache_hits,
            "api_lookups": report.api_lookups,
            "skipped_resources": report.skipped,
            "warnings": report.warnings,
            "unpriced_resources": report.unpriced,
        },
        "policy": {
            "budget_threshold": _d(budget),
            "passed": passed,
        },
    }
    return _json.dumps(doc, indent=2)


# -----------------------------------------------------------------------
# Markdown output
# -----------------------------------------------------------------------

def format_markdown(report: CostReport, budget: Decimal, passed: bool) -> str:
    sym = _currency_symbol(report.currency)
    lines: list[str] = []

    lines.append("## CostGuard: Azure Infrastructure Cost Impact Report")
    lines.append("")

    if report.warnings:
        for w in report.warnings:
            lines.append(f"> [WARN] {w}")
        lines.append("")

    lines.append(
        "| Resource | Action | Region | SKU | Old | New | Delta | Status |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for i in report.line_items:
        lines.append(
            f"| `{_trunc(i.address, 50)}` "
            f"| {i.action} "
            f"| {i.region} "
            f"| `{i.sku}` "
            f"| {_fmt(i.old_monthly, sym)} "
            f"| {_fmt(i.new_monthly, sym)} "
            f"| {_delta_fmt(i.delta, sym)} "
            f"| {i.pricing_status} |"
        )

    lines.append("")
    lines.append("### Financial Summary")
    lines.append("")
    non_usd = report.currency != "USD"
    note = " *(estimated reference price)*" if non_usd else ""
    lines.append(f"- **Prior Monthly Total:** {_fmt(report.prior_total, sym)}{note}")
    lines.append(f"- **Projected Monthly Total:** {_fmt(report.projected_total, sym)}{note}")
    lines.append(f"- **Net Monthly Impact:** {_delta_fmt(report.net_delta, sym)}{note}")
    lines.append(f"- **Currency:** {report.currency}")
    lines.append(f"- **Monthly baseline:** 730 hours")
    lines.append(f"- Cache Hits: {report.cache_hits}")
    lines.append(f"- API Lookups: {report.api_lookups}")
    lines.append(f"- Skipped: {report.skipped}")
    lines.append(f"- Unpriced: {report.unpriced}")

    lines.append("")
    lines.append("### Policy Verdict")
    lines.append("")
    verdict = "**PASSED**" if passed else "**FAILED**"
    lines.append(f"- Budget Threshold: {_fmt(budget, sym)}")
    lines.append(f"- Status: {verdict}")

    if not passed:
        lines.append("")
        lines.append(
            "> **[CIRCUIT BREAKER]** CostGuard: Budget threshold breached. "
            "Deployment blocked."
        )

    lines.append("")
    lines.append(
        "*Costs are estimated monthly retail prices based on 730 hours. "
        "This is NOT a complete Azure invoice prediction.*"
    )
    lines.append("")
    return "\n".join(lines)
