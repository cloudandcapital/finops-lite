"""Invented bills exercise the real producer, not a presentation calculator."""

from copy import deepcopy
from decimal import Decimal

import pytest
from ccac import validate_document

from finops_lite.billing import build_billing_result, prepare_period
from finops_lite.ccac import CCACBuildError
from finops_lite.ccac_json import dumps_exact, loads_exact

IDENTITY = dict(
    run_id="7e5f2340-60aa-46e6-a7dd-66ed33854d03", generated_at="2026-10-20T00:00:00Z"
)


def csv(body, header="Date,Service,Cost,Currency,Provider,RecordId"):
    return [{"name": "invented.csv", "text": header + "\n" + body}]


def metrics(result):
    return {item["id"]: item for item in result["metrics"]}


def test_exact_signed_billed_cost_scope_and_comparison():
    result = build_billing_result(
        csv(
            "2026-09-01,Compute,720.125,USD,AWS,c1\n2026-09-01,Database,340,USD,AWS,d1\n2026-09-02,Credit,-52.25,USD,AWS,k1"
        ),
        csv("2026-08-01,Compute,480.1,USD,AWS,c0"),
        **IDENTITY,
    )
    values = metrics(result)
    assert values["metric.tech-spend.scope.cloud"]["value"] == Decimal("1007.875")
    assert values["metric.cloud.change-amount"]["value"] == Decimal("527.775")
    assert values["metric.cloud.change-percentage"]["value"] == Decimal("109.9")
    boundary = values["metric.tech-spend.scope.cloud"]["accounting_boundary"]
    assert boundary["cost_basis"] == "billed_cost"
    assert boundary["coverage"] == "partial" and boundary["total_eligible"] is False
    assert validate_document(result) == []
    assert loads_exact(dumps_exact(result)) == result


def test_eight_place_decimals_do_not_round_to_cents_in_artifacts():
    result = build_billing_result(
        csv("2026-09-01,Compute,999999999999.99499999,USD,AWS,x"), **IDENTITY
    )
    assert "999999999999.99499999" in dumps_exact(result)
    assert metrics(result)["metric.tech-spend.scope.cloud"]["value"] == Decimal(
        "999999999999.99499999"
    )


@pytest.mark.parametrize(
    "cost,currency,date",
    [
        ("NaN", "USD", "2026-09-01"),
        ("1e3", "USD", "2026-09-01"),
        ("1.000000001", "USD", "2026-09-01"),
        ("1", "ABC", "2026-09-01"),
        ("1", "USD", "2026-02-30"),
    ],
)
def test_invalid_records_never_create_partial_totals(cost, currency, date):
    with pytest.raises(CCACBuildError):
        build_billing_result(csv(f"{date},Compute,{cost},{currency},AWS,x"), **IDENTITY)


def test_duplicate_identity_policy_and_full_dimension_conflicts():
    record = "2026-09-01,Compute,10.1,USD,AWS,x"
    result = prepare_period(csv(record + "\n" + record))
    assert len(result["rows"]) == 1 and result["repeatedIds"] == 1
    with pytest.raises(CCACBuildError, match="conflicting"):
        prepare_period(csv(record + "\n" + record.replace("10.1", "10.2")))
    anonymous = record[:-1]
    with pytest.raises(CCACBuildError, match="review"):
        prepare_period(csv(anonymous + "\n" + anonymous))
    assert (
        len(
            prepare_period(
                csv(anonymous + "\n" + anonymous), {"duplicatePolicy": "keep"}
            )["rows"]
        )
        == 2
    )
    assert (
        len(
            prepare_period(
                csv(anonymous + "\n" + anonymous), {"duplicatePolicy": "drop"}
            )["rows"]
        )
        == 1
    )
    with pytest.raises(CCACBuildError, match="conflicting"):
        prepare_period(
            csv(
                record + ",us-east-1\n" + record + ",us-west-2",
                header="Date,Service,Cost,Currency,Provider,RecordId,Region",
            )
        )


def test_daily_observations_require_full_consecutive_nonnegative_days():
    for body in [
        "2026-09-01,Compute,10,USD,AWS,a\n2026-09-03,Compute,20,USD,AWS,b",
        "2026-09-01,Compute,-1,USD,AWS,a",
    ]:
        result = build_billing_result(csv(body), **IDENTITY)
        assert (
            result["extensions"]["finops_lite"]["billing"][
                "daily_observation_metric_ids"
            ]
            == []
        )
    monthly = csv(
        "2026-09-01,Compute,300,USD,AWS,a,2026-10-01",
        header="Date,Service,Cost,Currency,Provider,RecordId,EndDate",
    )
    result = build_billing_result(monthly, **IDENTITY)
    assert (
        result["extensions"]["finops_lite"]["billing"]["daily_observation_metric_ids"]
        == []
    )
    assert len(result["extensions"]["finops_lite"]["billing"]["current"]["daily"]) == 1


def test_missing_comparison_never_becomes_zero_and_mixed_currency_fails():
    current = csv("2026-09-01,Compute,10,USD,AWS,x")
    result = build_billing_result(current, **IDENTITY)
    assert result["extensions"]["finops_lite"]["billing"]["comparison"] is None
    with pytest.raises(CCACBuildError, match="same currency"):
        build_billing_result(current, csv("2026-08-01,Compute,5,EUR,AWS,p"), **IDENTITY)
    with pytest.raises(CCACBuildError, match="overlap"):
        build_billing_result(current, deepcopy(current), **IDENTITY)


def test_json_boundary_rejects_duplicates_nonfinite_values_and_code_is_data():
    for text in ['{"a":1,"a":2}', '{"a":NaN}']:
        with pytest.raises(ValueError):
            loads_exact(text)
    result = build_billing_result(
        csv("2026-09-01,__import__(os).system,2,USD,AWS,x"), **IDENTITY
    )
    assert (
        result["extensions"]["finops_lite"]["billing"]["current"]["rows"][0]["service"]
        == "__import__(os).system"
    )
