"""Render parsed House filings as complete Markdown alerts."""

from __future__ import annotations

from dataclasses import dataclass

from capitol_trade_watch.house_report import HouseReportResult
from capitol_trade_watch.models import Filing, Transaction


class AlertRenderError(RuntimeError):
    """Raised when a filing cannot be represented as a safe alert."""


@dataclass(frozen=True, slots=True)
class DisclosureAlert:
    """A future GitHub issue title and body, without publishing behavior."""

    document_id: str
    title: str
    body: str


def render_disclosure_alert(report: HouseReportResult) -> DisclosureAlert:
    """Build one Markdown alert from one House report result."""
    filing = report.filing
    title = (
        f"{filing.filer.display_name} PTR {filing.document_id} "
        f"(filed {filing.filing_date.isoformat()})"
    )
    lines = [
        f"<!-- capitol-trade-watch:house-ptr:{filing.document_id} -->",
        "",
        (
            f"{_markdown_text(filing.filer.display_name)} filed a House Periodic "
            f"Transaction Report on **{filing.filing_date.isoformat()}**."
        ),
        "",
    ]

    if report.parse_error is not None:
        lines.extend(_render_parse_fallback(report.parse_error))
    else:
        if not filing.transactions:
            raise AlertRenderError(
                f"House PTR {filing.document_id} has no parsed transactions"
            )
        lines.extend(_render_transactions(filing))

    lines.extend(
        [
            "",
            (
                f"[Open the official House report for document "
                f"{filing.document_id}]({filing.source_url})"
            ),
            "",
            (
                "_This repeats the public filing as written. Congressional "
                "disclosures are delayed and this is not investment advice._"
            ),
        ]
    )
    return DisclosureAlert(
        document_id=filing.document_id,
        title=title,
        body="\n".join(lines) + "\n",
    )


def _render_transactions(filing: Filing) -> list[str]:
    lines = [f"## Transactions ({len(filing.transactions)})"]
    for number, transaction in enumerate(filing.transactions, start=1):
        lines.extend(
            [
                "",
                f"### {number}. {_markdown_text(transaction.asset)}",
                "",
                f"- Owner: {_owner_text(transaction)}",
                f"- Action: `{_inline_code(transaction.action)}`",
                f"- Transaction date: `{transaction.transaction_date.isoformat()}`",
                f"- Notification date: {_optional_date(transaction)}",
                f"- Amount: {_markdown_text(transaction.amount_range)}",
                (
                    "- Capital gains over $200: "
                    f"{_optional_text(transaction.capital_gains)}"
                ),
                (
                    "- Filing description: "
                    f"{_optional_text(transaction.description)}"
                ),
            ]
        )
    return lines


def _render_parse_fallback(parse_error: str) -> list[str]:
    return [
        "## Transaction details unavailable",
        "",
        "The filing was found, but its transaction table could not be read safely.",
        "",
        f"> {_markdown_text(_single_line(parse_error))}",
    ]


def _owner_text(transaction: Transaction) -> str:
    code = transaction.owner_code.strip().upper()
    if not code:
        return "not specified"
    if transaction.owner_label.startswith("unrecognized"):
        return f"`{_inline_code(code)}` (unrecognized)"
    return f"{transaction.owner_label} (`{_inline_code(code)}`)"


def _optional_date(transaction: Transaction) -> str:
    if transaction.notification_date is None:
        return "not specified"
    return f"`{transaction.notification_date.isoformat()}`"


def _optional_text(value: str | None) -> str:
    if value is None or not value.strip():
        return "not specified"
    return _markdown_text(value)


def _markdown_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "<", ">", "#"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _inline_code(value: str) -> str:
    return value.replace("`", "'")


def _single_line(value: str) -> str:
    return " ".join(value.split())
