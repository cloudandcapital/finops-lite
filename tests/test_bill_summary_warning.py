"""An arithmetic coincidence is a review signal, never an automatic deletion."""

from decimal import Decimal

import pytest

from finops_lite.billing import prepare_period
from finops_lite.ccac import CCACBuildError


def csv(rows):
    return [
        {
            "name": "bill.csv",
            "text": "Date,Service,Cost,Currency\n"
            + "\n".join(f"2026-09-02,{name},{cost},USD" for name, cost in rows),
        }
    ]


def test_unknown_summary_label_is_visible_without_changing_any_amount():
    period = prepare_period(
        csv(
            [
                ("EC2", "100"),
                ("S3", "20"),
                ("Tax", "9.60"),
                ("All items combined", "129.60"),
            ]
        )
    )
    assert sum(Decimal(row["cost"]) for row in period["rows"]) == Decimal("259.20")
    assert len(period["rows"]) == 4
    assert period["potentialSummaryRowCount"] == 1
    assert period["potentialSummaryRows"] == [
        {"source": "bill.csv", "line": 5, "service": "All items combined"}
    ]
    assert "All rows remain included" in " ".join(period["warnings"])


def test_real_equal_charges_remain_counted_and_tiny_decimals_are_not_rounded():
    period = prepare_period(
        csv(
            [
                ("Compute", "100.000000000000000001"),
                ("Storage", "50"),
                ("Support", "50.000000000000000001"),
            ]
        )
    )
    assert period["potentialSummaryRowCount"] == 1
    assert period["rows"][0]["cost"] == "100.000000000000000001"
    assert len(period["rows"]) == 3
    assert "real charge can also match" in " ".join(period["warnings"])


def test_month_end_tax_does_not_hide_an_invoice_summary():
    files = [
        {
            "name": "bill.csv",
            "text": "Date,EndDate,Service,Cost,Currency\n2026-09-01,2026-10-01,EC2,100,USD\n2026-09-01,2026-10-01,S3,20,USD\n2026-09-30,2026-10-01,Tax,9.60,USD\n2026-09-01,2026-10-01,Amazon Web Services,129.60,USD",
        }
    ]
    period = prepare_period(files)
    assert len(period["rows"]) == 4
    assert sum(Decimal(row["cost"]) for row in period["rows"]) == Decimal("259.20")
    assert period["potentialSummaryRows"] == [
        {"source": "bill.csv", "line": 5, "service": "Amazon Web Services"}
    ]


def test_two_calendar_months_are_checked_without_counting_a_candidate_twice():
    files = [
        {
            "name": "bill.csv",
            "text": "Date,Service,Cost,Currency\n2026-09-02,EC2,100,USD\n2026-09-03,S3,20,USD\n2026-09-30,September charges,120,USD\n2026-10-02,EC2,200,USD\n2026-10-03,S3,40,USD\n2026-10-31,October charges,240,USD",
        }
    ]
    period = prepare_period(files)
    assert period["potentialSummaryRowCount"] == 2
    assert {row["line"] for row in period["potentialSummaryRows"]} == {4, 7}
    assert len(period["rows"]) == 6


def test_embedded_subtotal_with_a_final_total_still_warns_without_guessing_rows_away():
    period = prepare_period(
        csv(
            [
                ("EC2", "100"),
                ("S3", "20"),
                ("Services combined", "120"),
                ("Tax", "9.60"),
                ("Amazon Web Services", "129.60"),
            ]
        )
    )
    assert period["potentialSummaryRowCount"] >= 1
    assert any(
        row["service"] == "Services combined" for row in period["potentialSummaryRows"]
    )
    assert len(period["rows"]) == 5
    assert sum(Decimal(row["cost"]) for row in period["rows"]) == Decimal("379.20")


@pytest.mark.parametrize(
    "label",
    ["Total tax", "Total VAT", "Total taxes", "Total tax amount", "Total VAT (USD)"],
)
def test_a_tax_charge_is_not_mistaken_for_the_bill_total(label):
    period = prepare_period(csv([("EC2", "100"), ("S3", "20"), (label, "9.60")]))
    assert len(period["rows"]) == 3
    assert sum(Decimal(row["cost"]) for row in period["rows"]) == Decimal("129.60")


@pytest.mark.parametrize(
    "label",
    [
        "Total incl. tax",
        "Total including VAT",
        "Total after credits",
        "Total before tax",
    ],
)
def test_tax_and_credit_qualified_bill_totals_still_stop_as_totals(label):
    with pytest.raises(CCACBuildError, match="looks like a total line"):
        prepare_period(csv([("EC2", "100"), (label, "100")]))


def test_warning_details_are_bounded_without_dropping_records():
    rows = [(f"Charge {i}", "100") for i in range(7)] + [
        (f"Adjustment {i}", "-100") for i in range(5)
    ]
    period = prepare_period(csv(rows))
    assert period["potentialSummaryRowCount"] == 7
    assert len(period["potentialSummaryRows"]) == 5
    assert len(period["rows"]) == 12


@pytest.mark.parametrize(
    "rows", [[("A", "50"), ("B", "50")], [("A", "0"), ("B", "0"), ("C", "0")]]
)
def test_two_equal_charges_and_zero_rows_do_not_get_summary_warnings(rows):
    assert prepare_period(csv(rows))["potentialSummaryRowCount"] == 0


@pytest.mark.parametrize(
    "label",
    [
        "Stripe credit card fee refund",
        "Credit card fee (Stripe) discount",
        "Credit card fee credit",
    ],
)
def test_fee_name_does_not_hide_a_separate_positive_refund_or_credit(label):
    with pytest.raises(CCACBuildError, match="positive cost"):
        prepare_period(csv([(label, "3")]))
