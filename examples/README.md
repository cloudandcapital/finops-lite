# Examples

This directory contains sample billing exports and expected outputs for all three supported cloud providers.

## AWS — FOCUS 1.0 Export

`aws-billing-sample.csv` is a sample of the output from `finops export focus`. It is already in FOCUS 1.0 format.

Feed it directly into [FinOps Watchdog](https://github.com/dianuhs/finops-watchdog):

```bash
finops-watchdog detect \
  --input aws-billing-sample.csv \
  --time-column ChargePeriodStart \
  --value-column BilledCost \
  --group-by ServiceName \
  --window 7d \
  --output-format json
```

## Azure — Ingest and Normalize

`azure-billing-sample.csv` mimics a Cost Management export from the Azure portal.

Normalize it to FOCUS 1.0:

```bash
finops ingest focus --file azure-billing-sample.csv
```

Expected output (stdout, CSV):

```
BilledCost,ResourceId,ServiceName,ChargePeriodStart,ChargePeriodEnd,ChargeType,provider,...
98.40,/subscriptions/a1b2c3d4/.../api-server-01,Virtual Machines,2026-03-01,2026-03-02,Usage,azure,...
```

## GCP — Ingest and Normalize

`gcp-billing-sample.csv` mimics a billing export from BigQuery or the GCP Billing console.

```bash
finops ingest focus --file gcp-billing-sample.csv
```

Expected output (stdout, CSV):

```
BilledCost,ResourceId,ServiceName,ChargePeriodStart,ChargePeriodEnd,ChargeType,provider,...
112.50,projects/my-prod-project/.../api-vm-001,Compute Engine,2026-03-01,2026-03-02,Usage,gcp,...
```
