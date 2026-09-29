"""Command-line interface for CostGuard."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from .cache import PricingCache
from .calculator import CostCalculator
from .formatter import format_json, format_markdown, format_text
from .parser import parse_plan
from .pricing import AzurePricingClient

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "INR"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="costguard",
        description=(
            "Analyze a Terraform JSON execution plan and estimate the "
            "projected monthly Azure infrastructure cost impact."
        ),
    )
    p.add_argument(
        "--plan",
        metavar="PATH",
        help="Path to a Terraform JSON plan file. Reads stdin if omitted.",
    )
    p.add_argument(
        "--max-increase",
        metavar="AMOUNT",
        required=True,
        help="Maximum permitted positive monthly cost increase (Decimal).",
    )
    p.add_argument(
        "--currency",
        metavar="CODE",
        default="USD",
        help="Currency code: USD (default), EUR, GBP, INR.",
    )
    p.add_argument(
        "--cache-path",
        metavar="PATH",
        default="pricing_cache.db",
        help="SQLite cache file path (default: pricing_cache.db).",
    )
    p.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete all cached pricing records before processing.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Emit Markdown table output.",
    )
    p.add_argument(
        "--allow-unpriced",
        action="store_true",
        help=(
            "Do not fail closed when a billable resource cannot be priced."
        ),
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point.  Returns an exit code (0, 1, or 2)."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    # --- validate --max-increase ---
    try:
        budget = Decimal(args.max_increase)
    except (InvalidOperation, TypeError, ValueError):
        print(
            f"[ERROR] Invalid --max-increase value: {args.max_increase!r}",
            file=sys.stderr,
        )
        return 2

    if budget < 0:
        print(
            "[ERROR] --max-increase must be non-negative.",
            file=sys.stderr,
        )
        return 2

    # --- validate --currency ---
    currency = args.currency.upper()
    if currency not in SUPPORTED_CURRENCIES:
        print(
            f"[ERROR] Unsupported currency: {currency}. "
            f"Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}",
            file=sys.stderr,
        )
        return 2

    # --- read plan ---
    raw: str
    if args.plan:
        try:
            with open(args.plan, "r", encoding="utf-8-sig") as fh:
                raw = fh.read()
        except (OSError, IOError) as exc:
            print(f"[ERROR] Cannot read plan file: {exc}", file=sys.stderr)
            return 2
    else:
        if sys.stdin.isatty():
            print(
                "[ERROR] No plan provided. Use --plan PATH or pipe JSON via stdin.",
                file=sys.stderr,
            )
            return 2
        raw = sys.stdin.read()

    # --- parse JSON ---
    try:
        plan_data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Invalid JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(plan_data, dict):
        print("[ERROR] Plan must be a JSON object.", file=sys.stderr)
        return 2

    # --- parse plan ---
    try:
        changes = parse_plan(plan_data)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    # --- set up cache ---
    try:
        cache = PricingCache(args.cache_path)
    except Exception as exc:
        print(f"[ERROR] Cannot open cache database: {exc}", file=sys.stderr)
        return 2

    if args.clear_cache:
        cache.clear()

    # --- calculate ---
    client = AzurePricingClient()
    calc = CostCalculator(
        cache=cache,
        pricing_client=client,
        currency=currency,
        allow_unpriced=args.allow_unpriced,
    )
    report = calc.calculate(changes)

    # --- determine policy verdict ---
    policy_passed = report.net_delta <= budget

    # fail-closed: unpriced billable resources cause failure unless overridden
    if report.unpriced > 0 and not args.allow_unpriced:
        policy_passed = False

    # --- render output ---
    if args.json_output:
        output = format_json(report, budget, policy_passed)
    elif args.markdown:
        output = format_markdown(report, budget, policy_passed)
    else:
        output = format_text(report, budget, policy_passed)

    print(output)

    cache.close()

    # --- exit code ---
    if not policy_passed:
        return 1
    return 0


def main_cli() -> None:
    """Wrapper for the ``costguard`` console-script entry point."""
    sys.exit(main())
