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
PDFs, format an alert, and publish it as one assigned GitHub issue. There is a
manual switchboard for the whole loop now; the timer is still off.

Right now the repo has the tracked-person config, disclosure models, config
validation, the House index reader, a small JSON ledger, the report parser, the
alert formatter, and an idempotent issue publisher. It only needs the built-in
`GITHUB_TOKEN`; there is no personal API token to configure. Nothing runs on a
timer yet.

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

## trying the switchboard

On GitHub, open **Actions → Manual monitor → Run workflow**. There are four
choices:

- `preview` reads and formats anything new, but saves and sends nothing.
- `seed` quietly updates the filing ledger on `main`.
- `check` looks for real new filings, sends their issues, then saves the
  ledger. It refuses to start until `seed` has run.
- `test-alert` makes an obviously fake issue so I can check email or phone
  delivery.

None of these run by themselves. The intended order is `seed`, `test-alert`,
then `check`. Scheduling comes later, after the quiet seed and notification
test have both been checked.

`check` saves the ledger as soon as all new filing alerts succeed. With nothing
new, it saves a heartbeat only once 24 hours have passed since the last save.
The workflow commits only when that file changes, so quiet checks do not each
add a commit. `status` shows the last saved check; individual runs still report
their result in the Actions log. Source timestamps are saved with the ledger,
so an index that changes between heartbeats may be downloaded again.

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
