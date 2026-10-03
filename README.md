# decidecall

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23116720.svg)](https://doi.org/10.5281/zenodo.23116720)

Pay a model only for the decisions that need one. A [Claude Code](https://claude.com/claude-code) plugin of
Poly A1 for a company's repeated small decisions — which team gets this message, approve or send back, urgent
or not. Repository: [github.com/vadimchernets/decidecall](https://github.com/vadimchernets/decidecall).

**decidecall decides and counts; it buys nothing.** A paid model is reached through a program the company has
installed and signed in itself, and the person
responsible for the company's accounts decides whether a hosted decision model is worth buying — on the
numbers `bench` gives.

## The chain

```
cache -> rules -> local model -> cheap cloud model -> strong model -> a person
```

Each link answers with a decision and a confidence and the chain stops at the first link sure enough:

- **act** at confidence 0.95 or more (the gate "confidence > .95" of the JEV notes);
- **verify** from 0.70: the next link is asked, and two links that agree settle it;
- otherwise **a person** decides, with the best guess so far — and the person's answer goes into the cache, so
  the same question never reaches a model again.

The cache knows exact repeats (case, spacing and punctuation do not matter) and similar messages by word overlap
— retrieval without a vector database; a similar message is never acted on alone. Rules are keyword rules from
the task file; rules that point at two answers abstain.

| Command | What the company gets |
|---|---|
| `decide` | One decision through the chain, with the link that took it, its confidence, its cost and an id. |
| `label` | A person's answer to a decision that reached them; it goes into the cache. |
| `promote` | Decisions the models kept making the same way (2+ times, all agreeing, mean confidence at the act gate) become cache entries with `--apply`; words that only ever came with one answer are offered as rules. |
| `bench` | The chain measured on labelled examples: accuracy of the automatic decisions, what each link decided, calibration (expected calibration error), the price of one decision against sending everything to one model, the wrong answers, and "what did not work" in plain words. |
| `route` | Which adapter each link would use, and why the company policy refuses the others. |
| `candidates` | Programs that can stand behind a link, with licence, page and the day they were read. |
| `schema` | The output schema every model's answer must match. |

## Model links are adapters

decidecall runs no model and opens no connection itself. A link is a program the company installs, named in
`decidecall-adapters.json` (example: `data/examples/decidecall-adapters.example.json`), run with no shell,
the question on its standard input:

- `systemone` — the Jev-compatible `POST /v1/systemone` shape: a strands-decider server, OpenJev, JevK5, or
  a hosted Jev through the company's own `curl`;
- `strands-cli` — the line `strands-decider ask` prints (`choice_0 -> billing (confidence 0.768)`);
- `decision-json` — a JSON answer: `ollama run … --format json`, or `claude -p --output-format json
  --json-schema <schema>` (its `structured_output`, and its reported cost).

Every answer is checked against the task's output schema (an option from the list, a confidence from 0 to 1, no
other fields); an answer that fails it is a note, not a decision. Candidates, all read on 2026-10-02
(`data/candidates.json`): strands-decider, OpenJev, JevK5, setfit, Outlines, XGrammar, BAML, llm-typesafe
(Apache-2.0), agent-model-router (MIT), OpenSmartRoute (Apache-2.0, a router to compare with the chain itself),
JevBench (MIT), ollama (MIT); TypeSafe's hosted Jev — $0.042 per million input tokens, output free — from a
secondary source.

## The company policy file

`company-ai-policy.json` is shared by the company plugins (firmcall writes it; billcall, gatecall, routecall
and decidecall read it), looked for in the same order: the managed copy an administrator installed
(`/Library/Application Support/ClaudeCode/`, `/etc/claude-code/`, `C:\Program Files\ClaudeCode\`), then
`$COMPANY_AI_POLICY`, then the project folder and its `.claude/`, then `~/.claude/`. decidecall reads:

- `--color red` (or `default_color`) — red data goes only to cache, rules and adapters with `"location": "local"`,
  and only its SHA-256 fingerprint is kept, never its text;
- `privacy_level` — `local-only` keeps every decision on the computer, `zdr` admits only cloud adapters marked
  `"zdr": true`;
- `allowed_providers`, `deny_providers`;
- `max_usd_per_day` — a paid link that would pass today's spend is skipped; free local links go on;
- `language` — the language of the bench report.

Among the adapters a link may use, the cheapest goes first.

## Measured on 2026-10-02

`data/bench/support-triage.jsonl`: 65 customer messages for four teams (billing, technical, account, sales),
with repeats as real support has them. Full results: `data/bench/results-2026-10-02.json`.

| Chain | Decided without a person | Right | To a person | Cost of 65 decisions |
|---|---|---|---|---|
| cache + rules | 36 (cache 4, rules 32) | 36 of 36 | 29 | $0 |
| cache + rules + Claude Haiku 4.5 via `claude -p` | 59 (cache 1, rules 32, Haiku 26) | 57 of 59 (96.6%) | 6 | $0.167 at list price |
| reference: every message straight to Haiku 4.5 over the API | — | not run | — | $0.018 at list price |

What did not work, as bench said it:

- **Haiku was wrong twice at confidence 0.98** — "I get logged out every five minutes" went to technical, not
  account, and its repeat followed it; `promote` asks for agreement before caching a model's answer, so such a
  repeat goes back to a link or a person. Its calibration was otherwise close: mean confidence 0.970,
  right 0.966 (expected calibration error 0.004).
- **The chain through `claude -p` cost nine times the reference**: each call carries Claude Code's own
  instructions (about 2,600 input tokens with `--tools "" --strict-mcp-config` and a one-line system prompt;
  about 24,000 and $0.049 a call without them). On a Claude subscription this is the plan's usage, not money;
  over the API, a direct call or a decision model is the cheaper link.
- **The local link was not in this run**: the measuring computer had no ollama, strands-decider or OpenJev. Its
  adapter and tests are in place; `bench --tiers cache,rules,local` measures it wherever one is installed.
- The rules and the examples come from one author, so 32 of 32 is the ceiling on this set; `bench` on the
  company's own past decisions gives the company's figure.

## Installing

From the Poly A1 catalogue, by its link — no git and no account needed:

```
/plugin marketplace add https://raw.githubusercontent.com/vadimchernets/poly-a1-plugins/main/.claude-plugin/marketplace.json
/plugin install decidecall@poly-a1
```

Then say what you want decided: "sort our support messages by team", "is Strands Decider worth it for us".

## What it needs

Python 3.8+ and its standard library — no dependencies, no network module. Skills run the script through
`hooks/python.sh` (PowerShell: `hooks/python.ps1`), which finds a real Python and never starts the Apple or
Microsoft Store stub.

## Checks

`python3 -m pytest -q` — the chain stops at the first sure link; two agreeing links settle an unsure answer; a
stand-in Jev (`tests/fixtures/stub_jev.py`) flips a decision that would otherwise go to a person; an answer
outside the schema is refused; a person's answer is never asked twice; red data never reaches a cloud adapter
and its text is never kept; `local-only`, `zdr`, allowed and denied providers; the daily budget stops paid links
and not free ones; promote and its rule ideas; bench, its calibration and its negative results; no network
module. `python3 tools/mutate_code.py` breaks those rules one by one in a copy and expects the tests to go red;
its last line counts the mutations that misbehaved.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
