"""Provider-published Azure prefix and documented Google invoice metadata."""

import gzip
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from finops_lite.billing import build_billing_result, normalize_csv, prepare_period
from finops_lite.ccac import CCACBuildError

ROOT = Path(__file__).parent / "fixtures"
GOOGLE = (ROOT / "google-cost-table-document.csv").read_bytes().decode()
BARE = (ROOT / "google-cost-table.csv").read_text()
DETAILS = {"month": "2026-09", "currency": "USD", "confirmed": True}
HEADER = "ChargePeriodStart,ChargePeriodEnd,ServiceName,BilledCost,BillingCurrency\n"


def test_published_microsoft_prefix_keeps_all_exact_charges_and_utc_boundaries():
    raw = gzip.decompress((ROOT / "microsoft-ea-focus-1000.csv.gz").read_bytes())
    assert (
        hashlib.sha256(raw).hexdigest()
        == "5766878f44ac4a143a6c45c401ab2bd736876ca2ff70a290a82d25dd2344d6ed"
    )
    result = prepare_period([{"name": "azure.csv.gz", "text": raw.decode()}])
    assert len(result["rows"]) == 1000 and result["identicalRows"] == 0
    assert {row["start"] for row in result["rows"]} == {"2024-06-12T00:00:00.000Z"}
    assert {row["end"] for row in result["rows"]} == {"2024-06-13T00:00:00.000Z"}
    assert sum((Decimal(row["cost"]) for row in result["rows"]), Decimal(0)) == Decimal(
        "83.346183636400141"
    )


def test_google_metadata_does_not_replace_charge_totals_month_or_source_bytes():
    supplied = [{"name": "google.csv", "text": GOOGLE}]
    result = prepare_period(supplied, {"googleCostTable": DETAILS})
    assert len(result["rows"]) == 6 and result["sourceRowCount"] == 7
    assert result["start"] == "2026-09-01" and result["end"] == "2026-10-01"
    assert result["rows"][0]["line"] == 7
    assert sum(Decimal(row["cost"]) for row in result["rows"]) == Decimal("108.830007")
    built = build_billing_result(
        supplied, options={"current": {"googleCostTable": DETAILS}}
    )
    assert (
        built["inputs"][0]["content_sha256"]
        == hashlib.sha256(GOOGLE.encode()).hexdigest()
    )
    assert any(
        "4 invoice/statement header rows" in note
        for note in built["inputs"][0]["mapping_notes"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "Invoice number,INVENTED\nUnknown charge,123\n" + BARE,
        "Invoice number,INVENTED\n2026-09-01,Compute,123,USD\n" + BARE,
        "Invoice number,INVENTED\n" * 33 + BARE,
        "Currency,USD\nCurrency,EUR\n" + BARE,
        "Invoice number,INVENTED\n",
    ],
)
def test_unknown_truncated_or_conflicting_document_headers_stop_the_file(text):
    with pytest.raises(CCACBuildError):
        normalize_csv(text, google_cost_table=DETAILS)


def test_document_currency_requires_confirmation_and_must_match():
    with pytest.raises(CCACBuildError, match="Confirm"):
        normalize_csv(GOOGLE)
    with pytest.raises(CCACBuildError, match="document header"):
        normalize_csv(GOOGLE, google_cost_table={**DETAILS, "currency": "EUR"})


@pytest.mark.parametrize("suffix", ["Z", "+00:00", " UTC"])
def test_minute_precision_explicit_utc_is_lossless(suffix):
    result = normalize_csv(
        HEADER
        + f"2026-09-01T13:02{suffix},2026-09-01T13:03{suffix},Compute,0.000001,USD"
    )
    assert result["rows"][0]["start"] == "2026-09-01T13:02:00.000Z"


def test_timezone_less_minutes_still_need_confirmation_and_invalid_times_fail():
    raw = HEADER + "2026-09-01T13:02,2026-09-01T13:03,Compute,1,USD"
    with pytest.raises(CCACBuildError, match="Confirm UTC"):
        normalize_csv(raw)
    assert (
        normalize_csv(raw, timestamp_basis="UTC")["rows"][0]["start"]
        == "2026-09-01T13:02:00.000Z"
    )
    for start in ["2026-09-01T13:02-07:00", "2026-09-01T13:99Z", "2026-09-01T24:00Z"]:
        with pytest.raises(CCACBuildError):
            normalize_csv(HEADER + f"{start},2026-09-02T00:00Z,Compute,1,USD")
