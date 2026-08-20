"""Download and read transaction rows from official House PTR PDFs."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pdfplumber
from pdfminer.pdfdocument import PDFException

from capitol_trade_watch.house_index import USER_AGENT
from capitol_trade_watch.models import Filing, Transaction

_OWNER_LEFT = 62.0
_ASSET_LEFT = 101.0
_TYPE_LEFT = 257.5
_TRANSACTION_DATE_LEFT = 322.0
_NOTIFICATION_DATE_LEFT = 377.0
_AMOUNT_LEFT = 441.5
_CAPITAL_GAINS_LEFT = 521.5
_TABLE_RIGHT = 577.0

_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_ACTION = re.compile(r"^[A-Z](?:\s+\([^)]+\))?$")
_FILING_ID = re.compile(r"\bFiling\s+ID\s*#\s*(\d+)\b", re.IGNORECASE)


class HouseReportError(RuntimeError):
    """Raised when a House PTR cannot be downloaded or parsed safely."""


@dataclass(frozen=True, slots=True)
class HouseReportResult:
    """A parsed filing, or the original filing plus a parse warning."""

    filing: Filing
    parse_error: str | None = None

    @property
    def parsed(self) -> bool:
        """Return whether transaction rows were parsed successfully."""
        return self.parse_error is None


class HouseReportClient:
    """Download an official PTR and keep its source link on parse failure."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urlopen,
        timeout: float = 15.0,
    ) -> None:
        self._opener = opener
        self._timeout = timeout

    def fetch(self, filing: Filing) -> HouseReportResult:
        """Download one filing and parse it without hiding a broken report."""
        request = Request(
            filing.source_url,
            headers={
                "Accept": "application/pdf",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise HouseReportError(
                        f"House PTR {filing.document_id} returned HTTP {status}"
                    )
                pdf_bytes = response.read()
        except HTTPError as error:
            raise HouseReportError(
                f"could not download House PTR {filing.document_id}: HTTP {error.code}"
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise HouseReportError(
                f"could not download House PTR {filing.document_id}: {error}"
            ) from error

        try:
            parsed_filing = parse_house_ptr_pdf(pdf_bytes, filing)
        except HouseReportError as error:
            return HouseReportResult(filing=filing, parse_error=str(error))
        return HouseReportResult(filing=parsed_filing)


def parse_house_ptr_pdf(pdf_bytes: bytes, filing: Filing) -> Filing:
    """Return a copy of a filing populated from its official PTR PDF."""
    if not pdf_bytes:
        raise HouseReportError(f"House PTR {filing.document_id} is empty")

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as report:
            return _parse_report_pages(tuple(report.pages), filing)
    except HouseReportError:
        raise
    except (PDFException, OSError, TypeError, ValueError) as error:
        raise HouseReportError(
            f"could not parse House PTR {filing.document_id}: {error}"
        ) from error


@dataclass(frozen=True, slots=True)
class _Word:
    text: str
    x0: float
    x1: float
    top: float


@dataclass(frozen=True, slots=True)
class _Line:
    top: float
    words: tuple[_Word, ...]


@dataclass(slots=True)
class _TransactionBuilder:
    owner_code: str
    asset_parts: list[str]
    action: str
    transaction_date: datetime
    notification_date: datetime | None
    amount_parts: list[str]
    capital_gains: str | None
    description_parts: list[str] = field(default_factory=list)
    phase: str = "asset"

    def finish(self) -> Transaction:
        asset = _join_parts(self.asset_parts)
        amount = _join_parts(self.amount_parts)
        if not asset or not amount:
            raise HouseReportError("a House PTR transaction row is incomplete")
        return Transaction(
            owner_code=self.owner_code,
            asset=asset,
            action=self.action,
            transaction_date=self.transaction_date.date(),
            notification_date=(
                self.notification_date.date()
                if self.notification_date is not None
                else None
            ),
            amount_range=amount,
            capital_gains=self.capital_gains,
            description=_join_parts(self.description_parts),
        )


def _parse_report_pages(pages: Sequence[Any], filing: Filing) -> Filing:
    if not pages:
        raise HouseReportError(f"House PTR {filing.document_id} has no pages")

    words_by_page = tuple(_extract_words(page) for page in pages)
    _check_filing_id(words_by_page[0], filing.document_id)

    transactions: list[Transaction] = []
    current: _TransactionBuilder | None = None
    found_table = False

    for page, words in zip(pages, words_by_page, strict=True):
        table_bbox = _find_transaction_table(page)
        if table_bbox is None:
            continue
        found_table = True

        table_lines = _group_lines(
            word
            for word in words
            if table_bbox[0] <= word.x0 <= table_bbox[2]
            and table_bbox[1] <= word.top <= table_bbox[3]
        )
        header_bottom = _header_bottom(table_lines, table_bbox[1])

        for line in table_lines:
            if line.top <= header_bottom:
                continue

            next_transaction = _transaction_from_line(line, page)
            if next_transaction is not None:
                if current is not None:
                    transactions.append(current.finish())
                current = next_transaction
                continue

            if current is None:
                continue

            label = _detail_label(line)
            if label is not None:
                label_name, value = label
                if label_name == "description":
                    current.phase = "description"
                    if value:
                        current.description_parts.append(value)
                else:
                    current.phase = "ignore"
                continue

            if current.phase == "asset":
                asset = _column_text(line, _ASSET_LEFT, _TYPE_LEFT)
                amount = _column_text(line, _AMOUNT_LEFT, _CAPITAL_GAINS_LEFT)
                if asset:
                    current.asset_parts.append(asset)
                if amount:
                    current.amount_parts.append(amount)
            elif current.phase == "description":
                continuation = _column_text(line, _ASSET_LEFT, _TABLE_RIGHT)
                if continuation:
                    current.description_parts.append(continuation)

    if current is not None:
        transactions.append(current.finish())

    if not found_table or not transactions:
        raise HouseReportError(
            f"House PTR {filing.document_id} contains no readable transaction rows"
        )
    return replace(filing, transactions=tuple(transactions))


def _extract_words(page: Any) -> tuple[_Word, ...]:
    extracted = page.extract_words(x_tolerance=1, y_tolerance=2)
    words: list[_Word] = []
    for raw_word in extracted:
        text = _clean_text(raw_word.get("text", ""))
        if not text:
            continue
        try:
            words.append(
                _Word(
                    text=text,
                    x0=float(raw_word["x0"]),
                    x1=float(raw_word["x1"]),
                    top=float(raw_word["top"]),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HouseReportError(
                "a House PTR contains malformed text positions"
            ) from error
    return tuple(words)


def _check_filing_id(words: Iterable[_Word], expected_id: str) -> None:
    page_text = " ".join(word.text for word in words)
    match = _FILING_ID.search(page_text)
    if match is None:
        raise HouseReportError("the House PTR filing ID could not be read")
    if match.group(1) != expected_id:
        raise HouseReportError(
            f"House PTR {expected_id} contains filing ID {match.group(1)}"
        )


def _find_transaction_table(page: Any) -> tuple[float, float, float, float] | None:
    candidates: list[tuple[float, float, float, float]] = []
    for table in page.find_tables():
        try:
            bbox = tuple(float(value) for value in table.bbox)
        except (AttributeError, TypeError, ValueError):
            continue
        if len(bbox) != 4:
            continue
        left, top, right, bottom = bbox
        if right - left >= 500 and bottom > top:
            candidates.append((left, top, right, bottom))
    if not candidates:
        return None
    return max(candidates, key=lambda bbox: (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


def _group_lines(words: Iterable[_Word]) -> tuple[_Line, ...]:
    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        if not lines or abs(lines[-1][0].top - word.top) > 2:
            lines.append([word])
        else:
            lines[-1].append(word)
    return tuple(
        _Line(
            top=sum(word.top for word in line_words) / len(line_words),
            words=tuple(sorted(line_words, key=lambda word: word.x0)),
        )
        for line_words in lines
    )


def _header_bottom(lines: Sequence[_Line], table_top: float) -> float:
    header_lines = [
        line
        for line in lines
        if line.top <= table_top + 40
        and any(
            word.text.casefold()
            in {"owner", "asset", "notification", "amount", "gains", "$200?"}
            for word in line.words
        )
    ]
    return max((line.top for line in header_lines), default=table_top) + 2


def _transaction_from_line(line: _Line, page: Any) -> _TransactionBuilder | None:
    transaction_date_text = _column_text(
        line,
        _TRANSACTION_DATE_LEFT,
        _NOTIFICATION_DATE_LEFT,
    )
    action = _column_text(line, _TYPE_LEFT, _TRANSACTION_DATE_LEFT)
    amount = _column_text(line, _AMOUNT_LEFT, _CAPITAL_GAINS_LEFT)
    if (
        not _DATE.fullmatch(transaction_date_text)
        or not _ACTION.fullmatch(action)
        or not amount.startswith("$")
    ):
        return None

    asset = _column_text(line, _ASSET_LEFT, _TYPE_LEFT)
    if not asset:
        raise HouseReportError("a House PTR transaction row has no asset")

    notification_text = _column_text(
        line,
        _NOTIFICATION_DATE_LEFT,
        _AMOUNT_LEFT,
    )
    notification_date = (
        _parse_date(notification_text, "notification")
        if notification_text
        else None
    )
    return _TransactionBuilder(
        owner_code=_column_text(line, _OWNER_LEFT, _ASSET_LEFT),
        asset_parts=[asset],
        action=action,
        transaction_date=_parse_date(transaction_date_text, "transaction"),
        notification_date=notification_date,
        amount_parts=[amount],
        capital_gains=_capital_gains_value(page, line.top),
    )


def _capital_gains_value(page: Any, row_top: float) -> str | None:
    checkbox_shapes = [
        curve
        for curve in getattr(page, "curves", ())
        if 540 <= float(curve.get("x0", 0)) <= 556
        and abs(float(curve.get("top", -100)) - row_top) <= 4
        and 5 <= float(curve.get("width", 0)) <= 12
    ]
    if not checkbox_shapes:
        return None

    for shape in checkbox_shapes:
        color = shape.get("non_stroking_color")
        if not shape.get("fill") or not isinstance(color, (tuple, list)):
            continue
        numeric_color = [float(component) for component in color]
        if numeric_color and max(numeric_color) < 0.6:
            return "Yes"
    return "No"


def _detail_label(line: _Line) -> tuple[str, str] | None:
    words = [word for word in line.words if _ASSET_LEFT <= word.x0 < 220]
    colon_index = next(
        (index for index, word in enumerate(words[:3]) if ":" in word.text),
        None,
    )
    if colon_index is None:
        return None

    label_words = words[: colon_index + 1]
    label_text = "".join(word.text.split(":", maxsplit=1)[0] for word in label_words)
    compact_label = re.sub(r"[^A-Z]", "", label_text.upper())
    labels = {
        "D": "description",
        "DESCRIPTION": "description",
        "FS": "filing_status",
        "FILINGSTATUS": "filing_status",
        "L": "location",
        "LOCATION": "location",
        "C": "comments",
        "COMMENTS": "comments",
    }
    label_name = labels.get(compact_label)
    if label_name is None:
        return None

    colon_word = words[colon_index]
    suffix = colon_word.text.split(":", maxsplit=1)[1]
    value_words = ([suffix] if suffix else []) + [
        word.text for word in line.words if word.x0 > colon_word.x1
    ]
    return label_name, _join_parts(value_words)


def _column_text(line: _Line, left: float, right: float) -> str:
    return _join_parts(
        word.text for word in line.words if left <= word.x0 < right
    )


def _parse_date(value: str, field_name: str) -> datetime:
    try:
        return datetime.strptime(value, "%m/%d/%Y")
    except ValueError as error:
        raise HouseReportError(
            f"a House PTR has an invalid {field_name} date: {value!r}"
        ) from error


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", "").split())


def _join_parts(parts: Iterable[str]) -> str:
    return " ".join(part.strip() for part in parts if part.strip())
