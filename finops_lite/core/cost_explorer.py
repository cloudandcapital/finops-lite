"""
AWS Cost Explorer service integration.
Handles all cost data retrieval and analysis.

Upgrades in this version:
- Adds month-window helpers (YYYY-MM monthly reporting)
- Uses REAL period length (days) for daily averages per service
- Adds compare helpers for month vs month
"""

import csv
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, TextIO, Tuple

from ..utils.config import FinOpsConfig

logger = logging.getLogger(__name__)


@dataclass
class CostData:
    """Cost data structure."""

    service: str
    cost: Decimal
    currency: str
    period_start: datetime
    period_end: datetime
    usage_quantity: Optional[Decimal] = None
    unit: Optional[str] = None


@dataclass
class CostTrend:
    """Cost trend analysis."""

    current_period_cost: Decimal
    previous_period_cost: Decimal
    change_amount: Decimal
    change_percentage: float
    trend_direction: str  # 'up', 'down', 'stable'


@dataclass
class ServiceCostBreakdown:
    """Service-level cost breakdown."""

    service_name: str
    total_cost: Decimal
    percentage_of_total: float
    daily_average: Decimal
    trend: CostTrend
    top_usage_types: List[Dict[str, Any]]


@dataclass
class FocusLiteRecord:
    """
    Normalized record for FOCUS-lite style exports.

    This is intentionally AWS-first but schema-aware so other providers
    can be added later without changing downstream tooling.
    """

    provider: str  # "aws" for now
    service: str  # AWS service name (e.g., "Amazon EC2")
    resource_id: Optional[str]  # None in v1 (SERVICE-level only)
    environment: str  # "prod", "staging", "dev", "unknown"
    cost: Decimal  # Blended cost amount for the period
    currency: str  # Usually "USD"
    usage_amount: Optional[Decimal]
    usage_unit: Optional[str]
    time_window_start: date
    time_window_end: date
    allocation_method: str  # "direct", "shared", "inferred"
    allocation_confidence: str  # "high", "medium", "low"


class CostExplorerService:
    """Service for interacting with AWS Cost Explorer API."""

    def __init__(self, config: FinOpsConfig):
        self.config = config
        self.session = config.get_boto3_session()
        self.ce_client = self.session.client("ce")

    # ----------------------------
    # Window helpers
    # ----------------------------
    def _month_window(self, year: int, month: int) -> Tuple[datetime, datetime, int]:
        """
        Return (start_datetime, end_datetime, days) where end is exclusive for CE.

        CE expects:
          Start: inclusive
          End: exclusive
        """
        if month < 1 or month > 12:
            raise ValueError("month must be 1..12")

        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)

        days = (end - start).days
        return (
            datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.min.time()),
            days,
        )

    def _previous_month(self, year: int, month: int) -> Tuple[int, int]:
        if month == 1:
            return year - 1, 12
        return year, month - 1

    # ----------------------------
    # Core CE calls
    # ----------------------------
    def get_cost_and_usage(
        self,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "DAILY",
        group_by: Optional[List[str]] = None,
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get cost and usage data from AWS Cost Explorer.

        Args:
            start_date: Start datetime for cost analysis
            end_date: End datetime (exclusive)
            granularity: DAILY, MONTHLY, or HOURLY
            group_by: List of dimensions to group by (e.g., ['SERVICE'])
            metrics: List of metrics to retrieve (e.g., ['BlendedCost'])

        Returns:
            Cost and usage data from AWS Cost Explorer
        """
        if not metrics:
            metrics = ["BlendedCost", "UnblendedCost", "UsageQuantity"]

        if not group_by:
            group_by = ["SERVICE"]

        # CE dates are YYYY-MM-DD strings; use date portion
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        logger.info(
            f"Fetching cost data from {start_str} to {end_str} (granularity={granularity})"
        )

        group_by_params = [{"Type": "DIMENSION", "Key": dim} for dim in group_by]

        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={"Start": start_str, "End": end_str},
                Granularity=granularity,
                Metrics=metrics,
                GroupBy=group_by_params,
            )
            logger.debug(
                f"Retrieved {len(response.get('ResultsByTime', []))} time periods"
            )
            return response
        except Exception as e:
            logger.error(f"Error fetching cost data: {e}")
            raise

    # ----------------------------
    # FOCUS-lite helpers
    # ----------------------------
    def get_focus_lite_records(self, days: int = 30) -> List[FocusLiteRecord]:
        """
        Normalize AWS cost data for the last N days into a FOCUS-lite shape.

        v1 is SERVICE-level only (no resource IDs yet), which still plays
        nicely with downstream analysis and spreadsheets.
        """
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.min.time())

        response = self.get_cost_and_usage(
            start_dt,
            end_dt,
            granularity="DAILY",
            group_by=["SERVICE"],
            metrics=["BlendedCost", "UsageQuantity"],
        )

        records: List[FocusLiteRecord] = []

        for time_period in response.get("ResultsByTime", []):
            time_info = time_period.get("TimePeriod", {})
            period_start_str = time_info.get("Start")
            period_end_str = time_info.get("End")

            try:
                period_start = (
                    datetime.strptime(period_start_str, "%Y-%m-%d").date()
                    if period_start_str
                    else start_date
                )
            except Exception:
                period_start = start_date

            try:
                period_end = (
                    datetime.strptime(period_end_str, "%Y-%m-%d").date()
                    if period_end_str
                    else end_date
                )
            except Exception:
                period_end = end_date

            for group in time_period.get("Groups", []):
                service_name = group.get("Keys", ["Unknown"])[0]
                metrics = group.get("Metrics", {})

                blended_cost = metrics.get("BlendedCost", {})
                amount_str = blended_cost.get("Amount", "0")
                currency = blended_cost.get("Unit", "USD")
                try:
                    cost_amount = Decimal(amount_str)
                except Exception:
                    cost_amount = Decimal("0")

                usage_amount: Optional[Decimal] = None
                usage_unit: Optional[str] = None
                usage = metrics.get("UsageQuantity")
                if usage:
                    usage_amount_str = usage.get("Amount", "0")
                    try:
                        usage_amount = Decimal(usage_amount_str)
                    except Exception:
                        usage_amount = None
                    usage_unit = usage.get("Unit")

                record = FocusLiteRecord(
                    provider="aws",
                    service=service_name,
                    resource_id=None,
                    environment="unknown",
                    cost=cost_amount,
                    currency=currency,
                    usage_amount=usage_amount,
                    usage_unit=usage_unit,
                    time_window_start=period_start,
                    time_window_end=period_end,
                    allocation_method="direct",
                    allocation_confidence="medium",
                )
                records.append(record)

        return records

    def export_focus_lite(self, days: int = 30, file: TextIO = sys.stdout) -> None:
        """Export FOCUS 1.0 compliant CSV to file (stdout by default)."""
        records = self.get_focus_lite_records(days=days)

        fieldnames = [
            "BilledCost",
            "ResourceId",
            "ServiceName",
            "ChargePeriodStart",
            "ChargePeriodEnd",
            "ChargeType",
            "provider",
            "currency",
            "usage_amount",
            "usage_unit",
            "allocation_method",
            "allocation_confidence",
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for r in records:
            writer.writerow(
                {
                    "BilledCost": f"{r.cost:.4f}",
                    "ResourceId": r.resource_id or "",
                    "ServiceName": r.service,
                    "ChargePeriodStart": r.time_window_start.isoformat(),
                    "ChargePeriodEnd": r.time_window_end.isoformat(),
                    "ChargeType": "Usage",
                    "provider": r.provider,
                    "currency": r.currency,
                    "usage_amount": (
                        "" if r.usage_amount is None else f"{r.usage_amount:.4f}"
                    ),
                    "usage_unit": r.usage_unit or "",
                    "allocation_method": r.allocation_method,
                    "allocation_confidence": r.allocation_confidence,
                }
            )

    # ----------------------------
    # Overviews
    # ----------------------------
    def get_monthly_cost_overview(self, days: int = 30) -> Dict[str, Any]:
        """
        Get a comprehensive cost overview for the last N days,
        compared to the previous N-day period.
        """
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.min.time())

        current_data = self.get_cost_and_usage(
            start_dt,
            end_dt,
            granularity="DAILY",
            group_by=["SERVICE"],
            metrics=["BlendedCost", "UnblendedCost", "UsageQuantity"],
        )

        prev_end = start_date
        prev_start = prev_end - timedelta(days=days)
        prev_start_dt = datetime.combine(prev_start, datetime.min.time())
        prev_end_dt = datetime.combine(prev_end, datetime.min.time())

        previous_data = self.get_cost_and_usage(
            prev_start_dt,
            prev_end_dt,
            granularity="DAILY",
            group_by=["SERVICE"],
            metrics=["BlendedCost"],
        )

        analysis = self._analyze_cost_data(
            current_data=current_data,
            previous_data=previous_data,
            days=days,
            window_start=start_dt,
            window_end=end_dt,
            window={"type": "rolling_days", "days": days},
        )
        return analysis

    def get_month_cost_overview(self, year: int, month: int) -> Dict[str, Any]:
        """
        Get a month-window cost overview (YYYY-MM),
        compared to the previous month.
        """
        start_dt, end_dt, days = self._month_window(year, month)
        prev_y, prev_m = self._previous_month(year, month)
        prev_start_dt, prev_end_dt, _prev_days = self._month_window(prev_y, prev_m)

        current_data = self.get_cost_and_usage(
            start_dt,
            end_dt,
            granularity="DAILY",
            group_by=["SERVICE"],
            metrics=["BlendedCost", "UnblendedCost", "UsageQuantity"],
        )

        previous_data = self.get_cost_and_usage(
            prev_start_dt,
            prev_end_dt,
            granularity="DAILY",
            group_by=["SERVICE"],
            metrics=["BlendedCost"],
        )

        analysis = self._analyze_cost_data(
            current_data=current_data,
            previous_data=previous_data,
            days=days,
            window_start=start_dt,
            window_end=end_dt,
            window={
                "type": "calendar_month",
                "year": year,
                "month": month,
                "label": f"{year:04d}-{month:02d}",
            },
            report_type="cost_overview_month",
        )
        return analysis

    def compare_months(
        self, year_a: int, month_a: int, year_b: int, month_b: int
    ) -> Dict[str, Any]:
        """
        Compare month A vs month B (A = current, B = baseline).
        Returns an overview-shaped dict plus a 'comparison' section.
        """
        a = self.get_month_cost_overview(year_a, month_a)
        b = self.get_month_cost_overview(year_b, month_b)

        # Build service maps
        a_services = {s.service_name: s for s in a.get("service_breakdown", [])}
        b_services = {s.service_name: s for s in b.get("service_breakdown", [])}

        all_names = set(a_services.keys()) | set(b_services.keys())

        rows = []
        for name in all_names:
            a_cost = (
                a_services.get(name).total_cost if name in a_services else Decimal("0")
            )
            b_cost = (
                b_services.get(name).total_cost if name in b_services else Decimal("0")
            )
            delta = a_cost - b_cost
            pct = (
                float((delta / b_cost) * 100)
                if b_cost > 0
                else (100.0 if a_cost > 0 else 0.0)
            )

            rows.append(
                {
                    "service_name": name,
                    "current_cost": a_cost,
                    "baseline_cost": b_cost,
                    "delta": delta,
                    "delta_percentage": pct,
                }
            )

        rows.sort(key=lambda r: abs(r["delta"]), reverse=True)

        result = {
            **a,
            "report_type": "cost_compare_months",
            "comparison": {
                "current": {
                    "year": year_a,
                    "month": month_a,
                    "label": f"{year_a:04d}-{month_a:02d}",
                },
                "baseline": {
                    "year": year_b,
                    "month": month_b,
                    "label": f"{year_b:04d}-{month_b:02d}",
                },
                "service_deltas": rows[:50],  # keep it bounded
                "total_delta": a["total_cost"] - b["total_cost"],
                "total_delta_percentage": (
                    float(((a["total_cost"] - b["total_cost"]) / b["total_cost"]) * 100)
                    if b["total_cost"] > 0
                    else (100.0 if a["total_cost"] > 0 else 0.0)
                ),
            },
        }
        return result

    # ----------------------------
    # Analysis
    # ----------------------------
    def _analyze_cost_data(
        self,
        current_data: Dict[str, Any],
        previous_data: Dict[str, Any],
        days: int,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
        window: Optional[Dict[str, Any]] = None,
        report_type: str = "cost_overview",
    ) -> Dict[str, Any]:
        current_total = self._calculate_total_cost(current_data)
        previous_total = self._calculate_total_cost(previous_data)

        trend = self._calculate_trend(current_total, previous_total)
        service_breakdown = self._get_service_breakdown(
            current_data=current_data,
            previous_data=previous_data,
            days=days,
        )

        daily_average = (current_total / days) if days > 0 else Decimal("0")

        top_services = sorted(
            service_breakdown, key=lambda x: x.total_cost, reverse=True
        )[:10]

        return {
            "report_type": report_type,
            "period_days": days,
            "total_cost": current_total,
            "daily_average": daily_average,
            "trend": trend,
            "service_breakdown": top_services,
            "currency": "USD",
            "generated_at": datetime.now(),
            "window": window or {},
            "window_start": window_start,
            "window_end": window_end,
        }

    def _calculate_total_cost(self, cost_data: Dict[str, Any]) -> Decimal:
        total = Decimal("0")
        for time_period in cost_data.get("ResultsByTime", []):
            if time_period.get("Total"):
                blended_cost = time_period["Total"].get("BlendedCost", {})
                amount = blended_cost.get("Amount", "0")
                total += Decimal(amount)
        return total

    def _calculate_trend(self, current: Decimal, previous: Decimal) -> CostTrend:
        change_amount = current - previous
        if previous > 0:
            change_percentage = float((change_amount / previous) * 100)
        else:
            change_percentage = 0.0

        if abs(change_percentage) < 5:
            trend_direction = "stable"
        elif change_percentage > 0:
            trend_direction = "up"
        else:
            trend_direction = "down"

        return CostTrend(
            current_period_cost=current,
            previous_period_cost=previous,
            change_amount=change_amount,
            change_percentage=change_percentage,
            trend_direction=trend_direction,
        )

    def _get_service_breakdown(
        self,
        current_data: Dict[str, Any],
        previous_data: Dict[str, Any],
        days: int,
    ) -> List[ServiceCostBreakdown]:
        # Aggregate costs by service for current period
        current_services: Dict[str, Decimal] = {}
        for time_period in current_data.get("ResultsByTime", []):
            for group in time_period.get("Groups", []):
                service_name = group["Keys"][0] if group.get("Keys") else "Unknown"
                metrics = group.get("Metrics", {})
                blended_cost = metrics.get("BlendedCost", {})
                amount = Decimal(blended_cost.get("Amount", "0"))
                current_services[service_name] = (
                    current_services.get(service_name, Decimal("0")) + amount
                )

        # Aggregate costs by service for previous period
        previous_services: Dict[str, Decimal] = {}
        for time_period in previous_data.get("ResultsByTime", []):
            for group in time_period.get("Groups", []):
                service_name = group["Keys"][0] if group.get("Keys") else "Unknown"
                metrics = group.get("Metrics", {})
                blended_cost = metrics.get("BlendedCost", {})
                amount = Decimal(blended_cost.get("Amount", "0"))
                previous_services[service_name] = (
                    previous_services.get(service_name, Decimal("0")) + amount
                )

        total_current = (
            sum(current_services.values()) if current_services else Decimal("0")
        )

        breakdown: List[ServiceCostBreakdown] = []
        for service_name, current_cost in current_services.items():
            previous_cost = previous_services.get(service_name, Decimal("0"))

            percentage = (
                float((current_cost / total_current) * 100)
                if total_current > 0
                else 0.0
            )
            daily_average = (current_cost / days) if days > 0 else Decimal("0")

            service_trend = self._calculate_trend(current_cost, previous_cost)

            breakdown.append(
                ServiceCostBreakdown(
                    service_name=service_name,
                    total_cost=current_cost,
                    percentage_of_total=percentage,
                    daily_average=daily_average,
                    trend=service_trend,
                    top_usage_types=[],
                )
            )

        breakdown.sort(key=lambda x: x.total_cost, reverse=True)
        return breakdown
