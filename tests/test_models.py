from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from capitol_trade_watch.models import Filing, TrackedPerson, Transaction


@pytest.fixture
def pelosi() -> TrackedPerson:
    return TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )


def test_models_preserve_disclosure_values(pelosi: TrackedPerson) -> None:
    transaction = Transaction(
        owner_code="SP",
        asset="Broadcom Inc. - Common Stock (AVGO) [ST]",
        action="P",
        transaction_date=date(2025, 6, 20),
        notification_date=date(2025, 6, 20),
        amount_range="$1,000,001 - $5,000,000",
        capital_gains=None,
        description="Exercised 200 call options.",
    )
    filing = Filing(
        document_id="20030630",
        filer=pelosi,
        filing_date=date(2025, 7, 9),
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/"
            "ptr-pdfs/2025/20030630.pdf"
        ),
        transactions=(transaction,),
    )

    assert filing.transactions == (transaction,)
    assert transaction.owner_code == "SP"
    assert transaction.owner_label == "spouse"
    assert transaction.amount_range == "$1,000,001 - $5,000,000"


def test_blank_owner_is_not_inferred() -> None:
    transaction = Transaction(
        owner_code="",
        asset="Example Corporation",
        action="S",
        transaction_date=date(2026, 1, 1),
        notification_date=None,
        amount_range="$1,001 - $15,000",
        capital_gains=None,
        description="",
    )

    assert transaction.owner_label == "not specified"


def test_models_are_immutable(pelosi: TrackedPerson) -> None:
    with pytest.raises(FrozenInstanceError):
        pelosi.state = "NY"  # type: ignore[misc]
