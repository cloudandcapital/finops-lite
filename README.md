# FinOps Lite

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Multi-cloud](https://img.shields.io/badge/cloud-AWS%20%7C%20Azure%20%7C%20GCP-orange)](https://github.com/cloudandcapital/finops-lite)
[![FOCUS 2026](https://img.shields.io/badge/FOCUS-2026-brightgreen)](https://focus.finops.org)

**Multi-cloud cost CLI — AWS/Azure/GCP spend analysis with FOCUS 2026 export and validator.**

Part of the [Cloud & Capital](https://github.com/cloudandcapital) FinOps pipeline.  
Feeds cost data into [Cloud Cost Guard](https://github.com/cloudandcapital/cloud-cost-guard) — the unified FinOps dashboard.

---

**Features:**
- Pull AWS, Azure, or GCP cost data via native billing APIs
- Period comparisons (month-over-month, week-over-week)
- FOCUS 2026 CSV export — the latest FinOps Foundation open cost spec
- FOCUS 2026 validator — flag non-compliant fields in any billing export
- Anomaly signals with configurable thresholds
- JSON, CSV, YAML, and executive Markdown output

---

## Install

```bash
# pipx (recommended for CLI tools)
pipx install "git+https://github.com/cloudandcapital/finops-lite.git"

# or pip in a venv
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

```bash
finops --help
```

---

## AWS Setup

```bash
aws configure --profile finops-prod
export AWS_PROFILE=finops-prod
export AWS_DEFAULT_REGION=us-east-1
```

IAM required: `ce:GetCostAndUsage`, `sts:GetCallerIdentity`.

---

## Key Commands

```bash
# Cost overview — last 30 days
finops cost overview --days 30

# Monthly calendar report
finops cost monthly 2026-04

# Compare two months
finops cost compare 2026-04 2026-03

# Export FOCUS 2026 CSV (latest spec)
finops export focus2026 --days 30 --output focus-2026.csv

# Export FOCUS 1.0 CSV (legacy)
finops export focus --days 30 > focus-1.0.csv

# Validate any billing CSV against FOCUS 2026
finops validate focus my-billing-export.csv

# Anomaly signals
finops signals detect --threshold 0.15
```

---

## FOCUS 2026 Support

FinOps Lite exports billing data in the **FOCUS 2026** format — the FinOps Foundation's latest open cost specification. Key additions over FOCUS 1.0:

| Column | What it adds |
|--------|-------------|
| `ChargeClass` | `Standard` or `Correction` — marks retroactive billing adjustments |
| `EffectiveCost` | Actual cost after commitment discounts (RI / Savings Plans / CUDs) |
| `ListCost` | On-demand list price before discounts |
| `ContractedCost` | Negotiated rate cost |
| `CommitmentDiscountType/Status` | Reserved, Savings-Plan, or Committed-Use tracking |
| `ServiceCategory` | Standardized taxonomy: Compute, Storage, Databases, Networking… |
| `RegionId / RegionName` | Normalized region identifiers |
| `ConsumedUnit / ConsumedQuantity` | Actual usage vs pricing unit |
| `x_focus_schema_version` | `2026.0` — marks file as FOCUS 2026 compliant |

The built-in validator (`finops validate focus`) checks all required columns, enum values, date ordering, currency codes, and schema version — and exits non-zero if the file is non-compliant (pipe-friendly for CI).

---

## Output Formats

```bash
finops cost overview --output-format json    # machine-readable JSON
finops cost overview --output-format csv     # spreadsheet-ready CSV
finops cost overview --output-format yaml    # YAML
finops cost overview --output-format executive  # executive Markdown summary
```

---

## Part of the Cloud & Capital Pipeline

| Tool | Role |
|------|------|
| **FinOps Lite** | Cost pull + FOCUS 2026 export |
| [Cloud Cost Guard](https://github.com/cloudandcapital/cloud-cost-guard) | Unified dashboard (consumes FinOps Lite output) |
| [FinOps Watchdog](https://github.com/cloudandcapital/finops-watchdog) | Anomaly detection from cost CSVs |
| [Recovery Economics](https://github.com/cloudandcapital/recovery-economics) | Resilience cost modeling |
| [AI Cost Lens](https://github.com/cloudandcapital/ai-cost-lens) | AI/LLM spend tracking |
| [SaaS Cost Analyzer](https://github.com/cloudandcapital/saas-cost-analyzer) | SaaS license governance |
| [Tech Spend Command Center](https://github.com/cloudandcapital/tech-spend-command-center) | Executive reporting |

---

## Pipeline

FinOps Lite is step one. From here:

- **[FinOps Watchdog](https://github.com/cloudandcapital/finops-watchdog)** — run anomaly detection on any cost CSV, including the FOCUS export above
- **[Recovery Economics](https://github.com/cloudandcapital/recovery-economics)** — model and compare backup/restore cost scenarios
- **[Cloud Cost Guard](https://github.com/cloudandcapital/cloud-cost-guard)** — full dashboard with spend trends, savings coverage, and rightsizing

## License

MIT © 2025 Diana Molski, Cloud & Capital
