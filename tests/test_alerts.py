from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from capitol_trade_watch.alerts import AlertRenderError, render_disclosure_alert
from capitol_trade_watch.house_report import HouseReportResult
from capitol_trade_watch.models import Filing, TrackedPerson, Transaction


@pytest.fixture
def filing() -> Filing:
    pelosi = TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )
    transactions = (
        Transaction(
            owner_code="SP",
            asset="Broadcom Inc. - Common Stock (AVGO) [ST]",
            action="P",
            transaction_date=date(2025, 6, 20),
            notification_date=date(2025, 6, 20),
            amount_range="$1,000,001 - $5,000,000",
            capital_gains="No",
            description=(
                "Exercised 200 call options at an $80 strike price, "
                "expiring 6/20/25."
            ),
        ),
        Transaction(
            owner_code="",
            asset="Matthews International Mutual Fund [OT]",
            action="S",
            transaction_date=date(2025, 6, 20),
            notification_date=None,
            amount_range="$15,001 - $50,000",
            capital_gains=None,
            description="Sale of 2,822 units.",
        ),
    )
    return Filing(
        document_id="20030630",
        filer=pelosi,
        filing_date=date(2025, 7, 9),
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/"
            "ptr-pdfs/2025/20030630.pdf"
        ),
        transactions=transactions,
    )


def test_renders_every_transaction_and_the_official_link(filing: Filing) -> None:
    alert = render_disclosure_alert(HouseReportResult(filing=filing))

    assert alert.document_id == "20030630"
    assert alert.title == "Nancy Pelosi PTR 20030630 (filed 2025-07-09)"
    assert alert.body.startswith(
        "<!-- capitol-trade-watch:house-ptr:20030630 -->\n"
    )
    assert alert.body.count("\n### ") == 2
    assert "### 1. Broadcom Inc. - Common Stock (AVGO) \\[ST\\]" in alert.body
    assert "### 2. Matthews International Mutual Fund \\[OT\\]" in alert.body
    assert "- Owner: spouse (`SP`)" in alert.body
    assert "- Owner: not specified" in alert.body
    assert "- Action: `P`" in alert.body
    assert "- Notification date: not specified" in alert.body
    assert "- Amount: $1,000,001 - $5,000,000" in alert.body
    assert "- Capital gains over $200: No" in alert.body
    assert "- Capital gains over $200: not specified" in alert.body
    assert "Exercised 200 call options at an $80 strike price" in alert.body
    assert alert.body.count(filing.source_url) == 1
    assert alert.body.endswith("not investment advice._\n")


def test_renders_a_linked_fallback_when_parsing_fails(filing: Filing) -> None:
    unparsed = replace(filing, transactions=())
    alert = render_disclosure_alert(
        HouseReportResult(
            filing=unparsed,
            parse_error="could not read *page 2*\nwithout guessing",
        )
    )

    assert "## Transaction details unavailable" in alert.body
    assert "could not read \\*page 2\\* without guessing" in alert.body
    assert "## Transactions" not in alert.body
    assert alert.body.count(filing.source_url) == 1


def test_rejects_a_supposedly_parsed_report_with_no_rows(filing: Filing) -> None:
    empty_filing = replace(filing, transactions=())

    with pytest.raises(AlertRenderError, match="has no parsed transactions"):
        render_disclosure_alert(HouseReportResult(filing=empty_filing))
