"""Read Periodic Transaction Report entries from the official House index."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from capitol_trade_watch.models import Filing, TrackedPerson

HOUSE_INDEX_URL = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
)
HOUSE_PTR_URL = (
    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{document_id}.pdf"
)
USER_AGENT = (
    "capitol-trade-watch/0.1 "
    "(+https://github.com/noor-ahmadi/capitol-trade-watch)"
)


class HouseIndexError(RuntimeError):
    """Raised when a House index cannot be downloaded or understood safely."""


class HouseIndexStatus(StrEnum):
    """Outcome of checking one filing-year index."""

    DOWNLOADED = "downloaded"
    NOT_MODIFIED = "not_modified"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class HouseIndexResult:
    """The result of checking one official House filing-year index."""

    year: int
    source_url: str
    status: HouseIndexStatus
    filings: tuple[Filing, ...] = ()
    last_modified: str | None = None


class HouseIndexClient:
    """Download House indexes and select PTRs for configured people."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urlopen,
        timeout: float = 15.0,
    ) -> None:
        self._opener = opener
        self._timeout = timeout

    def fetch_year(
        self,
        year: int,
        tracked_people: Iterable[TrackedPerson],
        *,
        modified_since: str | None = None,
    ) -> HouseIndexResult:
        """Fetch one year, optionally asking the server for changes only."""
        source_url = HOUSE_INDEX_URL.format(year=year)
        headers = {
            "Accept": "application/x-zip-compressed, application/zip;q=0.9",
            "User-Agent": USER_AGENT,
        }
        if modified_since:
            headers["If-Modified-Since"] = modified_since

        request = Request(source_url, headers=headers)
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                status = getattr(response, "status", 200)
                if status == 304:
                    return self._empty_result(
                        year,
                        source_url,
                        HouseIndexStatus.NOT_MODIFIED,
                        response.headers,
                    )
                if status == 404:
                    return self._empty_result(
                        year,
                        source_url,
                        HouseIndexStatus.NOT_FOUND,
                        response.headers,
                    )
                if status != 200:
                    raise HouseIndexError(
                        f"House index {year} returned unexpected HTTP status {status}"
                    )

                archive_bytes = response.read()
                last_modified = _header(response.headers, "Last-Modified")
        except HTTPError as error:
            if error.code == 304:
                return self._empty_result(
                    year,
                    source_url,
                    HouseIndexStatus.NOT_MODIFIED,
                    error.headers,
                )
            if error.code == 404:
                return self._empty_result(
                    year,
                    source_url,
                    HouseIndexStatus.NOT_FOUND,
                    error.headers,
                )
            raise HouseIndexError(
                f"could not download House index {year}: HTTP {error.code}"
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise HouseIndexError(f"could not download House index {year}: {error}") from error

        filings = parse_house_index_archive(archive_bytes, year, tracked_people)
        return HouseIndexResult(
            year=year,
            source_url=source_url,
            status=HouseIndexStatus.DOWNLOADED,
            filings=filings,
            last_modified=last_modified,
        )

    def fetch_recent(
        self,
        as_of: date,
        tracked_people: Iterable[TrackedPerson],
        *,
        modified_since: Mapping[int, str] | None = None,
    ) -> tuple[HouseIndexResult, HouseIndexResult]:
        """Fetch the current and prior filing years, in that order."""
        people = tuple(tracked_people)
        source_timestamps = modified_since or {}
        current = self.fetch_year(
            as_of.year,
            people,
            modified_since=source_timestamps.get(as_of.year),
        )
        prior = self.fetch_year(
            as_of.year - 1,
            people,
            modified_since=source_timestamps.get(as_of.year - 1),
        )
        return current, prior

    @staticmethod
    def _empty_result(
        year: int,
        source_url: str,
        status: HouseIndexStatus,
        headers: Any,
    ) -> HouseIndexResult:
        return HouseIndexResult(
            year=year,
            source_url=source_url,
            status=status,
            last_modified=_header(headers, "Last-Modified"),
        )


def parse_house_index_archive(
    archive_bytes: bytes,
    year: int,
    tracked_people: Iterable[TrackedPerson],
) -> tuple[Filing, ...]:
    """Parse matching PTR filings from a downloaded House index archive."""
    xml_name = f"{year}FD.xml"
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            matching_names = [
                name
                for name in archive.namelist()
                if PurePosixPath(name).name.casefold() == xml_name.casefold()
            ]
            if len(matching_names) != 1:
                raise HouseIndexError(
                    f"House index {year} must contain exactly one {xml_name}"
                )
            xml_bytes = archive.read(matching_names[0])
    except BadZipFile as error:
        raise HouseIndexError(f"House index {year} is not a valid ZIP archive") from error

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as error:
        raise HouseIndexError(f"House index {year} contains invalid XML") from error

    people_by_identity: dict[tuple[str, str, str], TrackedPerson] = {}
    for person in tracked_people:
        identity = (
            _normalize_name(person.first_name),
            _normalize_name(person.last_name),
            person.state.upper(),
        )
        if identity in people_by_identity:
            raise HouseIndexError(
                f"tracked-person configuration is ambiguous for {person.display_name}"
            )
        people_by_identity[identity] = person

    filings: list[Filing] = []
    seen_document_ids: set[str] = set()
    for member in root.iter():
        if _local_name(member.tag) != "Member":
            continue

        fields = {
            _local_name(child.tag): " ".join((child.text or "").split())
            for child in member
        }
        if fields.get("FilingType", "").upper() != "P":
            continue

        state_district = fields.get("StateDst", "").upper()
        identity = (
            _normalize_name(fields.get("First", "")),
            _normalize_name(fields.get("Last", "")),
            state_district[:2],
        )
        person = people_by_identity.get(identity)
        if person is None:
            continue

        document_id = fields.get("DocID", "")
        if not document_id.isdigit():
            raise HouseIndexError(
                f"matching House PTR for {person.display_name} has an invalid document ID"
            )
        if document_id in seen_document_ids:
            continue

        filing_year = fields.get("Year", "")
        if filing_year != str(year):
            raise HouseIndexError(
                f"House PTR {document_id} reports year {filing_year!r}, expected {year}"
            )
        try:
            filing_date = datetime.strptime(fields.get("FilingDate", ""), "%m/%d/%Y").date()
        except ValueError as error:
            raise HouseIndexError(
                f"House PTR {document_id} has an invalid filing date"
            ) from error

        seen_document_ids.add(document_id)
        filings.append(
            Filing(
                document_id=document_id,
                filer=person,
                filing_date=filing_date,
                source_url=HOUSE_PTR_URL.format(
                    year=year,
                    document_id=document_id,
                ),
            )
        )

    return tuple(sorted(filings, key=lambda filing: (filing.filing_date, filing.document_id)))


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    value = headers.get(name)
    return str(value) if value else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()
