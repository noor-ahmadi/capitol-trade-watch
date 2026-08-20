from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from urllib.request import Request

import pytest

import capitol_trade_watch.house_report as house_report
from capitol_trade_watch.house_report import (
    HouseReportClient,
    HouseReportError,
    parse_house_ptr_pdf,
)
from capitol_trade_watch.models import Filing, TrackedPerson


@pytest.fixture
def filing() -> Filing:
    pelosi = TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )
    return Filing(
        document_id="20000001",
        filer=pelosi,
        filing_date=date(2025, 2, 14),
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/"
            "ptr-pdfs/2025/20000001.pdf"
        ),
    )


class FakeTable:
    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        self.bbox = bbox


class FakePage:
    def __init__(
        self,
        words: list[dict[str, Any]],
        *,
        curves: list[dict[str, Any]] | None = None,
    ) -> None:
        self._words = words
        self.curves = curves or []

    def extract_words(self, **_settings: Any) -> list[dict[str, Any]]:
        return self._words

    def find_tables(self) -> list[FakeTable]:
        return [FakeTable((22, 70, 576, 250))]


class FakePdf:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    def __enter__(self) -> FakePdf:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeResponse:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_parses_wrapped_rows_across_pages(
    filing: Filing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = _report_pages("20000001")
    monkeypatch.setattr(house_report.pdfplumber, "open", lambda _stream: FakePdf(pages))

    parsed = parse_house_ptr_pdf(b"%PDF-fake", filing)

    assert parsed.source_url == filing.source_url
    assert len(parsed.transactions) == 2
    first, second = parsed.transactions
    assert first.owner_code == "SP"
    assert first.asset == "NVIDIA Corporation Common Stock (NVDA) [OP]"
    assert first.action == "P"
    assert first.transaction_date == date(2025, 1, 14)
    assert first.notification_date == date(2025, 1, 14)
    assert first.amount_range == "$500,001 - $1,000,000"
    assert first.capital_gains == "Yes"
    assert first.description == (
        "Purchased 50 call options with a strike price of $80 and an "
        "expiration date of 1/16/26."
    )
    assert second.owner_code == ""
    assert second.action == "S (partial)"
    assert second.notification_date is None
    assert second.amount_range == "$15,001 - $50,000"
    assert second.capital_gains == "No"
    assert second.description == "Sold 100 units."


def test_rejects_a_pdf_for_a_different_filing(
    filing: Filing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = _report_pages("29999999")
    monkeypatch.setattr(house_report.pdfplumber, "open", lambda _stream: FakePdf(pages))

    with pytest.raises(HouseReportError, match="contains filing ID 29999999"):
        parse_house_ptr_pdf(b"%PDF-fake", filing)


def test_client_keeps_official_link_when_parsing_fails(
    filing: Filing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        captured.update(request=request, timeout=timeout)
        return FakeResponse(b"not really a PDF")

    def fail_to_parse(_pdf_bytes: bytes, report_filing: Filing) -> Filing:
        assert report_filing is filing
        raise HouseReportError("the transaction table could not be read")

    monkeypatch.setattr(house_report, "parse_house_ptr_pdf", fail_to_parse)
    result = HouseReportClient(opener=opener, timeout=4).fetch(filing)

    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == filing.source_url
    assert request.get_header("Accept") == "application/pdf"
    assert request.get_header("User-agent").startswith("capitol-trade-watch/")
    assert captured["timeout"] == 4
    assert result.parsed is False
    assert result.filing is filing
    assert result.filing.source_url == filing.source_url
    assert result.parse_error == "the transaction table could not be read"


def test_client_returns_parsed_filing(
    filing: Filing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_filing = replace(filing, transactions=())

    monkeypatch.setattr(
        house_report,
        "parse_house_ptr_pdf",
        lambda _pdf_bytes, _filing: parsed_filing,
    )
    result = HouseReportClient(
        opener=lambda _request, timeout: FakeResponse(b"%PDF-fake")
    ).fetch(filing)

    assert result.parsed is True
    assert result.filing is parsed_filing
    assert result.parse_error is None


def _report_pages(document_id: str) -> list[FakePage]:
    first_page: list[dict[str, Any]] = []
    first_page += _words(40, 450, "Filing ID", f"#{document_id}")
    first_page += _table_header()
    first_page += _words(120, 65, "SP")
    first_page += _words(120, 104, "NVIDIA Corporation")
    first_page += _words(120, 260, "P")
    first_page += _words(120, 325, "01/14/2025")
    first_page += _words(120, 380, "01/14/2025")
    first_page += _words(120, 444, "$500,001 -")
    first_page += _words(131, 104, "Common Stock (NVDA) [OP]")
    first_page += _words(131, 444, "$1,000,000")
    first_page += _words(145, 104, "F\x00\x00\x00 S\x00\x00\x00: New")
    first_page += _words(
        158,
        104,
        "D\x00\x00\x00: Purchased 50 call options with a strike price of $80",
    )
    first_page += _words(171, 104, "and an expiration date of")

    second_page: list[dict[str, Any]] = []
    second_page += _table_header()
    second_page += _words(115, 104, "1/16/26.")
    second_page += _words(140, 104, "Mutual Fund Units [OT]")
    second_page += _words(140, 260, "S (partial)")
    second_page += _words(140, 325, "02/01/2025")
    second_page += _words(140, 444, "$15,001 -")
    second_page += _words(151, 444, "$50,000")
    second_page += _words(165, 104, "Filing Status: New")
    second_page += _words(180, 104, "Description: Sold 100 units.")

    return [
        FakePage(first_page, curves=_checked_box(120)),
        FakePage(second_page, curves=_empty_box(140)),
    ]


def _table_header() -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    words += _words(80, 25, "ID")
    words += _words(80, 65, "Owner")
    words += _words(80, 104, "Asset")
    words += _words(80, 260, "Transaction")
    words += _words(80, 325, "Date")
    words += _words(80, 380, "Notification")
    words += _words(80, 444, "Amount")
    words += _words(80, 524, "Cap.")
    words += _words(91, 260, "Type")
    words += _words(91, 380, "Date")
    words += _words(91, 524, "Gains >")
    words += _words(102, 524, "$200?")
    return words


def _words(top: float, x0: float, *parts: str) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    current_x = x0
    for part in " ".join(parts).split():
        width = max(len(part) * 3.8, 4)
        words.append(
            {
                "text": part,
                "x0": current_x,
                "x1": current_x + width,
                "top": top,
            }
        )
        current_x += width + 2
    return words


def _checked_box(top: float) -> list[dict[str, Any]]:
    return _empty_box(top) + [
        {
            "x0": 544.5,
            "top": top + 1,
            "width": 9.75,
            "fill": True,
            "non_stroking_color": (0.46, 0.46, 0.46),
        }
    ]


def _empty_box(top: float) -> list[dict[str, Any]]:
    return [
        {
            "x0": 544.6,
            "top": top + 1,
            "width": 9.45,
            "fill": True,
            "non_stroking_color": (0.66, 0.66, 0.66),
        }
    ]
