"""
Auto-detect billing CSV provider from column signatures and normalize to FOCUS 1.0.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterator, TextIO

# FOCUS 1.0 output columns (in order)
FOCUS_FIELDNAMES = [
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

# Column signatures for each provider
_GCP_REQUIRED = {"usage_start_time", "service.description"}
_AZURE_REQUIRED = {"BillingCurrency", "CostInBillingCurrency", "SubscriptionId"}
_FOCUS_REQUIRED = {"BilledCost", "ChargePeriodStart", "ServiceName"}


def detect_provider(columns: set[str]) -> str:
    """Return 'gcp', 'azure', 'focus' (already normalized), or raise ValueError."""
    if _GCP_REQUIRED.issubset(columns):
        return "gcp"
    if _AZURE_REQUIRED.intersection(columns):
        return "azure"
    if _FOCUS_REQUIRED.issubset(columns):
        return "focus"
    raise ValueError(
        f"Cannot auto-detect provider from columns: {sorted(columns)}. "
        "Expected Azure (BillingCurrency/CostInBillingCurrency/SubscriptionId), "
        "GCP (usage_start_time + service.description), "
        "or FOCUS (BilledCost + ChargePeriodStart + ServiceName)."
    )


def normalize_to_focus(path: Path, file: TextIO = sys.stdout) -> None:
    """Read a billing CSV, auto-detect provider, write FOCUS 1.0 CSV to file."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file is empty or missing a header row: {path}")
        columns = set(reader.fieldnames)
        provider = detect_provider(columns)

        writer = csv.DictWriter(file, fieldnames=FOCUS_FIELDNAMES, lineterminator="\n")
        writer.writeheader()

        rows: Iterator
        if provider == "focus":
            rows = _passthrough(reader)
        elif provider == "azure":
            rows = _normalize_azure(reader)
        else:
            rows = _normalize_gcp(reader)

        for row in rows:
            writer.writerow(row)


def _passthrough(reader: csv.DictReader) -> Iterator[dict]:
    for row in reader:
        yield {f: row.get(f, "") for f in FOCUS_FIELDNAMES}


def _normalize_azure(reader: csv.DictReader) -> Iterator[dict]:
    for row in reader:
        cost = row.get("CostInBillingCurrency") or row.get("Cost") or "0"
        currency = row.get("BillingCurrency") or row.get("Currency") or "USD"
        date = row.get("Date") or row.get("UsageDate") or ""
        service = (
            row.get("ServiceName")
            or row.get("ProductName")
            or row.get("MeterCategory")
            or ""
        )
        resource_id = row.get("ResourceId") or row.get("InstanceName") or ""
        charge_type = row.get("ChargeType") or "Usage"
        period_end = _next_day(date) if date else ""
        yield {
            "BilledCost": cost,
            "ResourceId": resource_id,
            "ServiceName": service,
            "ChargePeriodStart": date,
            "ChargePeriodEnd": period_end,
            "ChargeType": charge_type,
            "provider": "azure",
            "currency": currency,
            "usage_amount": row.get("Quantity") or "",
            "usage_unit": row.get("UnitOfMeasure") or "",
            "allocation_method": "direct",
            "allocation_confidence": "medium",
        }


def _normalize_gcp(reader: csv.DictReader) -> Iterator[dict]:
    for row in reader:
        cost = row.get("cost") or "0"
        currency = row.get("currency") or "USD"
        start = row.get("usage_start_time") or ""
        end = row.get("usage_end_time") or ""
        service = row.get("service.description") or ""
        resource_id = row.get("resource.name") or row.get("sku.description") or ""
        usage_amount = row.get("usage.amount") or ""
        usage_unit = row.get("usage.unit") or ""
        # Truncate ISO timestamps to date portion for consistency
        start_date = start[:10] if start else ""
        end_date = end[:10] if end else ""
        yield {
            "BilledCost": cost,
            "ResourceId": resource_id,
            "ServiceName": service,
            "ChargePeriodStart": start_date,
            "ChargePeriodEnd": end_date,
            "ChargeType": "Usage",
            "provider": "gcp",
            "currency": currency,
            "usage_amount": usage_amount,
            "usage_unit": usage_unit,
            "allocation_method": "direct",
            "allocation_confidence": "medium",
        }


def _next_day(date_str: str) -> str:
    """Return the next calendar day as YYYY-MM-DD, or empty string on failure."""
    try:
        from datetime import date, timedelta

        d = date.fromisoformat(date_str[:10])
        return (d + timedelta(days=1)).isoformat()
    except Exception:
        return ""
