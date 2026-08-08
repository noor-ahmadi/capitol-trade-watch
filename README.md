# Capitol Trade Watch

[![CI](https://github.com/noor-ahmadi/capitol-trade-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/noor-ahmadi/capitol-trade-watch/actions/workflows/ci.yml)

> [!IMPORTANT]
> **Scaffolding only—trade alerts are not active.**

Capitol Trade Watch is being built to detect newly published congressional
Periodic Transaction Reports (PTRs) and turn them into clear GitHub
notifications. The first tracked filer will be Representative Nancy Pelosi,
including every household transaction disclosed in her PTR filings.

## Current status

This repository currently contains the Python project foundation, tracked-person
configuration, immutable disclosure models, and validation tests. It does not
download House records, parse disclosure PDFs, create GitHub issues, or run on a
schedule.

Development is intentionally split into small, independently verified commits.
Upcoming milestones will add configuration and domain models, official House
index ingestion, idempotent state, PDF parsing, alert rendering, and finally
GitHub issue notifications.

## Local setup

- Python 3.12 or newer

The repository's `.python-version` selects the supported Python line for tools
that recognize it. Install Python 3.12 first if the `python` command is not
available on your machine.

From PowerShell in the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --editable ".[test]"
python -m pytest
python -m capitol_trade_watch validate-config
```

The test dependency graph used by CI is fully pinned in
`requirements-test.txt`.

## Commands

Show the package version:

```powershell
python -m capitol_trade_watch --version
```

Validate the tracked-person configuration:

```powershell
python -m capitol_trade_watch validate-config
```

The initial configuration tracks Nancy Pelosi's House filings. Additional
House members can be added later through `config/tracked_people.toml`; Senate
filings are outside the first release.

## Project layout

- `config/` contains the public tracked-person configuration.
- `src/capitol_trade_watch/` contains the application package.
- `tests/` contains fast, offline unit tests.
- `.github/workflows/ci.yml` runs tests only; it has no schedule or write access.

## Important limitations

Congressional transaction reports are delayed disclosures, not real-time trade
feeds. They commonly report value ranges instead of exact amounts. This project
will preserve what the official filing says without inferring who executed a
trade when ownership is not specified.

This software is for informational purposes only and is not financial advice.
Users are responsible for complying with the
[House financial disclosure data-use notice](https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch).

## License

The software is available under the [MIT License](LICENSE). Source disclosure
documents remain subject to the terms of their official publishers.
