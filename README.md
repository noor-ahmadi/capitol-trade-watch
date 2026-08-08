# Capitol Trade Watch

> [!IMPORTANT]
> **Scaffolding only—trade alerts are not active.**

Capitol Trade Watch is being built to detect newly published congressional
Periodic Transaction Reports (PTRs) and turn them into clear GitHub
notifications. The first tracked filer will be Representative Nancy Pelosi,
including every household transaction disclosed in her PTR filings.

## Current status

This repository currently contains only the Python project foundation. It does
not download House records, parse disclosure PDFs, create GitHub issues, or run
on a schedule.

Development is intentionally split into small, independently verified commits.
Upcoming milestones will add configuration and domain models, official House
index ingestion, idempotent state, PDF parsing, alert rendering, and finally
GitHub issue notifications.

## Requirements

- Python 3.12 or newer

## Version

From the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m capitol_trade_watch --version
```

## Important limitations

Congressional transaction reports are delayed disclosures, not real-time trade
feeds. They commonly report value ranges instead of exact amounts. This project
will preserve what the official filing says without inferring who executed a
trade when ownership is not specified.

This software is for informational purposes only and is not financial advice.
Users are responsible for complying with the House financial disclosure data
use notice.

## License

The software is available under the [MIT License](LICENSE). Source disclosure
documents remain subject to the terms of their official publishers.
