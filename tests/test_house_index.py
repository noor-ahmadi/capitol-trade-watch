from __future__ import annotations

from datetime import date
from email.message import Message
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request
from zipfile import ZipFile

import pytest

from capitol_trade_watch.house_index import (
    HOUSE_INDEX_URL,
    HOUSE_PTR_URL,
    HouseIndexClient,
    HouseIndexError,
    HouseIndexStatus,
    parse_house_index_archive,
)
from capitol_trade_watch.models import TrackedPerson


@pytest.fixture
def pelosi() -> TrackedPerson:
    return TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_parses_only_matching_periodic_reports(pelosi: TrackedPerson) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<FinancialDisclosure>
  <Member>
    <Last>Pelosi</Last><First>Nancy</First><FilingType>O</FilingType>
    <StateDst>CA11</StateDst><Year>2025</Year>
    <FilingDate>5/15/2026</FilingDate><DocID>10075701</DocID>
  </Member>
  <Member>
    <Last>Pelosi</Last><First>  Nancy  </First><FilingType>P</FilingType>
    <StateDst>CA11</StateDst><Year>2025</Year>
    <FilingDate>7/9/2025</FilingDate><DocID>20030630</DocID>
  </Member>
  <Member>
    <Last>Other</Last><First>Someone</First><FilingType>P</FilingType>
    <StateDst>CA01</StateDst><Year>2025</Year>
    <FilingDate>7/9/2025</FilingDate><DocID>20000000</DocID>
  </Member>
</FinancialDisclosure>
"""

    filings = parse_house_index_archive(_archive(2025, xml), 2025, [pelosi])

    assert len(filings) == 1
    assert filings[0].document_id == "20030630"
    assert filings[0].filer is pelosi
    assert filings[0].filing_date == date(2025, 7, 9)
    assert filings[0].source_url == HOUSE_PTR_URL.format(
        year=2025, document_id="20030630"
    )
    assert filings[0].transactions == ()


def test_rejects_bad_archives_and_matching_rows(
    pelosi: TrackedPerson,
) -> None:
    with pytest.raises(HouseIndexError, match="not a valid ZIP"):
        parse_house_index_archive(b"not a zip", 2025, [pelosi])

    invalid_row = """<FinancialDisclosure><Member>
<Last>Pelosi</Last><First>Nancy</First><FilingType>P</FilingType>
<StateDst>CA11</StateDst><Year>2025</Year>
<FilingDate>not-a-date</FilingDate><DocID>20030630</DocID>
</Member></FinancialDisclosure>"""
    with pytest.raises(HouseIndexError, match="invalid filing date"):
        parse_house_index_archive(_archive(2025, invalid_row), 2025, [pelosi])


def test_fetch_year_uses_conditional_request(pelosi: TrackedPerson) -> None:
    captured: dict[str, Any] = {}
    last_modified = "Fri, 07 Aug 2026 13:00:55 GMT"

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            _archive(2025, _single_ptr_xml(2025)),
            headers={"Last-Modified": last_modified},
        )

    result = HouseIndexClient(opener=opener, timeout=4).fetch_year(
        2025,
        [pelosi],
        modified_since="Thu, 06 Aug 2026 13:00:55 GMT",
    )

    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == HOUSE_INDEX_URL.format(year=2025)
    assert request.get_header("If-modified-since") == (
        "Thu, 06 Aug 2026 13:00:55 GMT"
    )
    assert request.get_header("User-agent").startswith("capitol-trade-watch/")
    assert captured["timeout"] == 4
    assert result.status is HouseIndexStatus.DOWNLOADED
    assert result.last_modified == last_modified
    assert [filing.document_id for filing in result.filings] == ["20030630"]


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [
        (304, HouseIndexStatus.NOT_MODIFIED),
        (404, HouseIndexStatus.NOT_FOUND),
    ],
)
def test_handles_expected_http_statuses(
    pelosi: TrackedPerson,
    status_code: int,
    expected_status: HouseIndexStatus,
) -> None:
    headers = Message()
    headers["Last-Modified"] = "Fri, 07 Aug 2026 13:00:55 GMT"

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        raise HTTPError(request.full_url, status_code, "expected", headers, None)

    result = HouseIndexClient(opener=opener).fetch_year(2026, [pelosi])

    assert result.status is expected_status
    assert result.filings == ()
    assert result.last_modified == "Fri, 07 Aug 2026 13:00:55 GMT"


def test_wraps_network_errors(pelosi: TrackedPerson) -> None:
    def opener(request: Request, *, timeout: float) -> FakeResponse:
        raise URLError("network down")

    with pytest.raises(HouseIndexError, match="could not download House index 2026"):
        HouseIndexClient(opener=opener).fetch_year(2026, [pelosi])


def test_fetch_recent_checks_current_then_prior_year(pelosi: TrackedPerson) -> None:
    requested_years: list[int] = []
    conditional_headers: list[str | None] = []

    def opener(request: Request, *, timeout: float) -> FakeResponse:
        year = int(request.full_url.rsplit("/", maxsplit=1)[-1][:4])
        requested_years.append(year)
        conditional_headers.append(request.get_header("If-modified-since"))
        return FakeResponse(_archive(year, "<FinancialDisclosure />"))

    results = HouseIndexClient(opener=opener).fetch_recent(
        date(2026, 8, 8),
        [pelosi],
        modified_since={2026: "current timestamp", 2025: "prior timestamp"},
    )

    assert requested_years == [2026, 2025]
    assert conditional_headers == ["current timestamp", "prior timestamp"]
    assert [result.year for result in results] == [2026, 2025]


def _archive(year: int, xml: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"{year}FD.xml", xml)
    return buffer.getvalue()


def _single_ptr_xml(year: int) -> str:
    return f"""<FinancialDisclosure><Member>
<Last>Pelosi</Last><First>Nancy</First><FilingType>P</FilingType>
<StateDst>CA11</StateDst><Year>{year}</Year>
<FilingDate>7/9/{year}</FilingDate><DocID>20030630</DocID>
</Member></FinancialDisclosure>"""
