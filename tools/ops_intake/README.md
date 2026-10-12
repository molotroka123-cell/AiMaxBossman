# ops_intake - Bossman Operations: request intake pilot

The smallest real slice of the "Bossman Operations" service line: take a pile of incoming requests, turn it into one
clean table with duplicates linked and doubtful rows queued for a human, and produce a daily report plus DRAFT
messages. Stdlib only (openpyxl is optional, for `table.xlsx`). No network, no model call, no sending.

```
cd tools
python -m ops_intake.cli run --input <export> --out <dir> [--window-min 1440] [--staff <from_id>] [--lang ru|en]
```

Input: Telegram Desktop `result.json` (single chat) or `messages.html` (parsed with `exchange_volume.parse`, so
senders are the same anonymised `S1, S2...` labels), or a leads file: CSV (`,` `;` or tab), JSONL, or a JSON array
(RU/EN column names such as `Имя, Телефон, Email, Услуга, Бюджет, Валюта, Срок, Сообщение, Дата, Статус`).

## Service boundary

| The service DOES | The service does NOT |
|---|---|
| read one export / leads file locally | connect to a CRM, helpdesk, Sheets, e-mail or Telegram API |
| extract name, phone, e-mail, @handle, service, amount + currency, deadline, language | send, post or schedule anything - notifications are text files |
| link duplicates (keep first, link the rest, never delete) | call a model or the internet (a local-LLM fallback is NOT implemented) |
| write `table.csv` (+ `table.xlsx`), `duplicates.csv`, `needs_review.csv`, `skipped.csv` | convert or sum currencies |
| write `daily_report.md` and `notifications_draft.md` (drafts for a human) | decide anything the data does not say: it flags |
| write `run.json` (input sha256, timings, counts, id lists, output hashes) | stitch a request split across several messages (they stay separate rows, flagged) |

## Outputs (one run = one directory)

| file | content |
|---|---|
| `table.csv` / `table.xlsx` | one row per UNIQUE request; `needs_review` column; XLSX sheets `requests`, `duplicates`, `needs_review`, `skipped`. Without openpyxl no XLSX is written and `run.json` says so (the CSV is the table) |
| `duplicates.csv` | every linked duplicate: kept row, rule, confidence, minutes from the first request, what it added |
| `needs_review.csv` | the rows with `needs_review = yes` and the reasons |
| `skipped.csv` | every item that is not a request: `non_request`, `staff_message`, `media_only`, `empty_text`, `unparsed` |
| `daily_report.md` | new requests, duplicates merged, needs-review count, top services, budgets per currency, SLA (oldest unanswered), per-day table, parse quality; appendix A lists the source ids behind every number |
| `notifications_draft.md` | a team digest and one acknowledgement draft per unique request, each marked `DRAFT, NOT SENT`; asks a clarifying question for every ambiguity instead of guessing |
| `run.json` | schema, input sha256, params, counts, reconciliation, id lists, timings, warnings, output hashes |

CSV files are UTF-8 with BOM (Excel shows Cyrillic). Cells that a spreadsheet could run as a formula (leading `=`,
TAB, CR, `@` that is not a plain @handle, `+`/`-` that is not a number) get a leading `'` in CSV and are explicit
strings in XLSX.

## Honesty rules (all enforced by tests)

* A field value is only copied or normalised from the source text. A phone/amount/deadline/e-mail that is ambiguous
  is left EMPTY, the raw text is kept in `phone_raw` / `amount_raw` / `deadline_raw` / `email_raw`, and the row is
  flagged. Examples: `777 123 456` (no country), `5.000 Kč` and `2,500 EUR` (thousands or decimals?), `04/05`
  (dd/mm or mm/dd?), two different phones or amounts in one message, `ivan@mail`.
* A name is taken only after an explicit marker (`меня зовут`, `my name is`, `I'm`, `Name:`, `с уважением,`).
* A service comes from a keyword taxonomy (`--services file.json` replaces it). Two different services in one
  message = ambiguous, not "first wins".
* Relative deadlines are resolved only against the message's own date and carry `deadline_basis`
  (`explicit_date`, `year_inferred_from_message_date`, `relative_to_message_date`, ...). `срочно/ASAP` is a flag.
* Amounts of different currencies are reported per currency and never summed or converted; duplicates are not
  counted twice; ambiguous amounts are listed separately.
* Nothing disappears: `input_items = unparsed + skipped + requests_found` and
  `requests_found = unique_requests + duplicates_merged`, both checked in `run.json`. Every input id is listed
  exactly once.

## Duplicate rules

The kept row is the chronologically first request; the window (`--window-min`, default 1440, inclusive) runs from it.

| rule | evidence | confidence |
|---|---|---|
| `A_phone` / `A_email` / `A_handle` | same normalised phone (`+420 777 123 456` = `00420777123456`, `8 (999) 123-45-67` = `+7 999 123 45 67`) / e-mail (case-folded; gmail dots and `+tag` ignored) / @handle; services equal or one side unknown | high |
| `C_same_text` | same sender, near-identical text | high (identical) / medium |
| `C_same_sender_service` | same sender, same service | medium |
| `B_name_service` | same multi-word name (transliteration tolerant), same service, senders not different | medium |

NOT merged: same person with a different service, two different people with the same service, anything outside the
window, anything with a conflicting known phone / amount / currency / deadline. Weaker matches (single-word name,
different senders, conflicts) are flagged `possible_duplicate` on BOTH rows and go to review. `--min-confidence high`
merges only the high rules. Only EMPTY fields of the kept row are filled from a duplicate, and each fill is recorded
in `filled_from`; an ambiguous value in the kept row is never replaced.

## Review policy

`needs_review = yes` when a row has an ambiguity flag (`phone_ambiguous`, `phone_multiple`, `email_invalid`,
`amount_ambiguous`, `amount_multiple`, `amount_currency_missing`, `service_ambiguous`, `deadline_ambiguous`,
`possible_duplicate`, `empty_request`) or lacks a field in `--require` (default `service,contact`; choices: name,
phone, email, service, amount, deadline, contact). A chat message counts as contactable through the chat itself.
Absences that are not required (`phone_missing`, `amount_missing`, ...) stay visible in `flags` only.

## Answered / SLA

Telegram: a request is answered when another sender (or a `--staff` sender) replied to it with a reply link. If the
export has no reply links the status is `unknown`, never `no`. Leads files use an `answered`/`status` column.
Replies sent without Telegram's reply feature are invisible: "unanswered" is an upper bound. Pass `--staff <from_id>`
(repeatable) so staff messages are not read as requests; without it the report warns.

## Acceptance criteria (pilot with a customer)

Measured on the synthetic fixtures (`tests/ops_intake`, 81-message Telegram chat): all MET. Targets 5-7 are
NOT yet measured on any real customer data.

1. One command, exit 0, all files written; `--no-xlsx` or missing openpyxl still produces CSV and says why.
2. Reconciliation true; every input id appears exactly once; the report's counts equal the id lists' lengths.
3. No invented values: every phone / e-mail / @handle / amount in the table is traceable to the source text.
4. Negative controls hold: different people + same service, same person + different service, outside window.
5. TARGET: on a 50-row random sample of a real export, field accuracy >= 95 % on rows NOT sent to review, and every
   wrong row is either flagged or counted as a failure.
6. TARGET: zero false merges on the operator-labelled sample (a missed duplicate is acceptable, a hidden request is not).
7. TARGET: `needs_review` share reported per run; the operator accepts the queue size (synthetic demo: 16 of 57).
8. Drafts: every draft says `DRAFT, NOT SENT`; the package imports no network, mail or model library (tested).

## Known limits

* Verified on SYNTHETIC data only (generated, no real client data). Real chats will be messier; expect the review
  share and the keyword taxonomy to need tuning on the first real export.
* Telegram display names are not read (the shared parser anonymises senders); a name must be written in the text.
* Keyword taxonomy (RU/EN/CS words): no negation or context ("раньше делали сайт" still hits `website`).
* A bare number next to an amount with currency ("5000 или 6000 Kč") is not seen as a second amount.
* `$` is read as USD; `к`/`k`/`тыс` multiply by 1000; `млн` by 10^6.
* Timestamps without a zone in CSV/JSON leads are read as UTC; items without a timestamp are merged only on
  phone/e-mail/@handle, with `window_verified = no`.
* Phone country table covers the common codes only; unknown codes are accepted at 8-15 digits.
* One chat per run (a whole-account export is refused); no incremental state between runs.

## Tests

```
python -m pytest tests/ops_intake -q -p no:cacheprovider      # from the repository root
```
