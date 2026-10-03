# Security

## What decidecall touches

- **The text of one decision**, given on the command line or on standard input, and the task file that
  says the question and the options.
- **Its own state** in `~/.decidecall/` (or `$DECIDECALL_HOME`, or `--state-dir`): `cache/<task>.json` and
  `journal.jsonl`. For red data only a SHA-256 fingerprint of the text is kept, never the text.
- **The programs named in `decidecall-adapters.json`**, run with no shell, with the decision's text on their
  standard input. The company chooses these programs, and its policy decides which of them receives which
  data.
- **The company policy file** `company-ai-policy.json`, read-only.

## What it never does

- Never buys, subscribes, opens a checkout or asks for a card, a password or an API key. Keys stay where the
  company's own programs keep them.
- Never goes to the network itself: Python's standard library only, and no network module is imported (a test
  fails the build if one appears).
- Never sends red data to a program marked `"location": "cloud"`, and never runs a paid program once today's
  spend would pass `max_usd_per_day`.

## Reporting

Open an issue on github.com/vadimchernets/decidecall, or write to the author through the Poly A1 support address.
