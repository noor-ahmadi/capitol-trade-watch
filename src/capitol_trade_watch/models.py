"""Immutable domain models for congressional trade disclosures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

_OWNER_LABELS = {
    "SP": "spouse",
    "DC": "dependent child",
    "JT": "jointly held",
}


@dataclass(frozen=True, slots=True)
class TrackedPerson:
    """A filer selected for monitoring."""

    id: str
    chamber: str
    first_name: str
    last_name: str
    state: str

    @property
    def display_name(self) -> str:
        """Return the filer's human-readable name."""
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True, slots=True)
class Transaction:
    """One transaction exactly as represented by a disclosure filing."""

    owner_code: str
    asset: str
    action: str
    transaction_date: date
    notification_date: date | None
    amount_range: str
    capital_gains: str | None
    description: str

    @property
    def owner_label(self) -> str:
        """Explain an ownership code without inferring a missing value."""
        normalized_code = self.owner_code.strip().upper()
        if not normalized_code:
            return "not specified"
        return _OWNER_LABELS.get(normalized_code, f"unrecognized ({normalized_code})")


@dataclass(frozen=True, slots=True)
class Filing:
    """A House filing and any transactions parsed from it."""

    document_id: str
    filer: TrackedPerson
    filing_date: date
    source_url: str
    transactions: tuple[Transaction, ...] = ()
