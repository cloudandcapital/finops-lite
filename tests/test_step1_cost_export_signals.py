"""
Offline tests for Step 1 command coverage.

These tests intentionally stub AWS interactions so they can run without
real credentials or network access.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime

from click.testing import CliRunner
import pytest

import finops_lite.cli as cli_module
from finops_lite.cli import cli
from finops_lite.signals.from_services import REQUIRED_COLUMNS


FOCUS_EXPORT_COLUMNS = [
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


@pytest.fixture
def stub_aws_connectivity(monkeypatch):
    """Bypass real AWS connectivity checks in CLI commands."""
    monkeypatch.setattr(
        cli_module,
        "_test_aws_connectivity",
        lambda *args, **kwargs: {
            "account_id": "123456789012",
            "user_arn": "arn:aws:iam::123456789012:user/test",
            "region": "us-east-1",
            "cost_explorer_status": "available",
        },
    )


def _patch_cost_explorer_service(
    monkeypatch,
    *,
    monthly_payload=None,
    compare_payload=None,
    focus_rows=None,
):
    """Patch CostExplorerService with a deterministic fake."""
    focus_rows = focus_rows or []

    class FakeCostExplorerService:
        def __init__(self, config):
            self.config = config

        def get_month_cost_overview(self, year, month):
            if monthly_payload is None:
                raise AssertionError("monthly payload not provided for fake service")
            return monthly_payload

        def compare_months(self, year_a, month_a, year_b, month_b):
            if compare_payload is None:
                raise AssertionError("compare payload not provided for fake service")
            return compare_payload

        def export_focus_lite(self, days=30, file=None):
            out = file or sys.stdout
            writer = csv.DictWriter(out, fieldnames=FOCUS_EXPORT_COLUMNS)
            writer.writeheader()
            for row in focus_rows:
                # Normalize legacy snake_case keys to FOCUS 1.0 names if needed
                normalized = {
                    "BilledCost": row.get("BilledCost") or row.get("cost", ""),
                    "ResourceId": row.get("ResourceId") or row.get("resource_id", ""),
                    "ServiceName": row.get("ServiceName") or row.get("service", ""),
                    "ChargePeriodStart": row.get("ChargePeriodStart") or row.get("time_window_start", ""),
                    "ChargePeriodEnd": row.get("ChargePeriodEnd") or row.get("time_window_end", ""),
                    "ChargeType": row.get("ChargeType", "Usage"),
                    "provider": row.get("provider", ""),
                    "currency": row.get("currency", ""),
                    "usage_amount": row.get("usage_amount", ""),
                    "usage_unit": row.get("usage_unit", ""),
                    "allocation_method": row.get("allocation_method", ""),
                    "allocation_confidence": row.get("allocation_confidence", ""),
                }
                writer.writerow(normalized)

    import finops_lite.core.cost_explorer as cost_explorer_module

    monkeypatch.setattr(
        cost_explorer_module, "CostExplorerService", FakeCostExplorerService
    )


def _write_focus_csv(path, csv_text):
    path.write_text(csv_text, encoding="utf-8")
    return path


def _create_service_rollup_from_focus_csv(focus_csv_path, rollup_csv_path):
    """
    Convert FOCUS-lite rows into the service rollup schema required by
    `signals from-services`.
    """
    totals = defaultdict(float)
    day_counts = defaultdict(set)

    with focus_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            service = (row.get("ServiceName") or row.get("service") or "Unknown").strip()
            totals[service] += float(row.get("BilledCost") or row.get("cost") or 0.0)
            day_counts[service].add(row.get("ChargePeriodStart") or row.get("time_window_start") or "")

    grand_total = sum(totals.values()) or 1.0

    with rollup_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "service_name",
                "total_cost",
                "percentage_of_total",
                "daily_average",
                "trend_direction",
                "trend_percentage",
                "trend_amount",
            ],
        )
        writer.writeheader()

        for service_name, total_cost in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        ):
            day_count = max(len(day_counts[service_name]), 1)
            writer.writerow(
                {
                    "service_name": service_name,
                    "total_cost": f"{total_cost:.2f}",
                    "percentage_of_total": f"{(total_cost / grand_total) * 100:.2f}",
                    "daily_average": f"{total_cost / day_count:.2f}",
                    # Single-window export does not include prior-period trend context.
                    "trend_direction": "stable",
                    "trend_percentage": "0.0",
                    "trend_amount": "0.0",
                }
            )


def test_cost_monthly_offline_stub_json_output(monkeypatch, stub_aws_connectivity):
    _patch_cost_explorer_service(
        monkeypatch,
        monthly_payload={
            "report_type": "cost_overview_month",
            "period_days": 31,
            "total_cost": 1234.56,
            "daily_average": 39.82,
            "trend": {
                "trend_direction": "up",
                "change_percentage": 12.3,
                "change_amount": 135.0,
                "current_period_cost": 1234.56,
                "previous_period_cost": 1099.56,
            },
            "service_breakdown": [
                {
                    "service_name": "Amazon EC2",
                    "total_cost": 800.0,
                    "percentage_of_total": 64.8,
                    "daily_average": 25.8,
                    "trend": {
                        "trend_direction": "up",
                        "change_percentage": 9.1,
                        "change_amount": 66.0,
                    },
                    "top_usage_types": [],
                }
            ],
            "currency": "USD",
            "generated_at": datetime(2026, 1, 31),
            "window": {"type": "calendar_month", "year": 2026, "month": 1, "label": "2026-01"},
            "window_start": datetime(2026, 1, 1),
            "window_end": datetime(2026, 2, 1),
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--no-cache", "cost", "monthly", "--month", "2026-01", "--format", "json"],
    )

    assert result.exit_code == 0
    assert '"report_type": "cost_overview_month"' in result.output
    assert '"label": "2026-01"' in result.output
    assert '"service_name": "Amazon EC2"' in result.output


def test_cost_compare_offline_stub_table_output(monkeypatch, stub_aws_connectivity):
    _patch_cost_explorer_service(
        monkeypatch,
        compare_payload={
            "comparison": {
                "current": {"label": "2026-01"},
                "baseline": {"label": "2025-12"},
                "total_delta": 120.0,
                "total_delta_percentage": 11.0,
                "service_deltas": [
                    {
                        "service_name": "Amazon EC2",
                        "current_cost": 700.0,
                        "baseline_cost": 600.0,
                        "delta": 100.0,
                        "delta_percentage": 16.7,
                    },
                    {
                        "service_name": "Amazon S3",
                        "current_cost": 120.0,
                        "baseline_cost": 100.0,
                        "delta": 20.0,
                        "delta_percentage": 20.0,
                    },
                ],
            }
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--no-cache",
            "cost",
            "compare",
            "--current",
            "2026-01",
            "--baseline",
            "2025-12",
        ],
    )

    assert result.exit_code == 0
    assert "Month Comparison" in result.output
    assert "2026-01" in result.output
    assert "2025-12" in result.output
    assert "Amazon EC2" in result.output


def test_export_focus_offline_stub_emits_focus_csv(monkeypatch, stub_aws_connectivity):
    _patch_cost_explorer_service(
        monkeypatch,
        focus_rows=[
            {
                "provider": "aws",
                "service": "Amazon EC2",
                "resource_id": "",
                "environment": "prod",
                "cost": "123.4567",
                "currency": "USD",
                "usage_amount": "10.0000",
                "usage_unit": "Hrs",
                "time_window_start": "2026-01-01",
                "time_window_end": "2026-01-02",
                "allocation_method": "direct",
                "allocation_confidence": "high",
            }
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["--no-cache", "export", "focus", "--days", "7"])

    assert result.exit_code == 0

    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert lines, "export focus should emit CSV output"
    assert lines[0].split(",") == FOCUS_EXPORT_COLUMNS
    assert "Amazon EC2" in result.output


def test_export_focus_csv_contract_mismatch_for_signals(
    monkeypatch, stub_aws_connectivity, tmp_path
):
    """
    Contract test: raw export-focus CSV is not the same schema that
    `signals from-services` requires today.
    """
    _patch_cost_explorer_service(
        monkeypatch,
        focus_rows=[
            {
                "provider": "aws",
                "service": "Amazon EC2",
                "resource_id": "",
                "environment": "prod",
                "cost": "150.0000",
                "currency": "USD",
                "usage_amount": "",
                "usage_unit": "",
                "time_window_start": "2026-01-01",
                "time_window_end": "2026-01-02",
                "allocation_method": "direct",
                "allocation_confidence": "medium",
            }
        ],
    )

    runner = CliRunner()
    export_result = runner.invoke(cli, ["--no-cache", "export", "focus", "--days", "30"])
    assert export_result.exit_code == 0

    focus_csv_path = _write_focus_csv(tmp_path / "focus-lite.csv", export_result.output)

    with focus_csv_path.open("r", encoding="utf-8", newline="") as f:
        exported_columns = set(csv.DictReader(f).fieldnames or [])

    missing_for_signals = REQUIRED_COLUMNS - exported_columns
    assert missing_for_signals, "raw focus export should miss required signals columns"
    assert "service_name" in missing_for_signals

    signals_result = runner.invoke(
        cli,
        [
            "signals",
            "from-services",
            "--file",
            str(focus_csv_path),
            "--format",
            "json",
        ],
    )

    assert signals_result.exit_code == 1
    assert isinstance(signals_result.exception, ValueError)
    assert "Missing columns" in str(signals_result.exception)


def test_signals_from_services_with_rollup_derived_from_focus_csv(
    monkeypatch, stub_aws_connectivity, tmp_path
):
    _patch_cost_explorer_service(
        monkeypatch,
        focus_rows=[
            {
                "provider": "aws",
                "service": "Amazon EC2",
                "resource_id": "",
                "environment": "prod",
                "cost": "120.0000",
                "currency": "USD",
                "usage_amount": "5.0000",
                "usage_unit": "Hrs",
                "time_window_start": "2026-01-01",
                "time_window_end": "2026-01-02",
                "allocation_method": "direct",
                "allocation_confidence": "high",
            },
            {
                "provider": "aws",
                "service": "Amazon S3",
                "resource_id": "",
                "environment": "prod",
                "cost": "30.0000",
                "currency": "USD",
                "usage_amount": "100.0000",
                "usage_unit": "GB-Mo",
                "time_window_start": "2026-01-01",
                "time_window_end": "2026-01-02",
                "allocation_method": "direct",
                "allocation_confidence": "high",
            },
        ],
    )

    runner = CliRunner()
    export_result = runner.invoke(cli, ["--no-cache", "export", "focus", "--days", "30"])
    assert export_result.exit_code == 0

    focus_csv_path = _write_focus_csv(tmp_path / "focus-lite.csv", export_result.output)
    rollup_csv_path = tmp_path / "services-rollup.csv"
    _create_service_rollup_from_focus_csv(focus_csv_path, rollup_csv_path)

    signals_result = runner.invoke(
        cli,
        [
            "signals",
            "from-services",
            "--file",
            str(rollup_csv_path),
            "--period",
            "Last 30 days",
            "--format",
            "json",
        ],
    )

    assert signals_result.exit_code == 0
    payload = json.loads(signals_result.output.strip())
    assert isinstance(payload, list)
    assert payload, "signals JSON output should contain at least one signal"
    assert any(item.get("id") == "concentration_risk" for item in payload)
