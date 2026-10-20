"""Canonical CCAC producer for FinOps Lite cost summaries."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping

from . import __version__

SUPPORTED_CONTRACT_VERSIONS = {"1.0.0", "1.1.0"}
AWS_COST_EXPLORER_API_VERSION = "2017-10-25"
MONEY_QUANTUM = Decimal("0.01")


class CCACBuildError(ValueError):
    """Raised when source data cannot support a trustworthy CCAC result."""


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise CCACBuildError(f"Missing numeric value for {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CCACBuildError(f"Invalid numeric value for {field}: {value!r}") from exc
    if not result.is_finite():
        raise CCACBuildError(f"Non-finite numeric value for {field}: {value!r}")
    return result


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _percentage(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CCACBuildError(f"Invalid ISO date for {field}: {value!r}") from exc


def _parse_timestamp(value: str | datetime | None) -> str:
    if value is None:
        result = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CCACBuildError(f"Invalid RFC3339 timestamp: {value!r}") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id(value: str | None) -> str:
    try:
        return str(uuid.UUID(value)) if value else str(uuid.uuid4())
    except (ValueError, AttributeError) as exc:
        raise CCACBuildError(f"Invalid run UUID: {value!r}") from exc


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stable_component(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:48]}-{digest}"


def _metric(
    *,
    metric_id: str,
    name: str,
    value: Decimal | None,
    currency: str | None,
    basis: str,
    additivity: str,
    period: dict[str, str],
    dimensions: dict[str, Any],
    evidence_id: str,
    formula: str | None = None,
    input_metric_ids: list[str] | None = None,
    unknown_reason: str | None = None,
    preserve_precision: bool = False,
) -> dict[str, Any]:
    return {
        "id": metric_id,
        "name": name,
        "value": (
            None
            if value is None
            else (
                value
                if preserve_precision
                else (
                    _percentage(value)
                    if currency is None and "percentage" in name.lower()
                    else _money(value)
                )
            )
        ),
        "unknown_reason": unknown_reason,
        "unit": "currency" if currency else "percent",
        "currency": currency,
        "basis": basis,
        "additivity": additivity,
        "period": period,
        "dimensions": dimensions,
        "formula": formula,
        "input_metric_ids": input_metric_ids or [],
        "evidence_ids": [evidence_id],
        "quality_status": "valid",
    }


def build_ccac_result(
    summary: Mapping[str, Any],
    *,
    mode: str,
    source_type: str,
    source_version: str,
    run_id: str | None = None,
    generated_at: str | datetime | None = None,
    contract_version: str = "1.0.0",
) -> dict[str, Any]:
    """Build a reconciled CCAC tool_result from a complete FinOps Lite summary."""
    if mode not in {"illustrative", "real"}:
        raise CCACBuildError("mode must be 'illustrative' or 'real'")
    if not source_type or not source_version:
        raise CCACBuildError("source_type and source_version are required")
    if contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise CCACBuildError(f"Unsupported CCAC contract version: {contract_version}")
    if summary.get("group_by") != "SERVICE":
        raise CCACBuildError("CCAC 0.1 requires group_by=SERVICE")

    currency = str(summary.get("currency", ""))
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise CCACBuildError(f"Invalid ISO currency: {currency!r}")
    if contract_version == "1.1.0" and currency != "USD":
        raise CCACBuildError(
            "CCAC 1.1 Cloud scope currently supports USD only because its currency minor unit is explicitly 0.01"
        )
    if contract_version == "1.1.0" and mode == "real":
        if summary.get("aws_cost_metric") != "NetUnblendedCost":
            raise CCACBuildError(
                "Real CCAC 1.1 output requires AWS Cost Explorer NetUnblendedCost; no fallback is permitted"
            )
    if contract_version == "1.1.0" and mode == "illustrative":
        if summary.get("illustrative_cost_basis") != "net_cost":
            raise CCACBuildError(
                "Illustrative CCAC 1.1 output requires illustrative_cost_basis=net_cost"
            )
        if summary.get("illustrative_cloud_sources") != ["aws"]:
            raise CCACBuildError(
                "Illustrative CCAC 1.1 output requires AWS as the sole illustrative Cloud source"
            )
    source_window = summary.get("window") or {}
    start = _parse_date(source_window.get("start"), "window.start")
    inclusive_end = _parse_date(source_window.get("end"), "window.end")
    if inclusive_end < start:
        raise CCACBuildError("window.end must be on or after window.start")
    period = {
        "start": start.isoformat(),
        "end": (inclusive_end + timedelta(days=1)).isoformat(),
        "timezone": "UTC",
    }
    day_count = (inclusive_end - start).days + 1
    expected_comparison_window = {
        "start": (start - timedelta(days=day_count)).isoformat(),
        "end": (start - timedelta(days=1)).isoformat(),
    }
    if (
        contract_version == "1.1.0"
        and summary.get("comparison_window") != expected_comparison_window
    ):
        raise CCACBuildError(
            "CCAC 1.1 requires the immediately preceding equal-length comparison_window"
        )

    total = _decimal(summary.get("total_cost"), "total_cost")
    previous = _decimal(summary.get("previous_total_cost"), "previous_total_cost")
    top_groups = summary.get("top_groups")
    daily_trend = summary.get("daily_trend")
    daily_groups = summary.get("daily_groups")
    if not isinstance(top_groups, list):
        raise CCACBuildError("top_groups must contain the complete service breakdown")
    if not top_groups and total != 0:
        raise CCACBuildError("top_groups cannot be empty when total_cost is non-zero")
    if not isinstance(daily_trend, list) or not daily_trend:
        raise CCACBuildError("daily_trend must contain at least one daily value")
    if not isinstance(daily_groups, list):
        raise CCACBuildError(
            "daily_groups must contain the complete daily service breakdown"
        )

    service_rows: list[tuple[str, Decimal]] = []
    for index, row in enumerate(top_groups):
        if not isinstance(row, Mapping) or not str(row.get("group", "")).strip():
            raise CCACBuildError(f"top_groups[{index}].group is required")
        service_rows.append(
            (str(row["group"]), _decimal(row.get("cost"), f"top_groups[{index}].cost"))
        )
    service_sum = sum((cost for _, cost in service_rows), Decimal("0"))
    if abs(service_sum - total) > MONEY_QUANTUM:
        raise CCACBuildError(
            f"Service breakdown does not reconcile: services={_money(service_sum)} total={_money(total)}"
        )

    daily_rows: list[tuple[date, Decimal]] = []
    for index, row in enumerate(daily_trend):
        if not isinstance(row, Mapping):
            raise CCACBuildError(f"daily_trend[{index}] must be an object")
        day = _parse_date(row.get("date"), f"daily_trend[{index}].date")
        cost = _decimal(row.get("cost"), f"daily_trend[{index}].cost")
        daily_rows.append((day, cost))
    daily_sum = sum((cost for _, cost in daily_rows), Decimal("0"))
    if abs(daily_sum - total) > MONEY_QUANTUM:
        raise CCACBuildError(
            f"Daily trend does not reconcile: days={_money(daily_sum)} total={_money(total)}"
        )
    expected_days = {
        start + timedelta(days=offset)
        for offset in range((inclusive_end - start).days + 1)
    }
    actual_days = [day for day, _ in daily_rows]
    if len(actual_days) != len(set(actual_days)) or set(actual_days) != expected_days:
        raise CCACBuildError(
            "daily_trend must contain each source-window date exactly once"
        )

    day_service_rows: list[tuple[date, str, Decimal]] = []
    seen_day_services: set[tuple[date, str]] = set()
    for index, row in enumerate(daily_groups):
        if not isinstance(row, Mapping) or not str(row.get("group", "")).strip():
            raise CCACBuildError(
                f"daily_groups[{index}] requires date, group, and cost"
            )
        day = _parse_date(row.get("date"), f"daily_groups[{index}].date")
        service = str(row["group"])
        key = (day, service)
        if key in seen_day_services:
            raise CCACBuildError(f"duplicate daily service row for {day} and {service}")
        seen_day_services.add(key)
        day_service_rows.append(
            (day, service, _decimal(row.get("cost"), f"daily_groups[{index}].cost"))
        )
    for day, daily_cost in daily_rows:
        grouped_cost = sum(
            (cost for row_day, _, cost in day_service_rows if row_day == day),
            Decimal("0"),
        )
        if abs(grouped_cost - daily_cost) > MONEY_QUANTUM:
            raise CCACBuildError(
                f"Daily service breakdown does not reconcile for {day}"
            )
    for service, service_cost in service_rows:
        grouped_cost = sum(
            (
                cost
                for _, row_service, cost in day_service_rows
                if row_service == service
            ),
            Decimal("0"),
        )
        if abs(grouped_cost - service_cost) > MONEY_QUANTUM:
            raise CCACBuildError(f"Daily service rows do not reconcile for {service}")

    source_change_pct = summary.get("change_pct")
    if previous == 0:
        if source_change_pct is not None:
            raise CCACBuildError(
                "change_pct must be null when previous_total_cost is zero"
            )
    elif source_change_pct is not None:
        calculated_change_pct = ((total - previous) / previous) * Decimal("100")
        supplied_change_pct = _decimal(source_change_pct, "change_pct")
        if abs(calculated_change_pct - supplied_change_pct) > Decimal("0.1"):
            raise CCACBuildError(
                "change_pct does not reconcile to current and previous totals"
            )

    source_payload = dict(summary)
    canonical_source = _canonical_json(source_payload)
    source_hash = hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()
    generated_timestamp = _parse_timestamp(generated_at)
    source_id = f"source.finops-lite.{_stable_component(source_type)}"
    evidence_id = "evidence.finops-lite.cost-summary"
    metrics: list[dict[str, Any]] = []

    canonical_metric_id = (
        "metric.tech-spend.scope.cloud"
        if contract_version == "1.1.0"
        else "metric.cloud.total"
    )
    canonical_metric = _metric(
        metric_id=canonical_metric_id,
        name="Cloud cost",
        value=total,
        currency=currency,
        basis="observed",
        additivity="additive",
        period=period,
        dimensions={"scope": "cloud", "provider": "aws"},
        evidence_id=evidence_id,
    )
    if contract_version == "1.1.0":
        illustrative = mode == "illustrative"
        component_policy = (
            "Cloud & Capital technology-spend boundary policy: include each charge type when AWS returns it in the unfiltered Cost Explorer result."
            if not illustrative
            else "The illustrative scenario follows Cloud & Capital's technology-spend boundary policy without asserting that an AWS API returned any charge type."
        )
        canonical_metric["accounting_boundary"] = {
            "relationship": "canonical_scope_spend",
            "scope": "cloud",
            "canonical_owner": "finops-lite",
            "source_channel": "cloud_provider_billing",
            "cost_basis": "net_cost",
            "currency_minor_unit": 0.01,
            "inclusion_rules": [
                "Cloud-provider-billed services, including provider-billed native AI, present in the authoritative source summary.",
                component_policy,
            ],
            "exclusion_rules": [
                "Direct AI-vendor billing and SaaS invoice or entitlement charges outside cloud-provider billing."
            ],
            "coverage": "complete" if illustrative else "partial",
            "overlap": {
                "disposition": "resolved",
                "treatment": "Provider-billed native AI remains in Cloud; direct AI-vendor and SaaS billing channels are excluded.",
            },
            "cross_scope_treatments": {
                "provider_billed_ai": "included",
                "direct_ai_vendor": "excluded",
            },
            "component_treatments": {
                "credits": "included",
                "taxes": "included",
                "adjustments": "included",
                "shared_services": "included",
            },
            "allocation_of_metric_id": None,
            "total_eligible": illustrative,
            "eligibility_reason": (
                "The deterministic public scenario declares net_cost and AWS as its sole illustrative Cloud source; no account or AWS API was queried."
                if illustrative
                else "The connected AWS billing view is observed and reconciled but does not prove complete enterprise Cloud coverage across other AWS views, Azure, or Google Cloud."
            ),
        }
    metrics.append(canonical_metric)
    previous_period = period
    if contract_version == "1.1.0":
        previous_end = start
        previous_start = start - timedelta(days=day_count)
        previous_period = {
            "start": previous_start.isoformat(),
            "end": previous_end.isoformat(),
            "timezone": "UTC",
        }
    metrics.append(
        _metric(
            metric_id="metric.cloud.previous-total",
            name="Previous cloud cost",
            value=previous,
            currency=currency,
            basis="observed",
            additivity="additive",
            period=previous_period,
            dimensions={"scope": "cloud", "comparison": "previous_equal_length_period"},
            evidence_id=evidence_id,
        )
    )
    change = total - previous
    metrics.append(
        _metric(
            metric_id="metric.cloud.change-amount",
            name="Cloud cost change amount",
            value=change,
            currency=currency,
            basis="calculated",
            additivity="non_additive",
            period=period,
            dimensions={"scope": "cloud"},
            evidence_id=evidence_id,
            formula=f"{canonical_metric_id} - metric.cloud.previous-total",
            input_metric_ids=[canonical_metric_id, "metric.cloud.previous-total"],
        )
    )
    if previous == 0:
        change_pct = None
        change_basis = "unknown"
        change_formula = None
        unknown_reason = "Previous-period cost is zero; percentage change is undefined."
    else:
        change_pct = (change / previous) * Decimal("100")
        change_basis = "calculated"
        change_formula = f"({canonical_metric_id} - metric.cloud.previous-total) / metric.cloud.previous-total * 100"
        unknown_reason = None
    metrics.append(
        _metric(
            metric_id="metric.cloud.change-percentage",
            name="Cloud cost change percentage",
            value=change_pct,
            currency=None,
            basis=change_basis,
            additivity="ratio",
            period=period,
            dimensions={"scope": "cloud"},
            evidence_id=evidence_id,
            formula=change_formula,
            input_metric_ids=[canonical_metric_id, "metric.cloud.previous-total"],
            unknown_reason=unknown_reason,
        )
    )

    service_metric_ids = []
    for service, cost in service_rows:
        metric_id = f"metric.cloud.service.{_stable_component(service)}.cost"
        service_metric_ids.append(metric_id)
        metrics.append(
            _metric(
                metric_id=metric_id,
                name=f"{service} cost",
                value=cost,
                currency=currency,
                basis="observed",
                additivity="additive",
                period=period,
                dimensions={"scope": "cloud", "provider": "aws", "service": service},
                evidence_id=evidence_id,
            )
        )

    for day, cost in daily_rows:
        metrics.append(
            _metric(
                metric_id=f"metric.cloud.day.{day.isoformat()}.cost",
                name=f"Cloud cost for {day.isoformat()}",
                value=cost,
                currency=currency,
                basis="observed",
                additivity="additive",
                period={
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                    "timezone": "UTC",
                },
                dimensions={
                    "scope": "cloud",
                    "provider": "aws",
                    "date": day.isoformat(),
                },
                evidence_id=evidence_id,
            )
        )

    daily_service_metric_ids = []
    for day, service, cost in day_service_rows:
        metric_id = f"metric.cloud.service.{_stable_component(service)}.day.{day.isoformat()}.cost"
        daily_service_metric_ids.append(metric_id)
        metrics.append(
            _metric(
                metric_id=metric_id,
                name=f"{service} cost for {day.isoformat()}",
                value=cost,
                currency=currency,
                basis="observed",
                additivity="additive",
                period={
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                    "timezone": "UTC",
                },
                dimensions={
                    "scope": "cloud",
                    "provider": "aws",
                    "service": service,
                    "date": day.isoformat(),
                },
                evidence_id=evidence_id,
            )
        )

    return {
        "contract": f"ccac/{contract_version}",
        "document_type": "tool_result",
        "producer": {"name": "finops-lite", "version": __version__},
        "run_id": _run_id(run_id),
        "generated_at": generated_timestamp,
        "mode": mode,
        "period": period,
        "inputs": [
            {
                "id": source_id,
                "source_type": source_type,
                "source_version": source_version,
                "adapter_version": __version__,
                "content_sha256": source_hash,
                "access": (
                    "illustrative_fixture"
                    if mode == "illustrative"
                    else "external_read_only"
                ),
                "data_classification": (
                    "public_illustrative"
                    if mode == "illustrative"
                    else "customer_confidential"
                ),
                "lossy_mapping": True,
                "mapping_notes": (
                    [
                        (
                            "Deterministic illustrative net-cost summary modeled for the public scenario; no account or AWS API was queried."
                            if mode == "illustrative"
                            else "Actual AWS Cost Explorer NetUnblendedCost maps to CCAC net_cost; no charge-type filter is passed, and credits, taxes, adjustments, and shared services are included when AWS returns them."
                        ),
                        (
                            "Service-level illustrative summary; not an AWS query or resource-level billing export."
                            if mode == "illustrative"
                            else "Service-level AWS Cost Explorer summary; not resource-level FOCUS billing rows."
                        ),
                        (
                            "Illustrative AWS is the sole Cloud billing source for this scenario."
                            if mode == "illustrative"
                            else "One connected AWS billing view does not establish complete enterprise Cloud coverage."
                        ),
                    ]
                    if contract_version == "1.1.0"
                    else [
                        "Service-level AWS Cost Explorer summary; not resource-level FOCUS billing rows."
                    ]
                ),
            }
        ],
        "quality": {"status": "valid", "issues": []},
        "metrics": metrics,
        "findings": [],
        "opportunities": [],
        "evidence": [
            {
                "id": evidence_id,
                "kind": "source_query",
                "source_ids": [source_id],
                "description": (
                    (
                        "Deterministic illustrative net-cost summary modeled for the public scenario; no account or AWS API was queried."
                        if mode == "illustrative"
                        else "Actual service-level AWS Cost Explorer NetUnblendedCost query with no charge-type filter and an equal-length previous-period comparison."
                    )
                    if contract_version == "1.1.0"
                    else "Complete service-level AWS Cost Explorer summary with equal-length previous-period comparison."
                ),
                "locator": "canonical-json:summary",
                "observed_at": generated_timestamp,
                "content_sha256": source_hash,
            }
        ],
        "extensions": {
            "finops_lite": {
                "group_by": "SERVICE",
                **(
                    (
                        {
                            "illustrative_cost_basis": "net_cost",
                            "illustrative_cloud_sources": ["aws"],
                            "aws_api_queried": False,
                        }
                        if mode == "illustrative"
                        else {
                            "aws_cost_metric": "NetUnblendedCost",
                            "aws_filter": None,
                        }
                    )
                    if contract_version == "1.1.0"
                    else {}
                ),
                "source_window_end_semantics": "inclusive",
                "contract_period_end_semantics": "exclusive",
                "service_metric_ids": service_metric_ids,
                "daily_service_metric_ids": daily_service_metric_ids,
                "reconciliation": {
                    "service_sum": _money(service_sum),
                    "daily_sum": _money(daily_sum),
                    "total": _money(total),
                    "difference": _money(service_sum - total),
                    "tolerance": 0.01,
                    "status": "passed",
                },
            }
        },
    }


def illustrative_summary() -> dict[str, Any]:
    """Deterministic 21-day public fixture with one labeled spend spike."""
    daily_costs = [100.0 + (index % 3) for index in range(20)] + [175.0]
    start = date(2026, 7, 1)
    return {
        "schema_version": "1.0",
        "currency": "USD",
        "group_by": "SERVICE",
        "window": {"start": "2026-07-01", "end": "2026-07-21"},
        "total_cost": 2194.0,
        "previous_total_cost": 2000.0,
        "change_pct": 9.7,
        "top_groups": [
            {"group": "AmazonEC2", "cost": 1535.8, "pct_of_total": 70.0},
            {"group": "AmazonS3", "cost": 658.2, "pct_of_total": 30.0},
        ],
        "daily_trend": [
            {"date": (start + timedelta(days=index)).isoformat(), "cost": cost}
            for index, cost in enumerate(daily_costs)
        ],
        "daily_groups": [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "group": service,
                "cost": round(cost * share, 2),
            }
            for index, cost in enumerate(daily_costs)
            for service, share in (("AmazonEC2", 0.7), ("AmazonS3", 0.3))
        ],
    }


def illustrative_summary_1_1() -> dict[str, Any]:
    """Explicit 1.1 public scenario with declared illustrative lineage."""
    summary = illustrative_summary()
    summary["illustrative_cost_basis"] = "net_cost"
    summary["illustrative_cloud_sources"] = ["aws"]
    summary["comparison_window"] = {"start": "2026-06-10", "end": "2026-06-30"}
    return summary
