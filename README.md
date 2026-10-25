# FinOps Lite

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Cloud](https://img.shields.io/badge/cloud-AWS-orange)](https://github.com/cloudandcapital/finops-lite)
[![Contract](https://img.shields.io/badge/CCAC-1.0%20%7C%201.1-blue)](https://github.com/cloudandcapital)

FinOps Lite is an open-source AWS cost visibility CLI and the canonical cloud-cost producer for the Cloud & Capital pipeline.

For the complete six-tool demo and roadmap, see [Tech Spend Command Center](https://github.com/cloudandcapital/tech-spend-command-center).

FinOps Lite 0.4.0 preserves its `ccac/1.0.0` producer path and adds an explicit `ccac/1.1.0` Cloud accounting-boundary path validated by the released CCAC 0.2.0 reference package.

## Current scope

What works:

- AWS Cost Explorer cost summaries and equal-length period comparisons
- Service-level cost breakdowns
- Reconciled daily-by-service metrics for downstream anomaly attribution
- Calendar-month reports and month comparisons
- JSON, CSV, YAML, table, and executive output for legacy reporting commands
- Deterministic, credential-free illustrative CCAC output
- Real AWS CCAC output with source version, content hash, evidence, metrics, and reconciliation
- Fail-closed numeric parsing for canonical summaries
- Experimental CSV ingestion/normalization helpers for selected Azure and Google-shaped samples

What is not yet claimed:

- Native Azure or Google Cloud billing API integrations
- Resource-level multi-cloud normalization
- Official FOCUS conformance or FOCUS 1.4 export
- Verified savings or automated remediation
- Direct production feeding into Cloud Cost Guard

The public demo is credential-free and uses entirely illustrative data.

## Install the released CLI

```bash
pipx install "git+https://github.com/cloudandcapital/finops-lite.git@v0.3.0"
```

For development from the default branch:

```bash
git clone https://github.com/cloudandcapital/finops-lite.git
cd finops-lite
uv sync --locked --extra dev
uv run pytest tests/ -q
```

Python 3.10 or newer is required.

## Five-minute credential-free demo

```bash
finops --no-cache ccac --demo --output finops-lite-result.json
```

The default remains `ccac/1.0.0`. Select either supported contract explicitly:

```bash
finops --no-cache ccac --demo --contract-version 1.0.0 --output finops-lite-result.json
finops --no-cache ccac --demo --contract-version 1.1.0 --output finops-lite-result.json
```

The command writes `finops-lite-result.json`; rerunning with the same path
replaces that explicitly named local file.

The cross-repository acceptance suite validates this artifact against the shared CCAC reference schemas. The `ccac validate` command is available to contributors who install the separate Cloud & Capital CCAC reference package; it is not required to run this demo.

The demo is explicitly labeled `illustrative`, declares AWS as the scenario's sole Cloud billing source, uses a fixed run ID and timestamp, and is byte-for-byte deterministic. Its reconciled Cloud value remains USD 2,194.00. It contains 21 consecutive daily observations and one deliberate final-day spend spike so the default FinOps Watchdog detector can be exercised end to end. It passes through the same FinOps Lite CCAC builder used for real summaries.

**Illustrative sample billing data. No customer accounts, credentials, or production resources are connected.**

## Real AWS CCAC output

Configure read-only AWS access:

```bash
aws configure --profile finops-prod
export AWS_PROFILE=finops-prod
export AWS_DEFAULT_REGION=us-east-1
```

Required permissions:

- `ce:GetCostAndUsage`
- `sts:GetCallerIdentity`

Generate a canonical result:

```bash
finops --no-cache ccac \
  --contract-version 1.1.0 \
  --start 2026-07-01 \
  --end 2026-07-31 \
  --output finops-lite-result.json
```

`--end` is inclusive at the CLI boundary. The CCAC document converts it to an exclusive period end and records both semantics.

The command retrieves the complete service breakdown rather than truncating it to the top ten. It refuses to emit a canonical result when daily service rows, service totals, daily totals, and the reported total do not reconcile.

The explicit real 1.1 path requests AWS Cost Explorer `NetUnblendedCost`, which AWS defines as cost after discounts, and maps it to CCAC `net_cost`. It does not fall back to `BlendedCost`, and neither current nor previous request passes a charge-type filter. Under Cloud & Capital's technology-spend boundary policy, credits, taxes, adjustments, and shared services are included when AWS returns them in that unfiltered result. The canonical Cloud metric includes provider-billed native AI and excludes direct AI-vendor billing.

The explicit 1.1 demo instead uses a deterministic illustrative net-cost summary modeled for the public scenario. It declares AWS as the scenario's sole illustrative Cloud source; no account or AWS API is queried.

A connected AWS billing view does not prove complete enterprise Cloud coverage. Real 1.1 output therefore preserves the observed AWS value but declares partial coverage and is not eligible for an all-in technology-spend total. Native Azure and Google Cloud billing ingestion remain later work.

## Other cost commands

```bash
finops cost overview --days 30
finops cost overview --days 30 --format json
finops cost monthly --month 2026-07 --format json
finops cost compare --current 2026-07 --baseline 2026-06
finops summarize --start 2026-07-01 --end 2026-07-31
```

The compact `summarize` output is a legacy interface. New pipeline consumers should use `finops ccac`.

## FOCUS status

The official current specification is FOCUS 1.4. FinOps Lite does not yet claim FOCUS 1.4 conformance.

Older releases advertised an unofficial “FOCUS 2026” format. That name was incorrect. The `focus2026` command and internal module remain temporarily as clearly deprecated experimental compatibility paths; they are not evidence of official FOCUS conformance.

Use the FinOps Foundation's official validator for conformance testing. Official FOCUS adapters and representative AWS/Azure/Google fixtures are a later FinOps Lite migration milestone.

The existing `finops export focus` command emits a service-level working schema. It should be treated as a FinOps Lite compatibility export, not a certified FOCUS dataset.

## Trust behavior

- Invalid, missing, NaN, or infinite monetary values fail instead of becoming zero.
- Missing Cost Explorer dates fail closed; only an explicitly observed zero is emitted as zero spend.
- Illustrative output is labeled in the document itself.
- The producer emits observed and calculated metrics, not verified savings.
- Untagged spend is not converted into an opportunity.
- FinOps Lite emits no mutating cloud commands.
- Every canonical metric references evidence derived from a hashed source summary.
- Help, version, and credential-free demo commands do not create cache directories in the user's home.

## Pipeline position

```text
billing export or illustrative source
  -> FinOps Lite CCAC result
  -> FinOps Watchdog / domain tools
  -> Tech Spend Command Center trusted report
  -> Cloud Cost Guard
  -> Lumen
```

The v0.3 illustrative acceptance run connects this result through Watchdog and Tech Spend Command Center into a contract-valid trusted report. Cloud Cost Guard remains unchanged until its downstream adapter is reviewed separately.

| Component | Compatible version |
|---|---|
| FinOps Lite | `0.4.x` |
| CCAC | `ccac/1.0.0`, `ccac/1.1.0` (CCAC package 0.2.0) |
| FinOps Watchdog | `0.4.x` |
| Tech Spend Command Center | `0.2.x` |

FinOps Lite is `0.4.x`, while FinOps Watchdog is independently versioned
at `0.4.x`. Compatible tools do not need identical application package
versions. This PR does not connect Cloud Cost Guard and does not change another producer or consumer.

## License

MIT © 2026 Diana Molski, Cloud & Capital

### Supplied billing CSVs (draft integration)

The credential-free [billing CSV adapter](docs/billing-csv.md) produces canonical signed billed-cost metrics, exact comparisons and eligible observed daily evidence for Guard. This is an input to FinOps Lite, not a separate dashboard calculator. The supported profile and partial-coverage rules are documented there.


### Development Python versions

The tool and its tests support Python 3.10 and newer. Run dependency auditing with Python 3.11 or newer; the audit tooling is included in the development extra on those versions.
