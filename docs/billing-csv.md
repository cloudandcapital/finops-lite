# Supplied billing CSV input (draft)

`finops_lite.billing.build_billing_result` is a credential-free input to the
existing FinOps Lite CCAC producer. It owns normalization, signed billed-cost
totals, service/provider/date metrics, comparisons, evidence and finding
references. It uses the existing metric builder with lossless Decimal output.
The AWS collector and its pinned illustrative artifacts keep their existing
behavior. This draft is part of Cloud Cost Guard's upload integration (#18).

```python
from pathlib import Path
from finops_lite.billing import build_billing_result
from finops_lite.ccac_json import dumps_exact

result = build_billing_result(
    [{"name": "current.csv", "text": Path("current.csv").read_text()}],
    # An earlier, non-overlapping period is optional.
    [{"name": "previous.csv", "text": Path("previous.csv").read_text()}],
)
Path("finops-lite.json").write_text(dumps_exact(result))
```

Simple CSV requires Date, Service, Cost, Currency. Optional Provider, RecordId,
BillingAccountId and exclusive EndDate preserve identity and charge intervals.
FOCUS requires ChargePeriodStart, ChargePeriodEnd, ServiceName, BilledCost and
BillingCurrency. EffectiveCost and ListCost are not substituted. This supported
column reader is not a complete FOCUS conformance validator.

Use UTF-8 uncompressed CSV, real ISO dates/UTC timestamps, a supported currency,
and signed decimals with at most eight places. No FX, amortization, omitted tax,
credit or account coverage is inferred. Invalid rows stop the entire selection.
Each period supports 20 parts, 10 MB, 50,000 rows, 366 days and 1,000 service
groups. Signed totals above one trillion in charges or adjustments are rejected.

Repeated stable IDs count once only if all billing content and extra dimensions
match. Conflicts fail. Identical anonymous rows require explicit
`options={"current":{"duplicatePolicy":"keep"}}` or `"drop"`; the default is
`"review"`. Optional start/end dates select the full window (form end inclusive),
which must contain every charge interval. Unknown source dimensions participate
in duplicate identity but are not analytical measures.

The canonical Cloud scope is partial, uses billed_cost, includes signed supplied
credits/taxes, and is ineligible for a complete all-in Technology Spend total.
Source hashes bind original CSV text, with customer-confidential/local-read-only
classification in real mode. No cloud API, filesystem write or network call is
made by this builder. Callers decide where to retain emitted artifacts.

Daily metrics eligible for Watchdog contain only consecutive observed full UTC
days with nonnegative daily totals for that service/provider. Gaps, monthly
intervals and negative days are excluded with reasons. Charge-start chart
buckets use a different dimension and cannot become anomaly observations. A
period comparison and Watchdog's daily-history comparison are distinct.

Canonical artifact JSON contains exact numeric tokens. Use `dumps_exact` and
`loads_exact` across analytical stages; binary float parsing loses decimal
precision. Presentation amounts are formatted only after released CCAC and
Command Center validation. Tests use invented records; no customer data belongs
in repository fixtures or public GitHub workflow inputs.
