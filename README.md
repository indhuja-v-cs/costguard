# CostGuard

CostGuard is a standalone developer CLI that analyzes a Terraform JSON execution plan before deployment. It estimates the projected monthly retail cost impact of Azure infrastructure changes using the public Azure Retail Prices API, caches prices locally in SQLite, calculates the monthly delta, and blocks deployment when the increase exceeds a configured budget.

```
terraform show -json tfplan.binary | costguard --max-increase 50
```

---

## What CostGuard Does

1. **Parses Terraform execution plans**: Inspects `resource_changes[]` to identify planned actions (`create`, `delete`, `update`, `replace`, and `no-op`).
2. **Attributes Azure pricing**: Directly queries the official, public [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) without requiring an Azure account, subscription, or API key.
3. **Caches locally in SQLite**: Implements a write-through SQLite database (`pricing_cache.db`) with parameterized queries to speed up evaluations and minimize network latency.
4. **Calculates monthly deltas with Decimal precision**: Computes the net cost change using a 730-hour monthly baseline with `decimal.Decimal` to eliminate binary floating-point rounding errors.
5. **Enforces FinOps budget guardrails**: Exits with deterministic codes (0, 1, 2) to cleanly integrate into CI/CD pipelines (such as GitHub Actions, GitLab CI, or Azure DevOps).

---

## Installation

CostGuard requires **Python 3.11+**.

```bash
# Clone the repository and install in editable mode:
pip install -e .

# Or install with dev dependencies (pytest):
pip install -e ".[dev]"
```

Verify installation:

```bash
costguard --help
# Or directly via Python module:
python -m costguard --help
```

---

## CLI Usage

```
usage: costguard [-h] [--plan PATH] --max-increase AMOUNT [--currency CODE]
                 [--cache-path PATH] [--clear-cache] [--json] [--markdown]
                 [--allow-unpriced]
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--plan PATH` | Path to Terraform JSON plan. If omitted, CostGuard reads from `stdin`. | *stdin* |
| `--max-increase AMOUNT` | Maximum permitted positive monthly cost increase. Must be non-negative. | *Required* |
| `--currency CODE` | Currency code: `USD`, `EUR`, `GBP`, `INR`. | `USD` |
| `--cache-path PATH` | SQLite cache file path. | `pricing_cache.db` |
| `--clear-cache` | Clear all cached records before evaluation. | `false` |
| `--json` | Output machine-readable JSON. | `false` |
| `--markdown` | Output Markdown table suitable for PR comments. | `false` |
| `--allow-unpriced` | Override fail-closed policy when billable resources cannot be priced. | `false` |

---

## Usage Examples

### 1. Stdin Pipeline (Primary CI/CD Workflow)

```bash
terraform plan -out=tfplan.binary
terraform show -json tfplan.binary | costguard --max-increase 50
```

Or with `cat` / `type`:

```bash
cat ./test-plans/upgrade_vm.json | costguard --max-increase 25
```

### 2. File-Based Analysis

```bash
costguard --plan ./test-plans/upgrade_vm.json --max-increase 25
```

### 3. Currency Selection

```bash
costguard --plan ./test-plans/create_vm.json --max-increase 50 --currency EUR
```

*Note: Azure specifies USD as its primary pricing currency. Non-USD amounts are reported as estimated reference prices.*

### 4. Machine-Readable JSON Output

```bash
costguard --plan ./test-plans/create_vm.json --max-increase 50 --json
```

### 5. GitHub Pull Request Markdown Summary

```bash
costguard --plan ./test-plans/replacement_vm.json --max-increase 30 --markdown
```

### 6. Managing the Local SQLite Cache

```bash
# Use a custom cache location:
costguard --plan ./test-plans/create_vm.json --max-increase 50 --cache-path ./tmp/cache.db

# Clear cache before execution:
costguard --plan ./test-plans/create_vm.json --max-increase 50 --clear-cache
```

---

## Exit Codes

CostGuard provides deterministic POSIX exit codes:

| Exit Code | Meaning | Action / Interpretation |
|---|---|---|
| **0** | **Policy Passed** | Valid plan and the net positive monthly delta is $\le$ `--max-increase`. |
| **1** | **Policy Failed / Breached** | Net monthly increase exceeds budget threshold, **or** a billable resource is unpriced (fail-closed behavior). |
| **2** | **User / Input Error** | Invalid JSON, missing plan input, negative `--max-increase`, invalid currency, or malformed plan structure. |

---

## Fail-Closed Behavior & `--allow-unpriced`

By default, CostGuard fails closed (**Exit 1**) if any billable resource cannot be priced (e.g. unknown SKU, unresolved region, or API lookup miss). It **never silently treats an unknown price as $0.00**.

If you need deployment to proceed despite unpriced resources (e.g. preview regions or proprietary SKUs), pass `--allow-unpriced`. When passed:
- Unpriced resources are logged with warnings and flagged in the table as `unpriced`.
- The policy verdict evaluates only the priced delta against `--max-increase`.

---

## Offline and Network Failure Behavior

- **Cache-First**: CostGuard always checks the local SQLite cache before making any network call. If all resources in the plan exist in the cache, no external HTTP request is made.
- **Graceful Failure**: If the network is unavailable or the Azure Retail Prices API times out, CostGuard logs a warning (`[WARN] Azure API request failed: ...`) and marks the affected resources as `unpriced`. Under default fail-closed behavior, the run exits with code 1 rather than crashing.

---

## Limitations

CostGuard provides an **estimated monthly retail impact** based on 730 monthly hours. It does not predict your actual Azure invoice. Notable exclusions include:
- Taxes, enterprise discounts, and currency conversion adjustments.
- Reserved Instances (RI), Azure Savings Plans, and Spot instances.
- Azure Hybrid Benefit (AHB).
- Variable consumption charges: data egress, bandwidth, IOPS, and storage transactions.
- Temporary cost overlap during replacement actions (`delete, create` or `create, delete`).
- Unregistered resource types (e.g., App Service, Cosmos DB, AKS node pools).
