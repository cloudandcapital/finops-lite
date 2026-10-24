"""Independent audit regressions: supplied-file controls, periods and useful answers."""

from decimal import Decimal

import pytest
from ccac import validate_document

from finops_lite.billing import build_billing_result, normalize_csv, prepare_period
from finops_lite.ccac import CCACBuildError

CONTEXT = {
    "googleCostTable": {"month": "2026-09", "currency": "USD", "confirmed": True}
}
GOOGLE = "Service description,Cost type,Unrounded cost,Cost\nCompute Engine,Usage,50.004,50.00\nCompute Engine,Usage,50.004,50.00\n,Tax,9,9.00\n,Total,,109.01\n"


def files(text):
    return [{"name": "audit.csv", "text": text}]


def test_google_total_cannot_be_invalidated_by_duplicate_choice():
    with pytest.raises(CCACBuildError, match="Google's Total says 109.01"):
        prepare_period(files(GOOGLE), {**CONTEXT, "duplicatePolicy": "drop"})
    period = prepare_period(files(GOOGLE), {**CONTEXT, "duplicatePolicy": "keep"})
    assert sum(Decimal(row["cost"]) for row in period["rows"]) == Decimal("109.008")
    assert period["invoiceChecks"][0]["status"] == "matched"
    assert period["invoiceChecks"][0]["actual"] == "109.008"
    with pytest.raises(CCACBuildError, match="need your review"):
        prepare_period(files(GOOGLE), CONTEXT)


@pytest.mark.parametrize(
    "label",
    [
        "Total",
        "Subtotal",
        "Sub total",
        "Grand total",
        "Total costs",
        "Amount due",
        "Net total:",
        "Invoice total",
        "Invoice total amount",
        "Total charges",
        "Totals",
        "Total due",
        "Total (USD)",
        "TOTAL (USD):",
        "Statement total",
    ],
)
def test_simple_total_lines_stop_instead_of_counting_twice(label):
    with pytest.raises(CCACBuildError, match="looks like a total line"):
        prepare_period(
            files(
                f"Date,Service,Cost,Currency\n2026-09-01,Compute,100,USD\n2026-09-01,{label},100,USD"
            )
        )


def test_google_document_total_compares_but_does_not_claim_invoice_completeness():
    for total, status in [
        ("59", "matched"),
        ("5000", "different"),
        ("$5,000", "unreadable"),
    ]:
        period = prepare_period(
            files(
                f'Invoice total,"{total}"\nCurrency,USD\nService description,Cost type,Unrounded cost\nCompute,Usage,59'
            ),
            CONTEXT,
        )
        check = period["invoiceChecks"][0]
        assert check["status"] == status and check["actual"] == "59"
        assert check["expected"] == (None if status == "unreadable" else total)
        if status == "different":
            assert any("differ from a Google" in text for text in period["warnings"])


def test_invoice_month_must_match_header_but_earlier_usage_is_preserved():
    table = "Service description,Cost type,Unrounded cost,Usage start date\nCompute,Usage,59,2026-08-20"
    with pytest.raises(CCACBuildError, match="invoice month does not match"):
        prepare_period(files("Invoice month,2026-08\n" + table), CONTEXT)
    period = prepare_period(files(table), CONTEXT)
    assert any("outside confirmed invoice month" in text for text in period["warnings"])
    assert period["start"] == "2026-09-01"


def test_billing_period_keeps_prior_usage_correction_in_current_invoice():
    header = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency,BillingPeriodStart,BillingPeriodEnd,ChargeClass,InvoiceId\n"
    current = (
        header
        + "2026-09-01,2026-10-01,Compute,100,USD,2026-09-01,2026-10-01,,SEP\n2026-08-20,2026-08-21,Compute,-10,USD,2026-09-01,2026-10-01,Correction,SEP"
    )
    previous = (
        header + "2026-08-01,2026-09-01,Compute,80,USD,2026-08-01,2026-09-01,,AUG"
    )
    result = build_billing_result(files(current), files(previous))
    layout = result["extensions"]["finops_lite"]["billing"]
    assert layout["current"]["start"] == "2026-09-01"
    assert layout["current"]["end"] == "2026-10-01"
    assert layout["current"]["rows"][1]["start"].startswith("2026-08-20")
    assert layout["current"]["rows"][1]["invoiceId"] == "SEP"
    assert validate_document(result) == []
    assert {metric["id"]: metric["value"] for metric in result["metrics"]}[
        "metric.cloud.current.daily-average"
    ] == Decimal("3")


def test_combined_provider_billing_windows_are_not_rejected_as_one_invoice():
    header = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency,BillingPeriodStart,BillingPeriodEnd,ProviderName\n"
    period = prepare_period(
        files(
            header
            + "2026-09-10,2026-09-11,Compute,10,USD,2026-09-01,2026-10-01,AWS\n2026-09-10,2026-09-11,Compute,20,USD,2026-08-15,2026-09-15,Azure"
        )
    )
    assert (period["start"], period["end"], period["days"]) == (
        "2026-08-15",
        "2026-10-01",
        47,
    )
    assert any(
        "different supplied billing windows" in text for text in period["warnings"]
    )


@pytest.mark.parametrize("label", ["Credits", "Refund", "Promotional discount"])
def test_positive_credit_labels_are_not_silently_counted_as_charges(label):
    with pytest.raises(CCACBuildError, match="has a positive cost"):
        normalize_csv(f"Date,Service,Cost,Currency\n2026-09-01,{label},25,USD")


def test_monthly_template_confirmation_has_correct_period_without_daily_observations():
    result = build_billing_result(
        files("Date,Service,Cost,Currency\n2026-09-01,Compute,300,USD"),
        options={"current": {"simpleDateBasis": "monthly"}},
    )
    layout = result["extensions"]["finops_lite"]["billing"]
    assert layout["current"]["days"] == 30
    assert layout["daily_observation_metric_ids"] == []
    assert any("confirmed" in warning for warning in layout["current"]["warnings"])


@pytest.mark.parametrize("symbol,currency", [("$", "EUR"), ("€", "USD"), ("£", "EUR")])
def test_google_impossible_symbol_currency_pairs_stop(symbol, currency):
    with pytest.raises(CCACBuildError, match="currency does not match"):
        normalize_csv(
            f"Service description,Cost type,Cost ({symbol})\nCompute,Usage,59",
            google_cost_table={
                "month": "2026-09",
                "currency": currency,
                "confirmed": True,
            },
        )


def test_ambiguous_thousands_grouping_is_visible_and_unknown_header_never_skipped():
    with pytest.raises(CCACBuildError, match="Cost is ambiguous"):
        prepare_period(
            files('Date,Service,Cost,Currency\n2026-09-01,Compute,"1,500",EUR')
        )
    period = prepare_period(
        files('Date,Service,Cost,Currency\n2026-09-01,Compute,"1,500.50",EUR')
    )
    assert any("line 2: 1500.5 EUR" in text for text in period["warnings"])
    with pytest.raises(
        CCACBuildError, match="Unrecognized Google document header: Amount due"
    ):
        normalize_csv(
            "Amount due,59\nService description,Cost type,Unrounded cost\nCompute,Usage,59",
            google_cost_table=CONTEXT["googleCostTable"],
        )


def test_many_explicit_comma_groups_keep_bounded_warning_context():
    text = "Date,Service,Cost,Currency,RecordId\n" + "\n".join(
        f'2026-09-02,Compute,"1,500.50",USD,{index}' for index in range(100)
    )
    period = prepare_period(files(text))
    notes = [note for note in period["warnings"] if "thousands separators" in note]
    assert len(notes) == 1 and "100 Cost values" in notes[0]
    assert len(notes[0]) < 300


@pytest.mark.parametrize(
    "label",
    [
        "Credit card processing fee",
        "Credit-card transaction fee",
        "Total Defense",
        "Totaljobs",
    ],
)
def test_real_service_labels_are_not_reclassified_as_credits_or_totals(label):
    period = prepare_period(
        files(f"Date,Service,Cost,Currency\n2026-09-02,{label},25,USD")
    )
    assert period["rows"][0]["cost"] == "25"


def test_unambiguous_multiple_comma_groups_keep_exact_amount():
    period = prepare_period(
        files('Date,Service,Cost,Currency\n2026-09-02,Compute,"1,500,000",USD')
    )
    assert period["rows"][0]["cost"] == "1500000"


@pytest.mark.parametrize(
    "symbol,currency", [("¥", "USD"), ("￥", "EUR"), ("₹", "USD"), ("₩", "EUR")]
)
def test_other_known_currency_symbols_cannot_contradict_confirmation(symbol, currency):
    with pytest.raises(CCACBuildError, match="currency does not match"):
        normalize_csv(
            f"Service description,Cost type,Cost ({symbol})\nCompute,Usage,59",
            google_cost_table={
                "month": "2026-09",
                "currency": currency,
                "confirmed": True,
            },
        )


def test_google_human_month_is_checked_and_unknown_month_needs_explicit_confirmation():
    table = "Service description,Cost type,Unrounded cost\nCompute,Usage,100\n,Tax,9\n,Tax,-1"
    result = build_billing_result(
        files("Invoice month,September 2026\n" + table), options={"current": CONTEXT}
    )
    layout = result["extensions"]["finops_lite"]["billing"]["current"]
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics[layout["metric_ids"]["tax"]] == Decimal("8")
    assert layout["rows"][1]["chargeCategory"] == "Tax"
    with pytest.raises(CCACBuildError, match="invoice month does not match"):
        prepare_period(files("Invoice month,August 2026\n" + table), CONTEXT)
    period = prepare_period(files("Invoice month,Septembre 2026\n" + table), CONTEXT)
    assert any(
        "header format could not be compared" in warning
        for warning in period["warnings"]
    )


def test_daily_evidence_keeps_in_window_usage_and_retains_correction_in_total():
    from datetime import date, timedelta

    header = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency,BillingPeriodStart,BillingPeriodEnd,ChargeClass\n"
    rows = [
        f"{date(2026,9,d)},{date(2026,9,d)+timedelta(days=1)},EC2,{250 if d==25 else 100},USD,2026-09-01,2026-10-01,Usage"
        for d in range(1, 31)
    ]
    rows.append("2026-08-20,2026-08-21,EC2,-10,USD,2026-09-01,2026-10-01,Correction")
    result = build_billing_result(files(header + "\n".join(rows)))
    layout = result["extensions"]["finops_lite"]["billing"]
    metrics = {item["id"]: item for item in result["metrics"]}
    assert metrics[layout["current"]["metric_ids"]["net"]]["value"] == Decimal("3140")
    assert len(layout["current"]["rows"]) == 31
    observations = [metrics[key] for key in layout["daily_observation_metric_ids"]]
    assert len(observations) == 30
    assert all(
        item["dimensions"]["date"].startswith("2026-09-") for item in observations
    )
    assert sum(item["value"] for item in observations) == Decimal("3150")
    assert any(
        "1 record is wholly outside" in warning
        for warning in layout["current"]["warnings"]
    )
    assert validate_document(result) == []


def test_partial_day_overlapping_billing_start_is_not_omitted_as_a_correction():
    header = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency,BillingPeriodStart,BillingPeriodEnd\n"
    text = (
        header
        + "2026-08-31T23:00:00Z,2026-09-01T01:00:00Z,EC2,2,USD,2026-09-01,2026-10-01\n2026-09-01,2026-09-02,EC2,100,USD,2026-09-01,2026-10-01"
    )
    result = build_billing_result(files(text))
    layout = result["extensions"]["finops_lite"]["billing"]
    assert not layout["daily_observation_metric_ids"]
    assert len(layout["current"]["rows"]) == 2
    assert not any("wholly outside" in note for note in layout["current"]["warnings"])
