# CostGuard Engineering and FinOps Report

## 1. What We Built

**CostGuard** is a standalone developer CLI and automated FinOps circuit breaker designed to inspect Terraform JSON execution plans before deployment (`terraform show -json tfplan.binary | costguard --max-increase 50`).

It solves a fundamental cloud governance challenge: preventing unintended, budget-busting infrastructure deployments before cloud resources are provisioned. CostGuard queries the official, public [Azure Retail Prices API](https://prices.azure.com/api/retail/prices), eliminates redundant network calls via an ACID-compliant SQLite write-through cache, calculates accurate cost deltas using `decimal.Decimal`, and halts CI/CD deployment pipelines through deterministic exit codes.

### Key Highlights
- **Zero Authentication Requirement**: Operates against the unauthenticated Azure Retail Prices REST endpoint; no subscriptions, credentials, or service principals are needed.
- **Strict Decimal Math**: Avoids IEEE 754 binary floating-point imprecision across all currency and cost calculations.
- **Fail-Closed Security Posture**: Unpriced or unrecognized billable items cause policy failure by default, avoiding silent "$0.00" pass-throughs.
- **Pluggable Formats**: Terminal text, machine-readable JSON, and GitHub PR-compatible Markdown summaries.

---

## 2. Detection and Extraction Logic

CostGuard inspects the top-level `resource_changes[]` array from Terraform's execution plan JSON (`format_version` 1.x).

### 2.1 Action Classification
Terraform represents changes as action arrays. CostGuard cleanly differentiates them:

| Action Array | Classification | Cost Formula |
|---|---|---|
| `["no-op"]` | No-op | Skipped silently |
| `["create"]` | Create | $\Delta = \text{New}$ |
| `["delete"]` | Delete | $\Delta = -\text{Old}$ |
| `["update"]` | Update | $\Delta = \text{New} - \text{Old}$ (or $\$0.00$ if metadata-only) |
| `["delete", "create"]` | Replace | $\Delta = \text{New} - \text{Old}$ |
| `["create", "delete"]` | Replace | $\Delta = \text{New} - \text{Old}$ |

CostGuard explicitly guards against treating `["delete", "create"]` or `["create", "delete"]` as standard creates.

### 2.2 Resource Identity Extraction & Normalization
CostGuard maintains an explicit registry:
- `azurerm_linux_virtual_machine` $\to$ `vm`
- `azurerm_windows_virtual_machine` $\to$ `vm`
- `azurerm_virtual_machine` $\to$ `vm`
- `azurerm_managed_disk` $\to$ `managed_disk`

Unsupported resources (e.g., `azurerm_resource_group`, `azurerm_virtual_network`, `azurerm_subnet`, `azurerm_network_security_group`, `azurerm_route_table`) are skipped cleanly without crashing.

#### Virtual Machines:
- **SKU**: Extracted from `size` or `vm_size`.
- **Region**: Extracted from `location` or `region` and normalized by stripping whitespace and converting to lowercase (e.g., `"East US"` $\to$ `"eastus"`, `"West Europe"` $\to$ `"westeurope"`).
- **OS Type**: Inferred directly from resource type and state (`azurerm_windows_virtual_machine` or `os_profile_windows_config` $\to$ `windows`, otherwise `linux`).

#### Managed Disks:
- **Identity**: Extracted from `storage_account_type`, `disk_size_gb`, and `location`.
- **Tier Resolution**: Mapped using canonical Azure disk sizes (`Premium_LRS` $\to$ P1..P80; `StandardSSD_LRS` $\to$ E1..E80; `Standard_LRS` $\to$ S4..S80). For example, 128 GB `Premium_LRS` maps to tier `P10`.

#### Expression Placeholders and `after_unknown`:
If any identity field (`size`, `vm_size`, `location`, `storage_account_type`, `disk_size_gb`) is `null`, missing, or marked `true` in `after_unknown`:
- CostGuard emits a warning (`[WARN] ... is unknown. Skipping.`)
- The resource is flagged as `unpriced`
- It is **never** assigned a fabricated price or $0.00.

---

## 3. Azure Pricing Method

CostGuard connects directly to `https://prices.azure.com/api/retail/prices` using Python's standard `urllib.request`.

### 3.1 OData Query Construction
- **Virtual Machines**:
  ```
  serviceName eq 'Virtual Machines'
  and armRegionName eq '{region}'
  and armSkuName eq '{sku}'
  and priceType eq 'Consumption'
  ```
- **Managed Disks**:
  ```
  serviceName eq 'Storage'
  and armRegionName eq '{region}'
  and priceType eq 'Consumption'
  and contains(productName, '{product_fragment}')
  ```

### 3.2 OData Pagination
CostGuard follows `NextPageLink` when returned in API responses. As required by the Azure API specification, the next page link is invoked as an absolute URL without re-applying existing query parameters.

### 3.3 Strict Price Record Validation
CostGuard rejects invalid, promotional, or mismatched price entries:
1. **SKU & Region Match**: `armSkuName` and `armRegionName` must strictly match the normalized request.
2. **Currency Code**: Must match the requested currency (`USD`, `EUR`, `GBP`, `INR`).
3. **Price Type**: Must be `Consumption` (rejects `Reservation`).
4. **Unit of Measure**: Must contain `Hour` for VMs, and `Month` for Disks.
5. **Rejection Filter**: Rejects any record containing `spot`, `low priority`, `low_priority`, `reservation`, `reserved`, or `devtest` in `meterName`, `productName`, or `skuName`.
6. **OS Licensing Separation**: Rejects Windows pricing records when pricing Linux VMs, and rejects Linux-only records when pricing Windows VMs.

---

## 4. SQLite Cache Design

CostGuard implements an ACID-compliant, write-through cache using SQLite (`pricing_cache.db`).

### 4.1 Schema
```sql
CREATE TABLE IF NOT EXISTS pricing_cache (
    sku            TEXT NOT NULL,
    region         TEXT NOT NULL,
    currency       TEXT NOT NULL,
    resource_kind  TEXT NOT NULL,
    hourly_rate    TEXT NOT NULL,
    unit_of_measure TEXT NOT NULL,
    meter_name     TEXT,
    product_name   TEXT,
    cached_at      INTEGER NOT NULL,
    PRIMARY KEY (sku, region, currency, resource_kind)
);
```

### 4.2 Lookup Workflow
1. Check SQLite with parameterized SQL query.
2. **Cache Hit**: Return record immediately; skip network call.
3. **Cache Miss**: Query Azure Retail Prices API.
4. **Validation**: Validate returned item.
5. **Persistence**: Store validated record in SQLite.
6. Return `PriceRecord`.

Failed API responses are **never cached**, preventing permanent zero-price poisoning.

---

## 5. Cost Calculation Method

Cost calculations use `decimal.Decimal` with a monthly standard of **730 hours**:

$$\text{Monthly VM Cost} = \text{hourly\_rate} \times 730$$

$$\text{Monthly Disk Cost} = \text{monthly\_rate}$$

### Net Delta Calculation:
$$\Delta_{\text{net}} = \sum \text{New Monthly Cost} - \sum \text{Old Monthly Cost}$$

- **Metadata-Only Updates**: If a resource undergoes an update where SKU, storage type, size, and region remain identical, the delta is forced to exactly $\$0.00$, preserving the baseline cost while reflecting zero net delta.
- **Precision**: Rounding is applied solely during terminal/JSON/Markdown formatting (using `ROUND_HALF_UP` to two decimal places). The internal comparison $\Delta_{\text{net}} \le \text{budget}$ is evaluated at full Decimal precision.

---

## 6. Policy Guardrail

### Verdict Criteria:
1. **Budget Threshold**: $\text{Net Monthly Delta} \le \text{--max-increase}$.
2. **Fail-Closed Rule**: If any billable resource is `unpriced`, the policy automatically **FAILS** (`exit 1`) unless `--allow-unpriced` is provided.

### Deterministic Exit Codes:
- **0**: Policy passed.
- **1**: Budget breached OR unpriced billable resource encountered without `--allow-unpriced`.
- **2**: User input error, invalid JSON, missing plan, malformed plan structure, or negative budget.

---

## 7. Test Results Matrix

The test suite covers the core project requirements and regression edge cases with 59 automated unit and regression tests:

| # | Requirement / Test Case | Module | Status |
|---|---|---|---|
| 1 | Region normalization (`"East US"` $\to$ `"eastus"`) | `test_parser.py` | PASS |
| 2 | Action classification (`create`, `delete`, `update`, `replace`, `no-op`) | `test_parser.py` | PASS |
| 3 | Create cost calculation ($\text{Old}=0, \Delta=\text{New}$) | `test_calculator.py` | PASS |
| 4 | Delete savings calculation ($\text{New}=0, \Delta=-\text{Old}$) | `test_calculator.py` | PASS |
| 5 | Update cost difference calculation | `test_calculator.py` | PASS |
| 6 | Replacement cost difference (`delete, create` / `create, delete`) | `test_calculator.py` | PASS |
| 7 | Metadata-only update produces zero delta | `test_calculator.py` | PASS |
| 8 | Metadata-only update with unknown SKU produces unpriced & warning | `test_calculator.py` | PASS |
| 9 | Metadata-only update with unknown region produces unpriced | `test_calculator.py` | PASS |
| 10 | Unknown/unsupported resource types are skipped cleanly | `test_calculator.py` | PASS |
| 11 | Unknown SKU produces warning and does not crash | `test_calculator.py` | PASS |
| 12 | Unknown region handled safely | `test_calculator.py` | PASS |
| 13 | Price record validation with `priceType` | `test_pricing.py` | PASS |
| 14 | Validation with `type` fallback | `test_pricing.py` | PASS |
| 15 | `priceType` takes precedence over `type` | `test_pricing.py` | PASS |
| 16 | Spot pricing rejected | `test_pricing.py` | PASS |
| 17 | Low Priority pricing rejected | `test_pricing.py` | PASS |
| 18 | Reservation pricing rejected | `test_pricing.py` | PASS |
| 19 | DevTest pricing rejected | `test_pricing.py` | PASS |
| 20 | Windows pricing not selected for Linux | `test_pricing.py` | PASS |
| 21 | Linux pricing not selected for Windows | `test_pricing.py` | PASS |
| 22 | Incorrect currency rejected | `test_pricing.py` | PASS |
| 23 | Non-hourly VM meters rejected | `test_pricing.py` | PASS |
| 24 | Azure pagination followed via `NextPageLink` | `test_pricing.py` | PASS |
| 25 | SQLite cache hit prevents HTTP call | `test_calculator.py` | PASS |
| 26 | Cache miss calls API and writes result | `test_calculator.py` | PASS |
| 27 | `--clear-cache` deletes cached records | `test_cli.py` | PASS |
| 28 | Invalid JSON returns exit code 2 | `test_cli.py` | PASS |
| 29 | Budget pass returns exit code 0 | `test_cli.py` | PASS |
| 30 | Budget breach returns exit code 1 | `test_cli.py` | PASS |
| 31 | Negative deltas pass when threshold is zero | `test_cli.py` | PASS |
| 32 | Non-billable noise resources produce zero impact and no crash | `test_cli.py` | PASS |
| 33 | Unpriced billable resources fail closed by default | `test_cli.py` | PASS |
| 34 | `--allow-unpriced` follows documented override behavior | `test_cli.py` | PASS |
| 35 | Metadata update unknown SKU fails closed by default | `test_cli.py` | PASS |
| 36 | Metadata update unknown SKU with `--allow-unpriced` passes | `test_cli.py` | PASS |
| 37 | Metadata update unknown region fails closed | `test_cli.py` | PASS |

---

## 8. Test Plan Results

The table below documents empirical execution results when running CostGuard against all sample test plans. Executions were conducted using a temporary SQLite cache database (`temp_test_cache.db`) to verify both live Azure Retail Prices API lookups and subsequent zero-network SQLite cache hits.

### Pricing Attribution Modes:
- **Live Azure API**: Fresh lookup executed over HTTPS against `https://prices.azure.com/api/retail/prices`.
- **Cached**: Retrieved from local SQLite cache with 0 HTTP calls.
- **Mocked/Unit-Test**: Isolated synthetic test data used during automated unit test runs.

### Execution Results Matrix

| Plan File | # Res | Scenario | Prior Total | Projected Total | Net Delta | Cache Hits | API Lookups | Exit Code | Result & Attribution |
|---|---|---|---|---|---|---|---|---|---|
| `test-plans/create_vm.json` *(Run 1)* | 4 | Create VM (`Standard_B2s`) & Disk (`Premium_LRS/P10`) | $0.00 | $59.93 | +$59.93 | 0 | 2 | **0** | **PASSED** *(Live Azure API: VM $30.37 + Disk $29.57)* |
| `test-plans/create_vm.json` *(Run 2)* | 4 | Re-run identical plan against warm cache | $0.00 | $59.93 | +$59.93 | 2 | 0 | **0** | **PASSED** *(Cached: instant evaluation)* |
| `test-plans/delete_vm.json` | 2 | Decommission VM and managed disk | $59.93 | $0.00 | -$59.93 | 2 | 0 | **0** | **PASSED** *(Cached: savings pass threshold 0)* |
| `test-plans/upgrade_vm.json` | 1 | Upgrade VM `Standard_B2s` $\to$ `Standard_D4s_v3` | $30.37 | $140.16 | +$109.79 | 1 | 1 | **0** | **PASSED** *(1 Cache hit [B2s], 1 Live API [D4s_v3])* |
| `test-plans/replacement_vm.json` | 1 | Cross-region replacement to `Standard_D2s_v3` | $30.37 | $70.08 | +$39.71 | 1 | 1 | **0** | **PASSED** *(1 Cache hit [B2s], 1 Live API [D2s_v3])* |
| `test-plans/metadata_update.json` | 2 | Tag update on VM; security rule update on NSG | $30.37 | $30.37 | $0.00 | 1 | 0 | **0** | **PASSED** *(Cached: exact $0.00 delta)* |
| `test-plans/noise_plan.json` | 17 | Unsupported infrastructure (VNet, subnets, NSGs, NICs) | $0.00 | $0.00 | $0.00 | 0 | 0 | **0** | **PASSED** *(17 resources cleanly skipped)* |
| `test-plans/unknown_sku.json` *(Default)* | 2 | Made-up VM SKU and missing location | $0.00 | $0.00 | $0.00 | 0 | 1 | **1** | **FAILED** *(Fail-closed: 2 unpriced billable items)* |
| `test-plans/unknown_sku.json` *(`--allow-unpriced`)* | 2 | Same plan with operator override | $0.00 | $0.00 | $0.00 | 0 | 1 | **0** | **PASSED** *(Override honored; warnings logged)* |

---

## 9. Limitations and Next Steps

CostGuard provides an **estimated monthly retail impact** and does not claim to predict exact final invoices:

1. **Enterprise Discounts & Agreements**: Does not reflect EA (Enterprise Agreement), MCA discounts, or customer-specific negotiated rates.
2. **Commitment Models**: Does not reflect Reserved Instances (1-year or 3-year RI) or Azure Savings Plans.
3. **Licensing Nuances**: Does not model Azure Hybrid Benefit (AHB) for Windows Server or SQL Server licensing.
4. **Variable & Consumption Pricing**: Does not predict variable data egress, bandwidth, storage transaction counts, or IOPS burst charges.
5. **Replacement Overlap**: During replacement operations, old and new resources may briefly coexist in Azure; CostGuard models steady-state monthly delta rather than transient mid-month provisioning overlap.
6. **Broader Resource Registry**: Current focus is compute VMs and managed disks; future iterations can add Azure SQL, Cosmos DB, and AKS clusters.

---

## 10. How to Run It

### Setup
```bash
pip install -e .
```

### Running Test Suite
```bash
python -m unittest discover -s tests -v
```

### CLI Command Examples
```bash
# 1. Pipeline via stdin
cat test-plans/upgrade_vm.json | costguard --max-increase 25

# 2. File execution with custom currency and budget
costguard --plan test-plans/create_vm.json --max-increase 60 --currency USD

# 3. PR Markdown table output
costguard --plan test-plans/upgrade_vm.json --max-increase 50 --markdown

# 4. JSON machine output for downstream tooling
costguard --plan test-plans/create_vm.json --max-increase 50 --json
```
