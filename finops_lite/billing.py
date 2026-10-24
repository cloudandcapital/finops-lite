"""Strict local billing CSV input for the existing FinOps Lite CCAC producer.

This adapter makes no cloud API calls. Billed cost, partial source coverage and
observed daily evidence stay distinct from the existing AWS net-cost adapter.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

from . import __version__
from .ccac import CCACBuildError, _metric, _parse_timestamp, _run_id, _stable_component

PROFILE = "billing_csv/1.0"
MAX_ROWS, MAX_BYTES, MAX_FILES, MAX_GROUPS = 50000, 10 * 1024 * 1024, 20, 1000
MAX_AMOUNT = Decimal("1000000000000")
CURRENCIES = json.loads(Path(__file__).with_name("billing_currencies.json").read_text())
GOOGLE_DOCUMENT_FIELDS = {
    "invoice number",
    "invoice date",
    "invoice total",
    "invoice total amount",
    "total amount due",
    "due date",
    "payment due date",
    "payment terms",
    "statement number",
    "statement date",
    "statement total",
    "total new activity",
    "statement total new activity",
    "billing account id",
    "billing account name",
    "currency",
    "currency code",
    "invoice currency",
    "statement currency",
    "exchange rate",
    "currency exchange rate",
    "publisher type",
    "invoice month",
    "billing period",
}
GOOGLE_CURRENCY_FIELDS = {
    "currency",
    "currency code",
    "invoice currency",
    "statement currency",
}
MAX_GOOGLE_DOCUMENT_ROWS = 32


def text_decimal(value):
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,18})?", value
    ):
        raise CCACBuildError(
            "Cost needs a signed decimal with up to 18 places; no currency symbol or scientific notation."
        )
    number = Decimal(value.replace(",", ""))
    if number.copy_abs() > MAX_AMOUNT:
        raise CCACBuildError(
            "A cost exceeds the supported limit of one trillion per currency."
        )
    return number


def day(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise CCACBuildError("Dates must use YYYY-MM-DD.")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise CCACBuildError("Use a real calendar date.") from exc
    if not 1900 <= result.year <= 2199:
        raise CCACBuildError("Dates must be between 1900 and 2199.")
    return result


def boundary(value, timestamp_basis=None):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        return datetime.combine(day(value), datetime.min.time(), timezone.utc)
    # BigQuery CSV serializes UTC explicitly as a suffix and may use microseconds.
    if isinstance(value, str) and value.endswith(" UTC"):
        value = value[:-4] + "Z"
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", value or ""
    ):
        if timestamp_basis != "UTC":
            raise CCACBuildError(
                "This timestamp has no timezone. Confirm UTC only if the export's timestamps are UTC; otherwise export explicit UTC timestamps."
            )
        value += "Z"
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|\+00:00)",
        value or "",
    ):
        raise CCACBuildError("Use an ISO date or UTC timestamp ending in Z or +00:00.")
    day(value[:10])
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CCACBuildError("Use a real UTC timestamp.") from exc


def stamp(value):
    precision = "microseconds" if value.microsecond % 1000 else "milliseconds"
    return value.isoformat(timespec=precision).replace("+00:00", "Z")


def google_cost_column(headers):
    """Recognize the documented English Cost table amounts, never list prices."""
    found = {}
    for base in ("unrounded cost", "cost"):
        matches = [
            key
            for key in headers
            if re.fullmatch(re.escape(base) + r"(?:\s*\([^()]+\))?", key)
        ]
        if len(matches) > 1:
            raise CCACBuildError(
                "Choose a Google Cost table export with one cost column per precision, in one currency."
            )
        if matches:
            found[base] = matches[0]
    if found:
        return found.get("unrounded cost") or found["cost"]
    raise CCACBuildError(
        "Include Unrounded cost (recommended) or Cost in the Google Cost table CSV."
    )


def google_invoice_window(details):
    if not isinstance(details, dict) or details.get("confirmed") is not True:
        raise CCACBuildError(
            "Confirm the Google Cost table invoice month and billing currency before review."
        )
    month, currency = details.get("month", ""), details.get("currency", "")
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise CCACBuildError("Choose the Google Cost table invoice month as YYYY-MM.")
    start = day(month + "-01")
    end = day(f"{start.year + (start.month == 12):04d}-{start.month % 12 + 1:02d}-01")
    if currency not in CURRENCIES:
        raise CCACBuildError(
            "Confirm the Google bill's three-letter billing currency; currency symbols are ambiguous."
        )
    return start, end, currency


def billing_header(reader):
    """Read bounded, recognized Google document metadata before its charge table.

    Do not search past unknown records or use invoice dates/totals as charges.
    The reader retains original line numbers and the source text is unchanged.
    """
    header = next(reader)
    while not any(cell.strip() for cell in header):
        header = next(reader)
    fields = [cell.strip().lower() for cell in header]
    if (
        {"service description", "cost type"}.issubset(fields)
        or {"date", "service", "cost", "currency"}.issubset(fields)
        or {
            "chargeperiodstart",
            "chargeperiodend",
            "servicename",
            "billedcost",
            "billingcurrency",
        }.issubset(fields)
    ):
        return header, 0, None
    if not fields or fields[0] not in GOOGLE_DOCUMENT_FIELDS:
        return header, 0, None
    count, currencies = 0, set()
    while not {"service description", "cost type"}.issubset(fields):
        if (
            count >= MAX_GOOGLE_DOCUMENT_ROWS
            or fields[0] not in GOOGLE_DOCUMENT_FIELDS
            or any(cell.strip() for cell in header[2:])
        ):
            raise CCACBuildError(
                "Unrecognized information before the Google Cost table. Use its English CSV with Service description, Cost type and Unrounded cost. No billing rows were skipped."
            )
        if fields[0] in GOOGLE_CURRENCY_FIELDS and len(header) > 1:
            candidate = header[1].strip().upper()
            if candidate in CURRENCIES:
                currencies.add(candidate)
        count += 1
        header = next(reader)
        while not any(cell.strip() for cell in header):
            header = next(reader)
        fields = [cell.strip().lower() for cell in header]
    if len(currencies) > 1:
        raise CCACBuildError("Google document headers contain conflicting currencies.")
    return header, count, next(iter(currencies), None)


def normalize_google_cost_table(
    text, filename, headers, reader, details, document_rows=0, document_currency=None
):
    """Google Cost table is invoice-month data; credits already have their own rows.

    Source: https://docs.cloud.google.com/billing/docs/how-to/cost-table
    Never add separate credit columns or treat its Total row as another charge.
    """
    start, end, currency = google_invoice_window(details)
    if document_currency and document_currency != currency:
        raise CCACBuildError(
            "The confirmed currency does not match the Google document header."
        )
    cost_key = google_cost_column(headers)
    unit = re.search(r"\(([A-Za-z]{3})\)$", cost_key)
    if unit and unit.group(1).upper() != currency:
        raise CCACBuildError(
            "The confirmed currency does not match the Google cost column."
        )
    rounded_key = next(
        (key for key in headers if re.fullmatch(r"cost(?:\s*\([^()]+\))?", key)), None
    )
    if rounded_key:
        rounded_unit = re.search(r"\(([A-Za-z]{3})\)$", rounded_key)
        if rounded_unit and rounded_unit.group(1).upper() != currency:
            raise CCACBuildError(
                "The confirmed currency does not match the Google Cost column."
            )
    rows, controls, missing_services, rounded_rows = [], [], 0, 0
    input_rows = 0
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        input_rows += 1
        if input_rows > MAX_ROWS or len(cells) != len(headers):
            raise CCACBuildError(
                "Check Google CSV columns and the 50,000-row input limit."
            )
        values = dict(zip(headers, (cell.strip() for cell in cells)))
        get = lambda key: values.get(key, "")
        kind = get("cost type").casefold()
        if kind not in {"usage", "adjustment", "rounding error", "tax", "total"}:
            raise CCACBuildError(
                "Use the English Google Cost table CSV with Cost type included and Group by set to No grouping."
            )
        row_currency = get("currency") or get("currency code")
        if row_currency and row_currency.upper() != currency:
            raise CCACBuildError(
                "Google records contain a different currency; review one billing currency at a time."
            )
        value = get(cost_key)
        if not value and rounded_key:
            value = get(rounded_key)
            if kind != "total":
                rounded_rows += 1
        cost = amount(value)
        if kind == "total":
            controls.append(cost)
            continue
        service = get("service description")
        if not service:
            missing_services += 1
            service = f"{get('cost type')} · service not supplied"
        if len(service) > 200 or len(get("billing account id")) > 300:
            raise CCACBuildError(
                "Keep service names to 200 characters and billing account IDs to 300."
            )
        row = dict(
            start=stamp(boundary(str(start))),
            end=stamp(boundary(str(end))),
            service=service,
            provider="Google Cloud",
            currency=currency,
            cost=text_decimal(cost),
            recordId="",
            accountId=get("billing account id"),
            file=filename,
            line=reader.line_num,
        )
        # Full original dimensions distinguish projects, SKUs and credit types.
        row["fingerprint"] = json.dumps(
            [
                row["start"],
                row["end"],
                service,
                currency,
                row["cost"],
                sorted(values.items()),
            ],
            ensure_ascii=False,
        )
        rows.append(row)
    if not rows:
        raise CCACBuildError(
            "The Google CSV has no charge records; a Total row alone cannot supply a service review."
        )
    if len(controls) > 1:
        raise CCACBuildError(
            "This Google CSV has multiple Total rows. Download one flat, ungrouped Cost table, in one currency."
        )
    if controls:
        with localcontext() as context:
            context.prec = 50
            net = sum((Decimal(row["cost"]) for row in rows), Decimal(0))
            # Google documents the invoice total as the sum of unrounded rows,
            # rounded to two places. Raw amounts remain unchanged in the report.
            if net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != controls[
                0
            ].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
                raise CCACBuildError(
                    "Google's Total row does not match the charge rows. Include Unrounded cost and download with No grouping and filters reset."
                )
    notes = [
        f"Google Cost table: {cost_key} supplies signed billed amounts; credits are separate rows and are not added twice.",
        f"The visitor confirmed invoice month {details['month']} and billing currency {currency}; these are invoice-month records, not daily usage observations.",
        "Google Total rows are reconciliation controls and are excluded from charge totals.",
    ]
    if rounded_rows:
        notes.append(
            f"{rounded_rows} rows lacked Unrounded cost and used the supplied Cost value; no missing amount was replaced with zero."
        )
    warnings = [
        f"Google Cost table uses the confirmed invoice month {details['month']}, not actual daily usage dates. Filters may limit coverage; a full invoice is not verified."
    ]
    if document_rows:
        notes.append(
            f"Read {document_rows} invoice/statement header rows separately from the Cost table; they are document metadata, not charge records."
        )
        warnings.append(
            "Invoice/statement header information is separate from the Cost table charges. Its invoice date and total do not set the reporting month or prove complete coverage."
        )
    if not cost_key.startswith("unrounded cost"):
        warnings.append(
            "This Google CSV supplies rounded Cost values. Include Unrounded cost for a more precise reconciliation."
        )
    elif rounded_rows:
        warnings.append(
            f"{rounded_rows} Google rows have no Unrounded cost value; their supplied rounded Cost values were used instead. No missing amount was replaced with zero."
        )
    if controls:
        warnings.append(
            "The Google Total row matches supplied charge rows at two-decimal invoice precision before duplicate handling; the Total row is not counted as a charge."
        )
    else:
        warnings.append(
            "No Google Total row was supplied; compare this review with the source invoice or statement."
        )
    if missing_services:
        warnings.append(
            f"{missing_services} Google rows have no service description; their cost-type labels identify unassigned charges, not known services."
        )
    return dict(
        name=filename,
        text=text,
        format="Google Cloud Cost table CSV",
        rows=rows,
        inputRowCount=input_rows,
        warnings=warnings,
        mappingNotes=notes,
    )


def normalize_csv(
    text, filename="billing.csv", *, timestamp_basis=None, google_cost_table=None
):
    # Transport may decompress Gzip first. The content and its source digest
    # remain the original expanded CSV text, never a rewritten table.
    if not filename.lower().endswith((".csv", ".csv.gz")):
        raise CCACBuildError("Choose CSV or Gzip CSV files.")
    if timestamp_basis not in {None, "UTC"}:
        raise CCACBuildError("The only supported timestamp confirmation is UTC.")
    if (
        not isinstance(text, str)
        or "\x00" in text
        or len(text.encode("utf-8")) > MAX_BYTES
    ):
        raise CCACBuildError("Choose valid UTF-8 CSV files totaling 10 MB or less.")
    try:
        reader = csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True)
        header, document_rows, document_currency = billing_header(reader)
        headers = [cell.strip().lower() for cell in header]
        if (
            not headers
            or any(not cell for cell in headers)
            or len(set(headers)) != len(headers)
        ):
            raise CCACBuildError("Every column needs a unique header.")
        if {"service description", "cost type"}.issubset(headers):
            return normalize_google_cost_table(
                text,
                filename,
                headers,
                reader,
                google_cost_table,
                document_rows,
                document_currency,
            )
        focus = "billedcost" in headers or "chargeperiodstart" in headers
        names = (
            [
                "chargeperiodstart",
                "servicename",
                "billedcost",
                "billingcurrency",
                "chargeperiodend",
                "providername",
            ]
            if focus
            else ["date", "service", "cost", "currency", "enddate", "provider"]
        )
        required = names[:5] if focus else names[:4]
        if any(name not in headers for name in required):
            raise CCACBuildError(
                "We could not find the supported billing columns. Use the simple template or a FOCUS BilledCost CSV."
            )
        used = set(names + ["recordid", "billingaccountid", "chargecategory"])
        rows, missing_services = [], 0
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) != len(headers):
                raise CCACBuildError(
                    f"Expected {len(headers)} columns but found {len(cells)}."
                )
            values = dict(zip(headers, (cell.strip() for cell in cells)))
            get = lambda name: values.get(name, "")
            service, provider = get(names[1]), get(names[5]) or "Not specified"
            missing_service = focus and not service
            if missing_service:
                missing_services += 1
                service = f"{get('chargecategory') or 'Charge'} · service not supplied"
            if not service or len(service) > 200 or len(provider) > 200:
                raise CCACBuildError(
                    "Supply a Service label and keep service/provider names to 200 characters."
                )
            currency = get(names[3]).upper()
            if currency not in CURRENCIES:
                raise CCACBuildError(
                    "Currency needs a supported three-letter code such as USD, EUR, or GBP."
                )
            cost = amount(get(names[2]))
            if get("chargecategory").lower() == "credit" and cost > 0:
                raise CCACBuildError(
                    "This credit has a positive cost. Enter credits as negative amounts."
                )
            start = (
                boundary(get(names[0]), timestamp_basis)
                if focus
                else boundary(day(get(names[0])).isoformat())
            )
            if focus and not get(names[4]):
                raise CCACBuildError(
                    "ChargePeriodEnd is empty; supply the exclusive end date."
                )
            end = (
                boundary(get(names[4]), timestamp_basis)
                if get(names[4])
                else start + timedelta(days=1)
            )
            if not focus and get(names[4]):
                day(get(names[4]))
            if end <= start:
                raise CCACBuildError(
                    "The exclusive charge end must be after its start."
                )
            record_id, account_id = get("recordid"), get("billingaccountid")
            if max(len(record_id), len(account_id)) > 300:
                raise CCACBuildError(
                    "Record and account IDs must be 300 characters or fewer."
                )
            row = dict(
                start=stamp(start),
                end=stamp(end),
                service=service,
                provider=provider,
                currency=currency,
                cost=text_decimal(cost),
                recordId=record_id,
                accountId=account_id,
                file=filename,
                line=reader.line_num,
            )
            extras = sorted(
                (key, value) for key, value in values.items() if key not in used
            )
            if missing_service:
                extras.append(("__missing_source_service__", "true"))
            row["fingerprint"] = json.dumps(
                [
                    row["start"],
                    row["end"],
                    service,
                    provider,
                    currency,
                    row["cost"],
                    account_id,
                    get("chargecategory"),
                    extras,
                ],
                ensure_ascii=False,
            )
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise CCACBuildError("This period has more than 50,000 rows.")
    except (csv.Error, StopIteration) as exc:
        raise CCACBuildError(
            f"{filename}: check the CSV header, commas and quotes."
        ) from exc
    if not rows:
        raise CCACBuildError(f"{filename}: this CSV needs at least one billing record.")
    return dict(
        name=filename,
        text=text,
        format="FOCUS BilledCost" if focus else "Simple billing CSV",
        rows=rows,
        warnings=(
            [
                f"{missing_services} FOCUS rows have no ServiceName; they are grouped by supplied ChargeCategory with a service-not-supplied label. Amounts are unchanged."
            ]
            if missing_services
            else []
        ),
    )


def prepare_period(files, options=None):
    options = options or {}
    policy = options.get("duplicatePolicy", "review")
    if policy not in {"review", "keep", "drop"}:
        raise CCACBuildError("Choose how identical rows should be handled.")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise CCACBuildError("Choose between 1 and 20 CSV parts per period.")
    if sum(len(item.get("text", "").encode("utf-8")) for item in files) > MAX_BYTES:
        raise CCACBuildError("Choose CSV files totaling 10 MB or less for each period.")
    parsed = [
        normalize_csv(
            item["text"],
            item.get("name", "billing.csv"),
            timestamp_basis=options.get("timestampBasis"),
            google_cost_table=options.get("googleCostTable"),
        )
        for item in files
    ]
    source_rows = [row for item in parsed for row in item["rows"]]
    input_row_count = sum(
        item.get("inputRowCount", len(item["rows"])) for item in parsed
    )
    if input_row_count > MAX_ROWS:
        raise CCACBuildError("Use between 1 and 50,000 billing records per period.")
    currencies = sorted({row["currency"] for row in source_rows})
    if len(currencies) != 1:
        raise CCACBuildError(
            f"These records contain {', '.join(currencies)}. Export one currency at a time; no exchange rates are applied."
        )
    ids, fingerprints, rows = {}, set(), []
    repeated, identical = 0, 0
    for row in source_rows:
        fingerprint = row["fingerprint"]
        if row["recordId"]:
            key = (row["provider"], row["accountId"], row["recordId"])
            if key in ids:
                if ids[key] != fingerprint:
                    raise CCACBuildError(
                        f"{row['file']}, line {row['line']}: RecordId {row['recordId']} has conflicting values."
                    )
                repeated += 1
                continue
            ids[key] = fingerprint
        else:
            if fingerprint in fingerprints:
                identical += 1
                if policy != "keep":
                    continue
            fingerprints.add(fingerprint)
        rows.append({key: value for key, value in row.items() if key != "fingerprint"})
    if identical and policy == "review":
        raise CCACBuildError(
            f"{identical} repeated rows without a RecordId need your review. Choose Count once or Keep all in Identical rows."
        )
    first, last = min(boundary(row["start"]) for row in rows), max(
        boundary(row["end"]) for row in rows
    )
    start = day(options["start"]) if options.get("start") else first.date()
    end = (
        day(options["end"]) + timedelta(days=1)
        if options.get("end")
        else last.date()
        + (timedelta(days=1) if last.time() != datetime.min.time() else timedelta(0))
    )
    if boundary(start.isoformat()) > first or boundary(end.isoformat()) < last:
        raise CCACBuildError("The selected dates leave out billing records.")
    days = (end - start).days
    if not 1 <= days <= 366:
        raise CCACBuildError("Choose a reporting period between 1 and 366 days.")
    if len({(row["provider"], row["service"]) for row in rows}) > MAX_GROUPS:
        raise CCACBuildError(
            "This bill has more than 1,000 service groups. Export a smaller period or fewer accounts."
        )
    warnings = [warning for item in parsed for warning in item.get("warnings", [])]
    google_files = [
        item for item in parsed if item["format"] == "Google Cloud Cost table CSV"
    ]
    if google_files and len(google_files) != len(parsed):
        raise CCACBuildError(
            "Review Google Cost table files separately from FOCUS or simple CSVs; do not combine different export representations of the same bill."
        )
    if google_files and (options.get("start") or options.get("end")):
        raise CCACBuildError(
            "For Google Cost table files, use the confirmed invoice month instead of overriding period dates."
        )
    if options.get("timestampBasis") == "UTC":
        warnings.append(
            "You confirmed that timestamps without a timezone are UTC. Original CSV text is preserved."
        )
    if repeated:
        warnings.append(f"{repeated} repeated record IDs counted once.")
    if identical:
        warnings.append(
            f"{identical} identical rows without IDs {'kept as separate charges' if policy == 'keep' else 'counted once'} by your choice."
        )
    if any(not row["recordId"] for row in rows):
        warnings.append(
            "Some records have no RecordId. Only identical rows can be checked; overlapping exports may contain other duplicates."
        )
    if any(row["provider"] == "Not specified" for row in rows):
        warnings.append(
            "Provider was not supplied for some records. They are grouped under “Not specified”."
        )
    return dict(
        currency=currencies[0],
        rows=rows,
        start=str(start),
        end=str(end),
        days=days,
        dateBasis=(
            "Invoice month confirmed by the visitor; actual usage dates may differ"
            if google_files
            else (
                "Selected dates (inclusive in the form; end exclusive in downloads)"
                if options.get("start") or options.get("end")
                else "Coverage inferred from charge dates; a complete invoice period is not confirmed"
            )
        ),
        timestampBasis=options.get("timestampBasis"),
        sources=[
            dict(name=item["name"], format=item["format"], rows=len(item["rows"]))
            for item in parsed
        ],
        sourceRowCount=input_row_count,
        sourceMappingNotes=[item.get("mappingNotes", []) for item in parsed],
        repeatedIds=repeated,
        identicalRows=identical,
        duplicatePolicy=policy,
        warnings=warnings,
        spansMultipleDays=any(
            boundary(row["end"])
            > boundary(str(boundary(row["start"]).date() + timedelta(days=1)))
            for row in rows
        ),
    )


def build_billing_result(
    current_files,
    previous_files=None,
    *,
    options=None,
    run_id=None,
    generated_at=None,
    mode="real",
):
    """Produce canonical billed-cost metrics and evidence without inventing coverage."""
    with localcontext() as context:
        context.prec = 50
        return _build(
            current_files,
            previous_files or [],
            options or {},
            run_id,
            generated_at,
            mode,
        )


def _build(current_files, previous_files, options, run_id, generated_at, mode):
    if mode not in {"real", "illustrative"}:
        raise CCACBuildError("Invalid billing input mode.")
    current = prepare_period(current_files, options.get("current"))
    previous = (
        prepare_period(previous_files, options.get("previous"))
        if previous_files
        else None
    )
    if previous and current["currency"] != previous["currency"]:
        raise CCACBuildError(
            "Choose the same currency for both periods; no exchange rates are applied."
        )
    if previous and previous["end"] > current["start"]:
        raise CCACBuildError(
            "The previous period must end before the current period begins; dates overlap or are reversed."
        )
    currency, generated = current["currency"], _parse_timestamp(generated_at)
    inputs, evidence, metrics, templates, findings, daily_ids, skipped = (
        [],
        [],
        [],
        {},
        [],
        [],
        [],
    )
    by_id = {}
    period = dict(start=current["start"], end=current["end"], timezone="UTC")
    for label, files in [("current", current_files), ("previous", previous_files)]:
        period_data = current if label == "current" else previous
        for index, item in enumerate(files):
            identifier = f"source.finops-lite.{label}.{index}"
            digest = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
            inputs.append(
                dict(
                    id=identifier,
                    source_type="billing_csv",
                    source_version=(
                        "google_cost_table/1.0"
                        if period_data["sources"][index]["format"]
                        == "Google Cloud Cost table CSV"
                        else PROFILE
                    ),
                    adapter_version=__version__,
                    content_sha256=digest,
                    access=(
                        "illustrative_fixture"
                        if mode == "illustrative"
                        else "local_read_only"
                    ),
                    data_classification=(
                        "public_illustrative"
                        if mode == "illustrative"
                        else "customer_confidential"
                    ),
                    lossy_mapping=True,
                    mapping_notes=[
                        "Supported signed billed-cost columns are preserved; source coverage is not assumed complete.",
                        "Source content_sha256 identifies the original expanded UTF-8 CSV text, including any BOM and line endings.",
                    ]
                    + period_data["sourceMappingNotes"][index]
                    + (
                        ["The visitor confirmed UTC for timestamps without a timezone."]
                        if options.get(label, {}).get("timestampBasis") == "UTC"
                        else []
                    ),
                )
            )
            evidence.append(
                dict(
                    id=f"evidence.finops-lite.{label}.{index}",
                    kind="source_row",
                    source_ids=[identifier],
                    description=f"Supplied billing records: {item.get('name', 'billing.csv')}",
                    locator=item.get("name", "billing.csv"),
                    observed_at=generated,
                    content_sha256=digest,
                )
            )

    def metric(
        identifier,
        name,
        value,
        *,
        basis="observed",
        unit="currency",
        dimensions=None,
        metric_period=None,
        refs=None,
        evidence_refs=None,
        formula=None,
        reason=None,
    ):
        item = _metric(
            metric_id=identifier,
            name=name,
            value=value,
            currency=currency if unit == "currency" else None,
            basis="unknown" if value is None else basis,
            additivity=(
                "ratio" if unit == "percent" else "non_additive" if refs else "additive"
            ),
            period=metric_period or period,
            dimensions=dimensions or {},
            evidence_id=evidence[0]["id"],
            formula=formula,
            input_metric_ids=refs or [],
            unknown_reason=reason,
            preserve_precision=True,
        )
        item["unit"] = unit
        item["evidence_ids"] = evidence_refs or [entry["id"] for entry in evidence]
        metrics.append(item)
        by_id[identifier] = item
        return identifier

    def summary(data, label, *, provider_filter=None):
        rows = (
            data["rows"]
            if provider_filter is None
            else [row for row in data["rows"] if row["provider"] == provider_filter]
        )
        prefix = f"metric.cloud.{label}" + (
            f".provider.{_stable_component(provider_filter)}"
            if provider_filter is not None
            else ""
        )
        own_period = dict(start=data["start"], end=data["end"], timezone="UTC")
        evidence_refs = [
            entry["id"] for entry in evidence if f".{label}." in entry["id"]
        ]
        groups, by_start = defaultdict(list), defaultdict(list)
        for row in rows:
            groups[(row["provider"], row["service"])].append(row)
            by_start[row["start"][:10]].append(row)
        charges = sum(
            (amount(row["cost"]) for row in rows if amount(row["cost"]) >= 0),
            Decimal(0),
        )
        credits = sum(
            (amount(row["cost"]) for row in rows if amount(row["cost"]) < 0), Decimal(0)
        )
        if charges > MAX_AMOUNT or -credits > MAX_AMOUNT:
            raise CCACBuildError(
                "This period exceeds one trillion in charges or credits."
            )
        net_id = (
            "metric.tech-spend.scope.cloud"
            if label == "current" and provider_filter is None
            else (
                "metric.cloud.previous-total"
                if label == "previous" and provider_filter is None
                else prefix + ".net"
            )
        )
        refs = {
            kind: metric(
                net_id if kind == "net" else prefix + "." + kind,
                f"{label} {kind} billed cost",
                value,
                metric_period=own_period,
                evidence_refs=evidence_refs,
            )
            for kind, value in [
                ("net", charges + credits),
                ("charges", charges),
                ("credits", credits),
            ]
        }
        services = []
        for (provider, service), records in sorted(groups.items()):
            key = json.dumps(
                [provider, service], separators=(",", ":"), ensure_ascii=False
            )
            identifier = prefix + ".service." + _stable_component(key)
            positive = sum(
                (amount(row["cost"]) for row in records if amount(row["cost"]) >= 0),
                Decimal(0),
            )
            negative = sum(
                (amount(row["cost"]) for row in records if amount(row["cost"]) < 0),
                Decimal(0),
            )
            dims = dict(scope="cloud", provider=provider, service=service)
            group_refs = {
                kind: metric(
                    identifier + "." + kind,
                    f"{service} {kind}",
                    value,
                    dimensions={**dims, "measure": kind},
                    metric_period=own_period,
                    evidence_refs=evidence_refs,
                )
                for kind, value in [
                    ("net", positive + negative),
                    ("charges", positive),
                    ("credits", negative),
                ]
            }
            share = (
                (positive / charges * 100).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                if charges > 0
                else None
            )
            group_refs["chargeShare"] = metric(
                identifier + ".share",
                f"{service} share of positive charges",
                share,
                basis="calculated",
                unit="percent",
                dimensions=dims,
                metric_period=own_period,
                refs=[group_refs["charges"], refs["charges"]],
                evidence_refs=evidence_refs,
                formula=(
                    "100 * service positive charges / period positive charges"
                    if share is not None
                    else None
                ),
                reason="No positive charges." if share is None else None,
            )
            services.append(
                dict(
                    key=key,
                    service=service,
                    provider=provider,
                    records=len(records),
                    metric_ids=group_refs,
                )
            )
            if label == "current" and provider_filter is None:
                days = defaultdict(list)
                for row in records:
                    days[row["start"][:10]].append(row)
                dates = sorted(days)
                full_days = all(
                    boundary(row["start"]).time() == datetime.min.time()
                    and boundary(row["end"])
                    == boundary(row["start"]) + timedelta(days=1)
                    for row in records
                )
                consecutive = all(
                    day(b) == day(a) + timedelta(days=1)
                    for a, b in zip(dates, dates[1:])
                )
                sums = {
                    key: sum((amount(row["cost"]) for row in values), Decimal(0))
                    for key, values in days.items()
                }
                if (
                    full_days
                    and consecutive
                    and all(value >= 0 for value in sums.values())
                ):
                    for value_date, value in sorted(sums.items()):
                        daily_ids.append(
                            metric(
                                identifier + ".day." + value_date,
                                f"{service} observed daily billed cost",
                                value,
                                dimensions={**dims, "date": value_date},
                                metric_period=dict(
                                    start=value_date,
                                    end=str(day(value_date) + timedelta(days=1)),
                                    timezone="UTC",
                                ),
                                evidence_refs=evidence_refs,
                            )
                        )
                else:
                    skipped.append(
                        dict(
                            provider=provider,
                            service=service,
                            reason="Daily anomaly evidence needs consecutive observed UTC days with non-negative daily totals. Multi-day charges, gaps and negative daily adjustments are not turned into daily usage.",
                        )
                    )
        services.sort(
            key=lambda row: (-by_id[row["metric_ids"]["net"]]["value"], row["key"])
        )
        daily = [
            dict(
                date=value_date,
                metric_id=metric(
                    prefix + ".start." + value_date,
                    "Cost by charge start date",
                    sum((amount(row["cost"]) for row in records), Decimal(0)),
                    dimensions={"charge_start": value_date},
                    metric_period=own_period,
                    evidence_refs=evidence_refs,
                ),
            )
            for value_date, records in sorted(by_start.items())
        ]
        positive_groups = sorted(
            (
                row
                for row in services
                if by_id[row["metric_ids"]["charges"]]["value"] > 0
            ),
            key=lambda row: (-by_id[row["metric_ids"]["charges"]]["value"], row["key"]),
        )
        chart = [
            dict(
                name=row["service"],
                provider=row["provider"],
                metric_id=row["metric_ids"]["charges"],
            )
            for row in positive_groups[:4]
        ]
        if len(positive_groups) > 4:
            others = [row["metric_ids"]["charges"] for row in positive_groups[4:]]
            chart.append(
                dict(
                    name="Other services",
                    provider="",
                    metric_id=metric(
                        prefix + ".other-charges",
                        "Other positive service charges",
                        sum((by_id[item]["value"] for item in others), Decimal(0)),
                        basis="calculated",
                        refs=others,
                        evidence_refs=evidence_refs,
                        formula="sum of remaining service positive charges",
                        metric_period=own_period,
                    ),
                )
            )
        return {
            **data,
            "rows": rows,
            "metric_ids": refs,
            "services": services,
            "daily": daily,
            "chargeChart": chart,
        }

    current_layout = summary(current, "current")
    current_layout["providerSummaries"] = {
        provider: summary(current, "current", provider_filter=provider)
        for provider in sorted({row["provider"] for row in current["rows"]})
    }
    previous_layout = summary(previous, "previous") if previous else None
    cloud = by_id["metric.tech-spend.scope.cloud"]
    cloud["dimensions"] = dict(scope="cloud")
    cloud["accounting_boundary"] = dict(
        relationship="canonical_scope_spend",
        scope="cloud",
        canonical_owner="finops-lite",
        source_channel="cloud_provider_billing",
        cost_basis="billed_cost",
        currency_minor_unit=Decimal(10) ** -CURRENCIES[currency],
        inclusion_rules=[
            "All supplied signed billed-cost rows, including taxes and credits when present, within the selected window."
        ],
        exclusion_rules=[
            "Direct-vendor AI and separate SaaS invoices outside this cloud billing export."
        ],
        coverage="partial",
        overlap=dict(
            disposition="resolved",
            treatment="Provider-billed AI remains in Cloud; direct-vendor AI is excluded.",
        ),
        cross_scope_treatments=dict(
            provider_billed_ai="included", direct_ai_vendor="excluded"
        ),
        component_treatments={
            key: "included"
            for key in ["credits", "taxes", "adjustments", "shared_services"]
        },
        allocation_of_metric_id=None,
        total_eligible=False,
        eligibility_reason="A supplied CSV does not prove complete organizational Cloud coverage.",
    )
    comparison = None
    if previous:
        before = by_id[previous_layout["metric_ids"]["net"]]["value"]
        delta = cloud["value"] - before
        delta_id = metric(
            "metric.cloud.change-amount",
            "Cloud billed-cost period change",
            delta,
            basis="calculated",
            refs=[cloud["id"], previous_layout["metric_ids"]["net"]],
            formula="current supplied period net - previous supplied period net",
        )
        pct = (
            (delta / before * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if before > 0
            else None
        )
        pct_id = metric(
            "metric.cloud.change-percentage",
            "Cloud billed-cost percentage change",
            pct,
            basis="calculated",
            unit="percent",
            refs=[delta_id, previous_layout["metric_ids"]["net"]],
            formula="100 * change / previous net" if pct is not None else None,
            reason="Previous net cost is zero or negative." if pct is None else None,
        )
        now_map = {row["key"]: row for row in current_layout["services"]}
        before_map = {row["key"]: row for row in previous_layout["services"]}
        drivers = []
        for key in sorted(set(now_map) | set(before_map)):
            now, old = now_map.get(key), before_map.get(key)
            row = now or old
            now_id = now["metric_ids"]["net"] if now else None
            old_id = old["metric_ids"]["net"] if old else None
            diff = (by_id[now_id]["value"] if now_id else Decimal(0)) - (
                by_id[old_id]["value"] if old_id else Decimal(0)
            )
            identifier = metric(
                "metric.cloud.change.service." + _stable_component(key),
                f"{row['service']} supplied cost change",
                diff,
                basis="calculated",
                refs=[item for item in [now_id, old_id] if item],
                formula="current supplied service net minus previous supplied service net; an absent upload contributes no supplied amount",
            )
            drivers.append(
                dict(
                    key=key,
                    service=row["service"],
                    provider=row["provider"],
                    current_metric_id=now_id,
                    previous_metric_id=old_id,
                    delta_metric_id=identifier,
                    coverage=(
                        "In both uploads"
                        if now and old
                        else (
                            "Not in previous upload" if now else "Not in current upload"
                        )
                    ),
                )
            )
        drivers.sort(
            key=lambda row: (-abs(by_id[row["delta_metric_id"]]["value"]), row["key"])
        )
        warnings = []
        if current["days"] != previous["days"]:
            warnings.append(
                f"The periods cover {current['days']} and {previous['days']} days. Changes compare period totals, not daily spending rates."
            )
        if {row["provider"] for row in current["rows"]} != {
            row["provider"] for row in previous["rows"]
        }:
            warnings.append(
                "Provider coverage differs between uploads. An absent service or provider means no records were supplied, not that it was switched on or off."
            )
        comparison = dict(
            delta_metric_id=delta_id,
            percent_metric_id=pct_id,
            drivers=drivers,
            warnings=warnings,
        )

    def finding(identifier, title, parts, metric_ids=None):
        refs = list(
            dict.fromkeys(
                [part["metric_id"] for part in parts if isinstance(part, dict)]
                + (metric_ids or [])
            )
        )
        if not refs:
            refs = [cloud["id"]]
        description = "".join(
            (
                part
                if isinstance(part, str)
                else f"{currency+' ' if part.get('format')=='money' else ''}{text_decimal(by_id[part['metric_id']]['value']) if by_id[part['metric_id']]['value'] is not None else 'unavailable'}"
            )
            for part in parts
        )
        identifier = "finding.billing." + identifier
        findings.append(
            dict(
                id=identifier,
                finding_type="other",
                title=title,
                description=description,
                severity="info",
                status="open",
                metric_ids=refs,
                evidence_ids=sorted(
                    {item for ref in refs for item in by_id[ref]["evidence_ids"]}
                ),
                first_observed_at=generated,
                last_observed_at=generated,
            )
        )
        templates[identifier] = parts

    ref = lambda identifier, fmt="money": dict(metric_id=identifier, format=fmt)
    finding(
        "summary",
        "Where your money went",
        [
            ref(cloud["id"]),
            " net cost comes from ",
            ref(current_layout["metric_ids"]["charges"]),
            " in positive charges and ",
            ref(current_layout["metric_ids"]["credits"]),
            f" in credits or negative adjustments across {len(current['rows'])} records.",
        ],
    )
    leader = max(
        current_layout["services"],
        key=lambda row: by_id[row["metric_ids"]["charges"]]["value"],
    )
    if by_id[current_layout["metric_ids"]["charges"]]["value"] > 0:
        finding(
            "largest-service",
            "Largest source of charges",
            [
                f"{leader['service']} ({leader['provider']}) accounts for ",
                ref(leader["metric_ids"]["chargeShare"], "percent"),
                " of positive charges: ",
                ref(leader["metric_ids"]["charges"]),
                " before credits. Its net cost is ",
                ref(leader["metric_ids"]["net"]),
                ".",
            ],
        )
    else:
        finding(
            "no-charges",
            "No positive charges",
            [
                "These records contain only credits, zero costs, or negative adjustments. There is no positive spending share to calculate."
            ],
        )
    if comparison:
        finding(
            "change",
            "What changed",
            [
                "Net cost changed by ",
                ref(comparison["delta_metric_id"]),
                " compared with the earlier upload. ",
            ]
            + (
                [
                    ref(comparison["percent_metric_id"], "percent"),
                    " of previous net cost. ",
                ]
                if pct is not None
                else [
                    "A percentage is not meaningful when the previous net cost is zero or negative. "
                ]
            )
            + [" ".join(comparison["warnings"])],
        )
        driver = next(
            (
                item
                for item in comparison["drivers"]
                if by_id[item["delta_metric_id"]]["value"] != 0
            ),
            None,
        )
        if driver:
            finding(
                "movement",
                "Biggest movement",
                [
                    f"{driver['service']} ({driver['provider']}) changed by ",
                    ref(driver["delta_metric_id"]),
                    f". {driver['coverage']}. This locates the change; billing amounts alone do not establish its cause.",
                ],
            )
    else:
        finding(
            "comparison-missing",
            "Want to know what changed?",
            [
                "Add an earlier billing CSV to compare totals and service costs. With one upload, a period-over-period increase or decrease cannot be measured. Watchdog checks suitable daily history separately."
            ],
        )
    finding(
        "context",
        "Before you act",
        [
            "These are the records you supplied, not a verified invoice or a savings estimate. Check the period, account coverage, and billed-cost basis against your provider bill. Utilization, pricing changes, and business activity are needed to explain why costs moved."
        ],
    )
    reconciliation = [
        dict(
            id="reconciliation.cloud.uploaded-services",
            assertion="Supplied service net costs sum exactly to supplied Cloud billed cost.",
            input_metric_ids=[
                row["metric_ids"]["net"] for row in current_layout["services"]
            ],
            output_metric_id=cloud["id"],
            difference=Decimal(0),
            tolerance=Decimal(0),
            status="passed",
        )
    ]
    return dict(
        contract="ccac/1.1.0",
        document_type="tool_result",
        producer=dict(name="finops-lite", version=__version__),
        run_id=_run_id(run_id),
        generated_at=generated,
        mode=mode,
        period=period,
        inputs=inputs,
        quality=dict(
            status="partial",
            issues=[
                dict(
                    code="billing.coverage.partial",
                    severity="warning",
                    message="Supplied CSV records do not establish complete organizational Cloud coverage.",
                )
            ],
        ),
        metrics=metrics,
        findings=findings,
        opportunities=[],
        evidence=evidence,
        extensions=dict(
            finops_lite=dict(
                input_profile=PROFILE,
                billing=dict(
                    current=current_layout,
                    previous=previous_layout,
                    comparison=comparison,
                    finding_templates=templates,
                    reconciliation=reconciliation,
                    daily_observation_metric_ids=daily_ids,
                    excluded_daily_series=skipped,
                ),
            )
        ),
    )
