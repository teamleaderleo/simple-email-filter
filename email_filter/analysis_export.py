from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from .models import MailMessage, Policy
from .planner import build_retention_plan
from .review import build_unmatched_review, first_matching_policy, sender_domain

_EXPORT_VERSION = 1
_EXCEL_CELL_LIMIT = 32767
_XML_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _private_file(path)


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    _private_file(path)


def _policy_retention_label(policy: Policy) -> str:
    retention = policy.retention
    if retention.mode == "forever":
        return "forever"
    if retention.mode == "days":
        return f"{retention.days} days"
    if retention.mode == "latest":
        return f"latest {retention.keep_latest}"
    return f"{retention.days} days and latest {retention.keep_latest}"


def _build_policy_and_sender_rows(
    messages: Sequence[MailMessage],
    policies: Sequence[Policy],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    plan = build_retention_plan(messages, policies)
    selected_by_message = {item.message_id: item.policy_id for item in plan}

    policy_counts: dict[str, Counter[str]] = {
        policy.id: Counter() for policy in policies if policy.enabled
    }
    sender_stats: dict[str, dict[str, Any]] = {}

    for message in messages:
        policy = first_matching_policy(message, policies)
        selected_policy = selected_by_message.get(message.id)
        disposition = "unmatched"
        policy_id = ""

        if policy is not None:
            policy_id = policy.id
            if policy.retention.mode == "forever":
                disposition = "protected_forever"
            elif selected_policy:
                disposition = "selected"
            else:
                disposition = "kept_by_retention"
            policy_counts[policy.id]["matched"] += 1
            policy_counts[policy.id][disposition] += 1

        row = sender_stats.setdefault(
            message.sender,
            {
                "sender": message.sender,
                "domain": sender_domain(message.sender),
                "total": 0,
                "read": 0,
                "unread": 0,
                "matched": 0,
                "unmatched": 0,
                "protectedForever": 0,
                "keptByRetention": 0,
                "selected": 0,
                "firstReceived": message.received_at,
                "lastReceived": message.received_at,
                "policies": Counter(),
            },
        )
        row["total"] += 1
        row["read" if message.is_read else "unread"] += 1
        row["firstReceived"] = min(row["firstReceived"], message.received_at)
        row["lastReceived"] = max(row["lastReceived"], message.received_at)
        if policy is None:
            row["unmatched"] += 1
        else:
            row["matched"] += 1
            row["policies"][policy_id] += 1
            if disposition == "protected_forever":
                row["protectedForever"] += 1
            elif disposition == "kept_by_retention":
                row["keptByRetention"] += 1
            elif disposition == "selected":
                row["selected"] += 1

    policy_rows: list[dict[str, Any]] = []
    for policy in policies:
        if not policy.enabled:
            continue
        counts = policy_counts[policy.id]
        policy_rows.append(
            {
                "policyId": policy.id,
                "description": policy.description,
                "priority": policy.priority,
                "retentionMode": policy.retention.mode,
                "retention": _policy_retention_label(policy),
                "days": policy.retention.days,
                "keepLatest": policy.retention.keep_latest,
                "matched": counts["matched"],
                "protectedForever": counts["protected_forever"],
                "keptByRetention": counts["kept_by_retention"],
                "selected": counts["selected"],
            }
        )

    sender_rows: list[dict[str, Any]] = []
    for sender, values in sender_stats.items():
        policy_breakdown: Counter[str] = values.pop("policies")
        total = int(values["total"])
        top_policy = policy_breakdown.most_common(1)[0][0] if policy_breakdown else ""
        sender_rows.append(
            {
                **values,
                "firstReceived": _iso(values["firstReceived"]),
                "lastReceived": _iso(values["lastReceived"]),
                "unreadRate": (int(values["unread"]) / total) if total else 0.0,
                "topPolicy": top_policy,
                "policyBreakdown": _json_text(dict(sorted(policy_breakdown.items()))),
            }
        )

    sender_rows.sort(key=lambda row: (-int(row["total"]), str(row["sender"])))
    policy_rows.sort(key=lambda row: (int(row["priority"]), str(row["policyId"])))
    return policy_rows, sender_rows, selected_by_message


def _review_rows(review: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sender_rows: list[dict[str, Any]] = []
    pattern_rows: list[dict[str, Any]] = []
    for sender in review.get("senders") or []:
        sender_rows.append(
            {
                "sender": sender.get("sender", ""),
                "domain": sender.get("domain", ""),
                "count": sender.get("count", 0),
                "read": sender.get("read", 0),
                "unread": sender.get("unread", 0),
                "unreadRate": (
                    sender.get("unread", 0) / sender.get("count", 1)
                    if sender.get("count", 0)
                    else 0.0
                ),
                "firstReceived": sender.get("firstReceived", ""),
                "lastReceived": sender.get("lastReceived", ""),
                "manualReviewRecommended": bool(
                    sender.get("manualReviewRecommended", False)
                ),
                "manualReviewSignals": _json_text(
                    sender.get("manualReviewSignals") or {}
                ),
                "subjectSignals": _json_text(sender.get("subjectSignals") or {}),
                "byYear": _json_text(sender.get("byYear") or {}),
            }
        )
        for pattern in sender.get("topSubjectPatterns") or []:
            pattern_rows.append(
                {
                    "sender": sender.get("sender", ""),
                    "domain": sender.get("domain", ""),
                    "senderCount": sender.get("count", 0),
                    "manualReviewRecommended": bool(
                        sender.get("manualReviewRecommended", False)
                    ),
                    "patternCount": pattern.get("count", 0),
                    "redactedSubjectPattern": pattern.get("pattern", ""),
                }
            )
    return sender_rows, pattern_rows


@dataclass(frozen=True)
class _SheetSpec:
    name: str
    title: str
    subtitle: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    widths: tuple[float, ...]
    percentage_columns: frozenset[int] = frozenset()


def _column_name(index: int) -> str:
    value = index
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _clean_xml_text(value: Any) -> str:
    text = _XML_CONTROL_RE.sub("", str(value))
    if len(text) > _EXCEL_CELL_LIMIT:
        return text[: _EXCEL_CELL_LIMIT - 1] + "…"
    return text


def _cell_xml(ref: str, value: Any, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None:
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" t="n"{style_attr}><v>{value}</v></c>'
    text = _clean_xml_text(value)
    preserve = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return (
        f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>'
        f"{escape(text)}</t></is></c>"
    )


def _worksheet_xml(spec: _SheetSpec) -> str:
    column_count = max(1, len(spec.headers))
    last_column = _column_name(column_count)
    last_row = 4 + len(spec.rows)
    rows: list[str] = []

    rows.append(
        f'<row r="1" ht="28" customHeight="1">'
        f'{_cell_xml("A1", spec.title, 1)}</row>'
    )
    rows.append(
        f'<row r="2" ht="21" customHeight="1">'
        f'{_cell_xml("A2", spec.subtitle, 2)}</row>'
    )
    rows.append('<row r="3" ht="8" customHeight="1"/>')
    header_cells = "".join(
        _cell_xml(f"{_column_name(index)}4", header, 3)
        for index, header in enumerate(spec.headers, start=1)
    )
    rows.append(f'<row r="4" ht="30" customHeight="1">{header_cells}</row>')

    for row_index, values in enumerate(spec.rows, start=5):
        cells: list[str] = []
        for column_index, value in enumerate(values, start=1):
            style = 5 if column_index - 1 in spec.percentage_columns else 0
            cells.append(
                _cell_xml(
                    f"{_column_name(column_index)}{row_index}",
                    value,
                    style,
                )
            )
        rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    widths = list(spec.widths) + [16.0] * max(0, column_count - len(spec.widths))
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths[:column_count], start=1)
    )
    auto_filter = (
        f'<autoFilter ref="A4:{last_column}{last_row}"/>' if spec.rows else ""
    )
    merge_cells = (
        '<mergeCells count="2">'
        f'<mergeCell ref="A1:{last_column}1"/>'
        f'<mergeCell ref="A2:{last_column}2"/>'
        '</mergeCells>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        f'<dimension ref="A1:{last_column}{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A5" sqref="A5"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols}</cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'{auto_filter}{merge_cells}'
        '<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        '</worksheet>'
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>'
        '<fonts count="4">'
        '<font><sz val="11"/><name val="Aptos"/><family val="2"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="18"/><name val="Aptos Display"/></font>'
        '<font><color rgb="FFDCE6F1"/><sz val="10"/><name val="Aptos"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Aptos"/></font>'
        '</fonts>'
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF17324D"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left/><right/><top/><bottom style="thin"><color rgb="FFD1D5DB"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/>'
        '<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        '</styleSheet>'
    )


def _write_xlsx(path: Path, sheets: Sequence[_SheetSpec], generated_at: str) -> None:
    sheet_names = [sheet.name for sheet in sheets]
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="14000"/></bookViews>'
        f'<sheets>{workbook_sheets}</sheets>'
        '<calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/>'
        '</workbook>'
    )
    workbook_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{workbook_relationships}'
        f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    content_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{content_overrides}'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>Mailbox analysis</dc:title><dc:creator>simple-email-filter</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{escape(generated_at)}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{escape(generated_at)}</dcterms:modified>'
        '</cp:coreProperties>'
    )
    vector_items = "".join(f'<vt:lpstr>{escape(name)}</vt:lpstr>' for name in sheet_names)
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>simple-email-filter</Application>'
        f'<TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{vector_items}</vt:vector></TitlesOfParts>'
        '</Properties>'
    )

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _worksheet_xml(sheet),
            )
    _private_file(path)


def _sheet_specs(
    summary: dict[str, Any],
    policy_rows: Sequence[dict[str, Any]],
    sender_rows: Sequence[dict[str, Any]],
    unmatched_sender_rows: Sequence[dict[str, Any]],
    pattern_rows: Sequence[dict[str, Any]],
    *,
    policy_path: str,
    generated_at: str,
) -> list[_SheetSpec]:
    overview_rows = (
        ("Generated", generated_at, "UTC"),
        ("Policy", policy_path, "Evaluated locally from the saved snapshot"),
        ("Folder", summary.get("folder", "inbox"), "Microsoft was not contacted"),
        ("Scanned", summary.get("scanned", 0), "Messages in the saved snapshot"),
        ("Read", summary.get("read", 0), ""),
        ("Unread", summary.get("unread", 0), ""),
        ("Matched", summary.get("matched", 0), "Messages covered by a policy"),
        ("Unmatched", summary.get("unmatched", 0), "Review before adding broad rules"),
        ("Protected forever", summary.get("protectedForever", 0), "Never selected by retention"),
        ("Kept by retention", summary.get("keptByRetention", 0), "Still inside its retention window"),
        ("Selected", summary.get("selected", 0), "Eligible to move to Deleted Items after review"),
        ("Privacy", "No message IDs, bodies, previews or attachments", "Subjects appear only as redacted aggregate patterns"),
    )
    policy_sheet_rows = tuple(
        (
            row["policyId"],
            row["description"],
            row["priority"],
            row["retention"],
            row["matched"],
            row["protectedForever"],
            row["keptByRetention"],
            row["selected"],
        )
        for row in policy_rows
    )
    sender_sheet_rows = tuple(
        (
            row["sender"],
            row["domain"],
            row["total"],
            row["read"],
            row["unread"],
            row["unreadRate"],
            row["firstReceived"],
            row["lastReceived"],
            row["matched"],
            row["unmatched"],
            row["protectedForever"],
            row["keptByRetention"],
            row["selected"],
            row["topPolicy"],
            row["policyBreakdown"],
        )
        for row in sender_rows
    )
    unmatched_sheet_rows = tuple(
        (
            row["sender"],
            row["domain"],
            row["count"],
            row["read"],
            row["unread"],
            row["unreadRate"],
            row["firstReceived"],
            row["lastReceived"],
            row["manualReviewRecommended"],
            row["manualReviewSignals"],
            row["subjectSignals"],
            row["byYear"],
        )
        for row in unmatched_sender_rows
    )
    pattern_sheet_rows = tuple(
        (
            row["sender"],
            row["domain"],
            row["senderCount"],
            row["manualReviewRecommended"],
            row["patternCount"],
            row["redactedSubjectPattern"],
        )
        for row in pattern_rows
    )
    dictionary_rows = (
        ("mailbox-summary.json", "Nested audit summary and export metadata", "Uploadable; aggregate sender/domain data only"),
        ("sender-summary.csv", "One row per sender with policy disposition counts", "Contains sender addresses, no subjects or message IDs"),
        ("policy-impact.csv", "One row per enabled policy", "Aggregate counts only"),
        ("unmatched-senders.csv", "One row per unmatched sender", "Contains aggregate signals and no raw subjects"),
        ("subject-patterns.csv", "Redacted subject patterns grouped by sender", "No message IDs or bodies"),
        ("unmatched-review.json", "Nested unmatched sender review", "Redacted subject patterns"),
        ("mailbox-analysis.xlsx", "Human review workbook containing the same aggregate exports", "Safe alternative to pasting large reports"),
        ("messages.jsonl", "Private raw local snapshot", "Never upload; excluded from this export directory"),
        ("plan.jsonl", "Private message-level retention plan", "Never upload; excluded from this export directory"),
    )
    return [
        _SheetSpec(
            name="Overview",
            title="Mailbox Analysis",
            subtitle="Aggregate, privacy-minimised export generated from the private local snapshot",
            headers=("Metric", "Value", "Notes"),
            rows=overview_rows,
            widths=(24, 34, 58),
        ),
        _SheetSpec(
            name="Policy Impact",
            title="Policy Impact",
            subtitle="First matching policy wins; selected means eligible for Deleted Items after review",
            headers=("Policy ID", "Description", "Priority", "Retention", "Matched", "Protected Forever", "Kept", "Selected"),
            rows=policy_sheet_rows,
            widths=(30, 60, 10, 24, 12, 18, 12, 12),
        ),
        _SheetSpec(
            name="Sender Summary",
            title="Sender Summary",
            subtitle="One aggregate row per sender; no message IDs or subjects",
            headers=("Sender", "Domain", "Total", "Read", "Unread", "Unread %", "First Received", "Last Received", "Matched", "Unmatched", "Protected", "Kept", "Selected", "Top Policy", "Policy Breakdown"),
            rows=sender_sheet_rows,
            widths=(36, 28, 10, 10, 10, 11, 24, 24, 11, 11, 12, 10, 10, 30, 48),
            percentage_columns=frozenset({5}),
        ),
        _SheetSpec(
            name="Unmatched Senders",
            title="Unmatched Senders",
            subtitle="Use this sheet to decide which senders need new rules or should remain unmatched",
            headers=("Sender", "Domain", "Count", "Read", "Unread", "Unread %", "First Received", "Last Received", "Manual Review", "Manual Review Signals", "Subject Signals", "By Year"),
            rows=unmatched_sheet_rows,
            widths=(36, 28, 10, 10, 10, 11, 24, 24, 14, 42, 42, 36),
            percentage_columns=frozenset({5}),
        ),
        _SheetSpec(
            name="Subject Patterns",
            title="Redacted Subject Patterns",
            subtitle="Numbers, email addresses, URLs and identifier-like tokens are redacted before export",
            headers=("Sender", "Domain", "Sender Count", "Manual Review", "Pattern Count", "Redacted Subject Pattern"),
            rows=pattern_sheet_rows,
            widths=(36, 28, 14, 14, 14, 80),
        ),
        _SheetSpec(
            name="Data Dictionary",
            title="Data Dictionary and Privacy",
            subtitle="Files inside this export directory are designed for analysis; raw mailbox state remains one directory above",
            headers=("File", "Purpose", "Privacy Notes"),
            rows=dictionary_rows,
            widths=(30, 62, 70),
        ),
    ]


def export_mailbox_analysis(
    output_dir: str | Path,
    messages: Iterable[MailMessage],
    policies: Iterable[Policy],
    summary: dict[str, Any],
    *,
    policy_path: str,
    samples_per_sender: int = 6,
) -> dict[str, Any]:
    """Write aggregate JSON, CSV and XLSX analysis files without raw message content."""
    destination = Path(output_dir)
    _private_dir(destination)
    message_list = list(messages)
    policy_list = list(policies)
    generated_at = _iso(_utc_now())

    policy_rows, sender_rows, _ = _build_policy_and_sender_rows(
        message_list,
        policy_list,
    )
    unmatched_sender_count = len(
        {
            message.sender
            for message in message_list
            if first_matching_policy(message, policy_list) is None
        }
    )
    review = build_unmatched_review(
        message_list,
        policy_list,
        top_senders=max(1, unmatched_sender_count),
        samples_per_sender=max(1, samples_per_sender),
    )
    unmatched_sender_rows, pattern_rows = _review_rows(review)

    export_summary = {
        **summary,
        "exportVersion": _EXPORT_VERSION,
        "exportGeneratedAt": generated_at,
        "exportPolicyPath": policy_path,
        "exportCounts": {
            "policies": len(policy_rows),
            "senders": len(sender_rows),
            "unmatchedSenders": len(unmatched_sender_rows),
            "redactedSubjectPatterns": len(pattern_rows),
        },
        "privacy": {
            "messageIdsIncluded": False,
            "bodiesIncluded": False,
            "previewsIncluded": False,
            "attachmentsIncluded": False,
            "rawSubjectsIncluded": False,
            "senderAddressesIncluded": True,
            "subjectsRedacted": True,
        },
    }
    review.update(
        {
            "exportVersion": _EXPORT_VERSION,
            "generatedAt": generated_at,
            "policyPath": policy_path,
        }
    )

    paths = {
        "summary": destination / "mailbox-summary.json",
        "senderCsv": destination / "sender-summary.csv",
        "policyCsv": destination / "policy-impact.csv",
        "unmatchedSenderCsv": destination / "unmatched-senders.csv",
        "subjectPatternCsv": destination / "subject-patterns.csv",
        "review": destination / "unmatched-review.json",
        "workbook": destination / "mailbox-analysis.xlsx",
        "readme": destination / "README.txt",
        "manifest": destination / "manifest.json",
    }

    _write_json(paths["summary"], export_summary)
    _write_json(paths["review"], review)
    _write_csv(
        paths["senderCsv"],
        sender_rows,
        (
            "sender", "domain", "total", "read", "unread", "unreadRate",
            "firstReceived", "lastReceived", "matched", "unmatched",
            "protectedForever", "keptByRetention", "selected", "topPolicy",
            "policyBreakdown",
        ),
    )
    _write_csv(
        paths["policyCsv"],
        policy_rows,
        (
            "policyId", "description", "priority", "retentionMode", "retention",
            "days", "keepLatest", "matched", "protectedForever",
            "keptByRetention", "selected",
        ),
    )
    _write_csv(
        paths["unmatchedSenderCsv"],
        unmatched_sender_rows,
        (
            "sender", "domain", "count", "read", "unread", "unreadRate",
            "firstReceived", "lastReceived", "manualReviewRecommended",
            "manualReviewSignals", "subjectSignals", "byYear",
        ),
    )
    _write_csv(
        paths["subjectPatternCsv"],
        pattern_rows,
        (
            "sender", "domain", "senderCount", "manualReviewRecommended",
            "patternCount", "redactedSubjectPattern",
        ),
    )

    sheets = _sheet_specs(
        export_summary,
        policy_rows,
        sender_rows,
        unmatched_sender_rows,
        pattern_rows,
        policy_path=policy_path,
        generated_at=generated_at,
    )
    _write_xlsx(paths["workbook"], sheets, generated_at)

    readme = f"""Mailbox analysis export
=======================

Generated: {generated_at}
Policy: {policy_path}
Messages represented: {len(message_list)}

Recommended upload
------------------
Upload mailbox-analysis.xlsx for human review and mailbox-summary.json for exact nested counts.
The CSV files are useful for focused filtering, sorting and programmatic analysis.

Privacy
-------
This export contains aggregate sender addresses, domains, counts and redacted subject patterns.
It contains no message IDs, message bodies, previews, attachments or raw subjects.

Do not upload files from the parent state directory, especially messages.jsonl, plan.jsonl or apply-results.jsonl.
Those files are intentionally excluded from this export directory.

Files
-----
mailbox-analysis.xlsx    Multi-sheet review workbook
mailbox-summary.json     Audit summary and export metadata
sender-summary.csv       Aggregate policy impact per sender
policy-impact.csv        Aggregate impact per enabled policy
unmatched-senders.csv    Aggregate unmatched sender review
subject-patterns.csv     Redacted subject patterns
unmatched-review.json    Nested unmatched review
manifest.json            File names, counts and privacy declaration
"""
    paths["readme"].write_text(readme, encoding="utf-8")
    _private_file(paths["readme"])

    manifest = {
        "version": _EXPORT_VERSION,
        "generatedAt": generated_at,
        "policyPath": policy_path,
        "files": {key: value.name for key, value in paths.items() if key != "manifest"},
        "counts": export_summary["exportCounts"],
        "privacy": export_summary["privacy"],
    }
    _write_json(paths["manifest"], manifest)

    return {
        "outputDir": str(destination),
        "generatedAt": generated_at,
        "policyPath": policy_path,
        "counts": export_summary["exportCounts"],
        "files": manifest["files"],
        "privacy": export_summary["privacy"],
    }
