# capitol trade watch

[![checks](https://github.com/noor-ahmadi/capitol-trade-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/noor-ahmadi/capitol-trade-watch/actions/workflows/ci.yml)

*a small alarm bell for slow-moving public paperwork*

I kept seeing paid trackers for congressional trades and wondered how much of
the useful part I could build myself. The filings are public; I mostly want a
clean heads-up when a new one appears.

The rough shape is:

```text
official House filing  ->  small Python job  ->  a ping in my GitHub inbox
```

Nancy Pelosi is the first name on the watch list. The code is meant to make
adding other House members boring later on.

**status:** it can find matching PTRs, remember which ones it has seen, read the
PDFs, and format the result as a Markdown alert. Nothing posts those alerts yet.

Right now the repo has the tracked-person config, disclosure models, config
validation, the House index reader, a small JSON ledger, the report parser, and
the alert formatter. It does not send notifications yet. I am building those
pieces in small passes so the history stays easy to follow.

## running what exists

You need Python 3.12. From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --editable ".[test]"
python -m pytest
python -m capitol_trade_watch validate-config
```

The watch list lives in `config/tracked_people.toml`.

To mark everything already public as seen:

```powershell
python -m capitol_trade_watch seed
python -m capitol_trade_watch status
```

That checks the current and previous House indexes, then updates
`data/state.json`. It does not send anything. This is the quiet first run so old
filings do not turn into new alerts later. `status` only reads that file.

## a few rules for the project

- The official filing wins. The tracker should repeat it, not embellish it.
- `SP` means spouse and `JT` means jointly held. A blank owner stays
  "not specified" instead of quietly becoming "Nancy."
- A disclosed dollar range stays a range.
- This will notify, not trade. There is no brokerage connection hiding on the
  roadmap.

Congressional disclosures are late by design, sometimes weeks after the trade.
This is a paperwork watcher, not a real-time market feed or investment advice.

Code is [MIT licensed](LICENSE). House documents and disclosure data remain
subject to the official
[data-use notice](https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch).
