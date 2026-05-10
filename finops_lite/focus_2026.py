"""
FOCUS 2026 export and validator for FinOps Lite.

FOCUS (FinOps Open Cost and Usage Specification) 2026 extends FOCUS 1.0 with:
  - Mandatory billing account and sub-account columns
  - Standardized ChargeCategory / ChargeClass taxonomy
  - EffectiveCost, ListCost, ContractedCost alongside BilledCost
  - Commitment discount tracking (RI, Savings Plans, CUDs)
  - Resource-level identifiers (ResourceType, ResourceName, ResourceId)
  - Tag normalization (Tags/* columns)
  - Pricing metadata (SkuId, SkuPriceId, PricingUnit, PricingQuantity)
  - ConsumedUnit / ConsumedQuantity (actual usage vs pricing unit)
  - RegionId / RegionName standardized naming
  - ChargeClass: "Correction" for retroactive adjustments (new in 2026)
  - InvoiceIssuerName (for reseller / marketplace scenarios)

Reference: https://focus.finops.org  (FOCUS 2026 working draft)
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, TextIO


# ── FOCUS 2026 column names ───────────────────────────────────────────────────

FOCUS_2026_COLUMNS = [
    # Billing account hierarchy
    "BillingAccountId",
    "BillingAccountName",
    "SubAccountId",
    "SubAccountName",
    "InvoiceIssuerName",
    # Provider & service
    "ProviderName",
    "ServiceName",
    "ServiceCategory",
    # Region
    "RegionId",
    "RegionName",
    # Resource
    "ResourceId",
    "ResourceName",
    "ResourceType",
    # Charge time
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    # Charge classification
    "ChargeCategory",      # Usage | Purchase | Tax | Credit | Adjustment
    "ChargeClass",         # Standard | Correction  (NEW in 2026)
    "ChargeDescription",
    "ChargeFrequency",     # One-Time | Recurring | Usage-Based
    # Pricing
    "SkuId",
    "SkuPriceId",
    "PricingUnit",
    "PricingQuantity",
    "ListUnitPrice",
    "ListCost",
    "ContractedUnitPrice",
    "ContractedCost",
    # Consumption
    "ConsumedUnit",
    "ConsumedQuantity",
    # Costs
    "BilledCost",
    "EffectiveCost",
    # Currency
    "BillingCurrency",
    # Commitment discounts
    "CommitmentDiscountId",
    "CommitmentDiscountName",
    "CommitmentDiscountType",    # Reserved | Savings-Plan | Committed-Use
    "CommitmentDiscountStatus",  # Used | Unused
    # Capacity reservations
    "CapacityReservationId",
    "CapacityReservationStatus",
    # Tags (free-form; convention: Tags/key)
    "Tags",
    # FOCUS compliance metadata
    "x_focus_schema_version",
]

REQUIRED_COLUMNS = {
    "BillingAccountId",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "ChargeCategory",
    "ChargeClass",
    "BilledCost",
    "EffectiveCost",
    "BillingCurrency",
    "ProviderName",
    "ServiceName",
}

VALID_CHARGE_CATEGORIES = {"Usage", "Purchase", "Tax", "Credit", "Adjustment"}
VALID_CHARGE_CLASSES = {"Standard", "Correction"}
VALID_CHARGE_FREQUENCIES = {"One-Time", "Recurring", "Usage-Based", ""}
VALID_COMMITMENT_TYPES = {"Reserved", "Savings-Plan", "Committed-Use", ""}
VALID_COMMITMENT_STATUSES = {"Used", "Unused", ""}
FOCUS_SCHEMA_VERSION = "2026.0"


# ── FOCUS 2026 record dataclass ───────────────────────────────────────────────

@dataclass
class Focus2026Record:
    """
    A single normalized FOCUS 2026 billing record.

    All monetary amounts are Decimal to preserve precision.
    Empty optional strings default to "" (not None) for clean CSV output.
    """

    # Required — billing hierarchy
    billing_account_id: str
    billing_account_name: str = ""
    sub_account_id: str = ""
    sub_account_name: str = ""
    invoice_issuer_name: str = ""

    # Required — provider & service
    provider_name: str = "aws"
    service_name: str = ""
    service_category: str = ""

    # Region
    region_id: str = ""
    region_name: str = ""

    # Resource
    resource_id: str = ""
    resource_name: str = ""
    resource_type: str = ""

    # Required — charge time
    billing_period_start: date = field(default_factory=date.today)
    billing_period_end: date = field(default_factory=date.today)
    charge_period_start: date = field(default_factory=date.today)
    charge_period_end: date = field(default_factory=date.today)

    # Required — charge classification
    charge_category: str = "Usage"
    charge_class: str = "Standard"
    charge_description: str = ""
    charge_frequency: str = "Usage-Based"

    # Pricing
    sku_id: str = ""
    sku_price_id: str = ""
    pricing_unit: str = ""
    pricing_quantity: Optional[Decimal] = None
    list_unit_price: Optional[Decimal] = None
    list_cost: Optional[Decimal] = None
    contracted_unit_price: Optional[Decimal] = None
    contracted_cost: Optional[Decimal] = None

    # Consumption
    consumed_unit: str = ""
    consumed_quantity: Optional[Decimal] = None

    # Required — costs
    billed_cost: Decimal = Decimal("0")
    effective_cost: Decimal = Decimal("0")
    billing_currency: str = "USD"

    # Commitment discounts
    commitment_discount_id: str = ""
    commitment_discount_name: str = ""
    commitment_discount_type: str = ""
    commitment_discount_status: str = ""

    # Capacity reservations
    capacity_reservation_id: str = ""
    capacity_reservation_status: str = ""

    # Tags (serialized as JSON string or "key=value,key2=value2")
    tags: str = ""

    def to_dict(self) -> Dict[str, str]:
        def _fmt(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, Decimal):
                return f"{v:.4f}"
            if isinstance(v, date):
                return v.isoformat()
            return str(v)

        return {
            "BillingAccountId": _fmt(self.billing_account_id),
            "BillingAccountName": _fmt(self.billing_account_name),
            "SubAccountId": _fmt(self.sub_account_id),
            "SubAccountName": _fmt(self.sub_account_name),
            "InvoiceIssuerName": _fmt(self.invoice_issuer_name),
            "ProviderName": _fmt(self.provider_name),
            "ServiceName": _fmt(self.service_name),
            "ServiceCategory": _fmt(self.service_category),
            "RegionId": _fmt(self.region_id),
            "RegionName": _fmt(self.region_name),
            "ResourceId": _fmt(self.resource_id),
            "ResourceName": _fmt(self.resource_name),
            "ResourceType": _fmt(self.resource_type),
            "BillingPeriodStart": _fmt(self.billing_period_start),
            "BillingPeriodEnd": _fmt(self.billing_period_end),
            "ChargePeriodStart": _fmt(self.charge_period_start),
            "ChargePeriodEnd": _fmt(self.charge_period_end),
            "ChargeCategory": _fmt(self.charge_category),
            "ChargeClass": _fmt(self.charge_class),
            "ChargeDescription": _fmt(self.charge_description),
            "ChargeFrequency": _fmt(self.charge_frequency),
            "SkuId": _fmt(self.sku_id),
            "SkuPriceId": _fmt(self.sku_price_id),
            "PricingUnit": _fmt(self.pricing_unit),
            "PricingQuantity": _fmt(self.pricing_quantity),
            "ListUnitPrice": _fmt(self.list_unit_price),
            "ListCost": _fmt(self.list_cost),
            "ContractedUnitPrice": _fmt(self.contracted_unit_price),
            "ContractedCost": _fmt(self.contracted_cost),
            "ConsumedUnit": _fmt(self.consumed_unit),
            "ConsumedQuantity": _fmt(self.consumed_quantity),
            "BilledCost": _fmt(self.billed_cost),
            "EffectiveCost": _fmt(self.effective_cost),
            "BillingCurrency": _fmt(self.billing_currency),
            "CommitmentDiscountId": _fmt(self.commitment_discount_id),
            "CommitmentDiscountName": _fmt(self.commitment_discount_name),
            "CommitmentDiscountType": _fmt(self.commitment_discount_type),
            "CommitmentDiscountStatus": _fmt(self.commitment_discount_status),
            "CapacityReservationId": _fmt(self.capacity_reservation_id),
            "CapacityReservationStatus": _fmt(self.capacity_reservation_status),
            "Tags": _fmt(self.tags),
            "x_focus_schema_version": FOCUS_SCHEMA_VERSION,
        }


# ── FOCUS 2026 exporter ───────────────────────────────────────────────────────

def export_focus_2026(records: List[Focus2026Record], file: TextIO = sys.stdout) -> int:
    """
    Write records to file as FOCUS 2026 CSV.

    Returns the number of rows written.
    """
    writer = csv.DictWriter(file, fieldnames=FOCUS_2026_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in records:
        writer.writerow(r.to_dict())
    return len(records)


def from_focus_1_record(
    provider: str,
    service: str,
    cost: Decimal,
    currency: str,
    period_start: date,
    period_end: date,
    billing_account_id: str = "unknown",
    usage_amount: Optional[Decimal] = None,
    usage_unit: Optional[str] = None,
    region_id: str = "",
    resource_id: str = "",
    tags: Dict[str, str] | None = None,
) -> Focus2026Record:
    """
    Convert a FOCUS 1.0-style record to FOCUS 2026.

    EffectiveCost defaults to BilledCost when discount detail is unavailable.
    ListCost defaults to BilledCost when list pricing is unavailable.
    """
    tags_str = ",".join(f"{k}={v}" for k, v in (tags or {}).items())
    service_category = _infer_service_category(provider, service)
    return Focus2026Record(
        billing_account_id=billing_account_id,
        provider_name=provider,
        service_name=service,
        service_category=service_category,
        region_id=region_id,
        region_name=_region_name(region_id),
        resource_id=resource_id,
        billing_period_start=period_start,
        billing_period_end=period_end,
        charge_period_start=period_start,
        charge_period_end=period_end,
        charge_category="Usage",
        charge_class="Standard",
        charge_frequency="Usage-Based",
        pricing_unit=usage_unit or "",
        pricing_quantity=usage_amount,
        consumed_unit=usage_unit or "",
        consumed_quantity=usage_amount,
        billed_cost=cost,
        effective_cost=cost,
        list_cost=cost,
        billing_currency=currency,
        tags=tags_str,
    )


def _infer_service_category(provider: str, service: str) -> str:
    s = service.lower()
    if any(w in s for w in ["ec2", "compute", "virtual machine", "instance", "gce", "aks", "gke"]):
        return "Compute"
    if any(w in s for w in ["rds", "sql", "aurora", "database", "cosmos", "firestore", "bigtable"]):
        return "Databases"
    if any(w in s for w in ["s3", "storage", "blob", "gcs", "ebs", "efs"]):
        return "Storage"
    if any(w in s for w in ["vpc", "elb", "cdn", "nat", "network", "dns", "bandwidth"]):
        return "Networking"
    if any(w in s for w in ["lambda", "functions", "fargate", "serverless"]):
        return "Serverless"
    if any(w in s for w in ["ml", "ai", "sagemaker", "bedrock", "openai", "cognitive"]):
        return "AI and Machine Learning"
    if any(w in s for w in ["cloudwatch", "monitor", "logging", "observability"]):
        return "Management and Governance"
    if any(w in s for w in ["iam", "security", "kms", "secret", "waf", "shield"]):
        return "Security, Identity, and Compliance"
    return "Other"


def _region_name(region_id: str) -> str:
    names = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "eu-west-1": "Europe (Ireland)",
        "eu-central-1": "Europe (Frankfurt)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
        "eastus": "East US",
        "westus2": "West US 2",
        "us-central1": "US Central",
        "europe-west1": "Europe West 1",
    }
    return names.get(region_id, region_id)


# ── FOCUS 2026 validator ──────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    row: int
    column: str
    value: str
    rule: str
    severity: str  # "error" | "warning"


@dataclass
class ValidationResult:
    total_rows: int
    valid_rows: int
    issues: List[ValidationIssue]
    compliant: bool

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def summary(self) -> str:
        status = "COMPLIANT" if self.compliant else "NON-COMPLIANT"
        return (
            f"FOCUS 2026 Validation — {status}\n"
            f"  Rows: {self.total_rows} total, {self.valid_rows} valid\n"
            f"  Issues: {self.error_count} error(s), {self.warning_count} warning(s)"
        )


def validate_focus_2026_csv(file_path: str) -> ValidationResult:
    """
    Validate a CSV file against the FOCUS 2026 specification.

    Checks:
      - Required columns are present
      - ChargeCategory is a valid enum value
      - ChargeClass is a valid enum value (Standard | Correction)
      - BilledCost and EffectiveCost are numeric
      - BillingPeriodStart <= BillingPeriodEnd
      - ChargePeriodStart <= ChargePeriodEnd
      - BillingCurrency is a 3-letter ISO code
      - CommitmentDiscountType is valid if non-empty
      - x_focus_schema_version is present and >= 2026.0
    """
    issues: List[ValidationIssue] = []

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = set(reader.fieldnames or [])

        # Check required columns
        missing = REQUIRED_COLUMNS - headers
        for col in sorted(missing):
            issues.append(ValidationIssue(
                row=0, column=col, value="",
                rule=f"Required column '{col}' is missing from the CSV header",
                severity="error",
            ))

        # Check schema version column presence
        if "x_focus_schema_version" not in headers:
            issues.append(ValidationIssue(
                row=0, column="x_focus_schema_version", value="",
                rule="Column 'x_focus_schema_version' is missing; cannot verify schema version",
                severity="warning",
            ))

        rows = list(reader)

    row_errors: set[int] = set()

    for i, row in enumerate(rows, start=2):  # row 1 = header
        def issue(col: str, rule: str, severity: str = "error") -> None:
            val = row.get(col, "")
            issues.append(ValidationIssue(row=i, column=col, value=str(val)[:80], rule=rule, severity=severity))
            if severity == "error":
                row_errors.add(i)

        # ChargeCategory
        cat = row.get("ChargeCategory", "")
        if cat not in VALID_CHARGE_CATEGORIES:
            issue("ChargeCategory", f"Must be one of {sorted(VALID_CHARGE_CATEGORIES)}, got '{cat}'")

        # ChargeClass (new in 2026)
        cls_ = row.get("ChargeClass", "")
        if cls_ not in VALID_CHARGE_CLASSES:
            issue("ChargeClass", f"Must be 'Standard' or 'Correction', got '{cls_}'")

        # BilledCost numeric
        for cost_col in ("BilledCost", "EffectiveCost"):
            v = row.get(cost_col, "")
            try:
                Decimal(v)
            except Exception:
                issue(cost_col, f"Must be a numeric value, got '{v}'")

        # Date ordering — BillingPeriod
        bp_start = row.get("BillingPeriodStart", "")
        bp_end = row.get("BillingPeriodEnd", "")
        if bp_start and bp_end:
            try:
                if date.fromisoformat(bp_start) > date.fromisoformat(bp_end):
                    issue("BillingPeriodStart", "BillingPeriodStart must be <= BillingPeriodEnd")
            except ValueError:
                issue("BillingPeriodStart", f"Invalid ISO date: '{bp_start}'")

        # Date ordering — ChargePeriod
        cp_start = row.get("ChargePeriodStart", "")
        cp_end = row.get("ChargePeriodEnd", "")
        if cp_start and cp_end:
            try:
                if date.fromisoformat(cp_start) > date.fromisoformat(cp_end):
                    issue("ChargePeriodStart", "ChargePeriodStart must be <= ChargePeriodEnd")
            except ValueError:
                issue("ChargePeriodStart", f"Invalid ISO date: '{cp_start}'")

        # BillingCurrency — 3-letter ISO
        currency = row.get("BillingCurrency", "")
        if currency and (len(currency) != 3 or not currency.isalpha()):
            issue("BillingCurrency", f"Expected 3-letter ISO 4217 currency code, got '{currency}'", severity="warning")

        # CommitmentDiscountType
        cdt = row.get("CommitmentDiscountType", "")
        if cdt and cdt not in VALID_COMMITMENT_TYPES:
            issue("CommitmentDiscountType",
                  f"If set, must be one of {sorted(VALID_COMMITMENT_TYPES - {''})}",
                  severity="warning")

        # Schema version
        sv = row.get("x_focus_schema_version", "")
        if sv:
            try:
                if float(sv.split(".")[0]) < 2026:
                    issue("x_focus_schema_version",
                          f"Schema version {sv} is older than FOCUS 2026 — expected >= 2026.0",
                          severity="warning")
            except (ValueError, IndexError):
                issue("x_focus_schema_version", f"Cannot parse schema version '{sv}'", severity="warning")

        # BillingAccountId non-empty
        if not row.get("BillingAccountId", "").strip():
            issue("BillingAccountId", "BillingAccountId must not be empty")

    total = len(rows)
    valid = total - len(row_errors)
    compliant = len([i for i in issues if i.severity == "error"]) == 0
    return ValidationResult(total_rows=total, valid_rows=valid, issues=issues, compliant=compliant)


def print_validation_report(result: ValidationResult, verbose: bool = False) -> None:
    """Print a human-readable validation report to stdout."""
    print(result.summary())
    if result.issues:
        print()
        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]
        for issue_list, label in ((errors, "ERRORS"), (warnings, "WARNINGS")):
            if not issue_list:
                continue
            print(f"  {label}:")
            shown = issue_list if verbose else issue_list[:10]
            for iss in shown:
                loc = f"row {iss.row}" if iss.row > 0 else "header"
                print(f"    [{loc}] {iss.column}: {iss.rule}")
            if len(issue_list) > 10 and not verbose:
                print(f"    … and {len(issue_list) - 10} more (use --verbose to see all)")
