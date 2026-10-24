"""Invented records in Google's documented English Cost table layout.

This is contract evidence, not a captured current customer export.
"""

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from ccac import validate_document

from finops_lite.billing import build_billing_result, normalize_csv, prepare_period
from finops_lite.ccac import CCACBuildError

CSV = (Path(__file__).parent / "fixtures/google-cost-table.csv").read_text()
DETAILS = {"month": "2026-09", "currency": "USD", "confirmed": True}
IDENTITY = {
    "run_id": "7e5f2340-60aa-46e6-a7dd-66ed33854d03",
    "generated_at": "2026-10-20T00:00:00Z",
}


def supplied(text=CSV):
    return [{"name": "google-cost-table.csv", "text": text}]


def test_google_exact_credits_tax_and_total_keep_original_evidence():
    result = build_billing_result(
        supplied(), options={"current": {"googleCostTable": DETAILS}}, **IDENTITY
    )
    values = {m["id"]: m["value"] for m in result["metrics"]}
    assert values["metric.tech-spend.scope.cloud"] == Decimal("108.830007")
    data = prepare_period(supplied(), {"googleCostTable": DETAILS})
    assert len(data["rows"]) == 6 and data["sourceRowCount"] == 7
    assert data["start"] == "2026-09-01" and data["end"] == "2026-10-01"
    assert data["spansMultipleDays"] is True
    assert data["rows"][3]["service"] == "Tax · service not supplied"
    assert any("not counted as a charge" in w for w in data["warnings"])
    assert (
        result["inputs"][0]["content_sha256"]
        == hashlib.sha256(CSV.encode()).hexdigest()
    )
    assert result["inputs"][0]["source_version"] == "google_cost_table/1.0"
    assert any(
        "2026-09" in note and "USD" in note
        for note in result["inputs"][0]["mapping_notes"]
    )
    assert (
        result["extensions"]["finops_lite"]["billing"]["daily_observation_metric_ids"]
        == []
    )
    assert validate_document(result) == []


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {**DETAILS, "confirmed": False},
        {**DETAILS, "currency": "ABC"},
        {**DETAILS, "month": "2026-13"},
    ],
)
def test_google_never_guesses_missing_period_currency_or_confirmation(details):
    with pytest.raises(CCACBuildError):
        normalize_csv(CSV, google_cost_table=details)


@pytest.mark.parametrize(
    "changed",
    [
        CSV.replace(",108.83\n", ",999.00\n"),
        CSV + ",,,,,,Total,,,,108.83\n",
        CSV.replace(",Usage,", ",Unknown category,"),
        CSV.replace("120.123456", "not a cost", 1),
        CSV.replace("Unrounded Cost ($)", "List cost"),
    ],
)
def test_google_malformed_or_unreconciled_records_fail(changed):
    # Replacing Unrounded cost with List cost leaves only rounded Cost, whose
    # per-row sum cannot reconcile to this source's invoice-precision total.
    with pytest.raises(CCACBuildError):
        normalize_csv(changed, google_cost_table=DETAILS)


def test_google_cost_currency_mismatch_and_period_overrides_fail():
    with pytest.raises(CCACBuildError, match="currency"):
        normalize_csv(CSV.replace("Cost ($)", "Cost (EUR)"), google_cost_table=DETAILS)
    with pytest.raises(CCACBuildError, match="invoice month"):
        prepare_period(supplied(), {"googleCostTable": DETAILS, "start": "2026-08-01"})


def test_focus_missing_service_preserves_tax_and_microsecond_boundaries():
    text = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency,ProviderName,ChargeCategory\n2026-09-01 00:00:00.000001 UTC,2026-09-01 00:00:00.000002 UTC,,0.000007,USD,Google Cloud,Tax"
    result = normalize_csv(text)
    assert result["rows"][0]["start"] == "2026-09-01T00:00:00.000001Z"
    assert result["rows"][0]["end"] == "2026-09-01T00:00:00.000002Z"
    assert result["rows"][0]["cost"] == "0.000007"
    assert result["rows"][0]["service"] == "Tax · service not supplied"
    assert result["warnings"]
