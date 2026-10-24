# Supplied billing CSV input

Audit recheck: template summary labels include Invoice total, Total charges,
Totals, Total due and currency-qualified totals such as Total (USD). They stop
instead of becoming a charge. Real service names are not rejected merely for
containing “total”; a credit card processing fee remains a positive charge.
Service/monthly totals and tax/currency-qualified summary labels are also blocked.
Vendor-prefixed card fees remain charges; a separately named refund or credit
still requires its signed amount. In a flat, same-window simple CSV with at least
three rows, a row equal to the combined other counted rows receives a possible
summary warning. Every row stays included: equal values do not prove a duplicate
or summary. Review details name the original file/line and are capped at five;
the full candidate count and original records are preserved.

Google invoice-month headers support YYYY-MM and English Month YYYY. An unknown
header format requires explicit visitor confirmation and a visible warning;
a recognized conflicting month still stops. Explicit Tax rows retain their
category and produce an exact tax metric, without validating tax correctness.
Records wholly outside their supplied billing window remain in totals and
exports but are excluded from that window’s daily spike observations. In-window
gaps, partial days and negative daily sums still cannot become anomaly evidence.

`finops_lite.billing.build_billing_result` is a credential-free input to the
existing FinOps Lite CCAC producer. It owns normalization, signed billed-cost
totals, service/provider/date metrics, comparisons, evidence and finding
references. It uses the existing metric builder with lossless Decimal output.
The AWS collector and its pinned illustrative artifacts keep their existing
behavior. This input powers Cloud Cost Guard's browser-local upload pipeline.

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

Pass expanded UTF-8 CSV text, real ISO dates/UTC timestamps, a supported currency,
and signed decimals with at most 18 places. A transport may locally decompress
`.csv.gz` first; the source hash identifies the expanded original CSV text.
Timezone-less FOCUS timestamps require explicit
`options={"current":{"timestampBasis":"UTC"}}` after confirming the export uses
UTC. The choice appears in warnings and source mapping notes; source text is
never rewritten. Non-UTC offsets remain unsupported. No FX, amortization, omitted tax,
credit or account coverage is inferred. Invalid rows stop the entire selection.
Each period supports 20 parts, 10 MB, 50,000 rows, 366 days and 1,000 service
groups. Signed totals above one trillion in charges or adjustments are rejected.

Explicit BigQuery timestamps ending in ` UTC` and up to six fractional-second
digits are accepted without dropping microseconds. FOCUS rows with an empty
ServiceName keep their amounts and receive a supplied ChargeCategory plus
service-not-supplied label, with a coverage warning. A blank name is not evidence
of a particular service. Simple CSV still requires its Service value.
Valid UTC timestamps at minute precision, such as `2024-06-12T00:00Z` in
Microsoft's published EA example, are also accepted. Missing timezones still
require explicit confirmation; non-UTC offsets are not guessed.

## Google Cloud Cost table

The English Cost table CSV is a separate invoice-month input. Follow Google's
[Cost table instructions](https://docs.cloud.google.com/billing/docs/how-to/cost-table):
reset filters, choose the invoice month, use No grouping, and include Service
description, Cost type and Unrounded cost. Cost is a supported rounded fallback;
List cost is never substituted. Credits already appear as separate signed rows,
so no other credit column is added to these amounts.

Recognized invoice/statement key-value headers before the cost table are read
separately, up to 32 nonblank metadata rows. Unknown preceding records stop the
file; the reader does not search past arbitrary billing data. A clear three-letter
document currency must match the confirmed currency. Invoice dates and header
totals do not override the reporting month or become charges. Their presence is
noted in evidence; the hash still covers the complete original CSV.

Confirm the invoice month and three-letter billing currency explicitly:

```python
options = {"current": {"googleCostTable": {
    "month": "2026-09", "currency": "USD", "confirmed": True
}}}
```

The month is a reporting window, not a claim about daily usage or the export's
completeness. No daily anomaly observations are created from these rows. A
single Total row is checked at Google's documented two-decimal invoice precision
and is excluded from charges; a mismatch fails the review. Missing amounts are
never replaced with zero. Source text stays unchanged; evidence records its hash,
normalized charges, selected context and any rounded fallback, rather than every
original CSV column. Review this format separately from other formats of a
bill. Tests use invented records matching documented columns, not captured
current customer downloads; broader native-provider acceptance remains separate.

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

## Independent bill-review controls

Google Cost table Total is checked again after duplicate handling. A Count once
choice cannot produce a report that contradicts the source Total. Invoice/statement
header totals are separately compared at two decimal places and recorded in
`invoiceChecks`. A difference is a visible review warning, not an automatic claim
of missing charges: Google's own documentation describes valid timing, multiple
invoice and memo differences. Unreadable header amounts are flagged, not guessed.
An explicit Invoice month header must match the confirmed month; earlier usage
dates are retained and flagged rather than silently relabelled or discarded.

Simple-template Total/Subtotal/Grand total lines and positive credit/refund/discount
labels stop the review, rather than changing their sign or counting totals twice.
Ambiguous comma-only amounts stop instead of guessing a locale. Explicit thousands groups with a decimal point are disclosed in one bounded warning per file, with at most three examples. Currency symbols in Google
cost columns reject impossible currency confirmations; a dollar sign still needs
an explicit three-letter code. Unknown metadata before a recognizable Google
table remains an error and no rows are skipped.

For monthly simple totals, pass `simpleDateBasis: "monthly"` after confirmation;
Date must be a month start and missing EndDate becomes the next month start. An
explicit EndDate remains authoritative. Without this option, Date still means
one day, with a warning for ambiguous first-of-month records.

When every FOCUS row supplies BillingPeriodStart/End, those dates define
the reporting/comparison window. ChargePeriod dates, including prior-period
corrections, remain unchanged. Invalid or incomplete boundary pairs stop the
review. Different billing windows use their combined range with a comparison
warning; they are not presented as one verified invoice. InvoiceId is retained where supplied, without making
a completeness claim. Neither native provider manifests nor invoice IDs establish
coverage in this version.

Daily average is a producer-owned metric: supplied signed net divided by reporting
days, rounded to 18 decimal places. It is not measured daily usage, a run-rate
forecast or a complete-account estimate. Findings include questions to ask the
account owner; these are next checks, not causes inferred from a cost file.
