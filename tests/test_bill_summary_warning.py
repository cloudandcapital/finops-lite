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
