# Changelog

## 0.1.4 — 2026-10-03

- The meaning cache: with an embedder adapter (kind `embed`: ollama `/api/embed`, llama.cpp or any
  `/v1/embeddings` server on this computer) a question asked in other words finds the cached answer by cosine
  similarity of local embeddings (0.90 by default, per task and per entry); without one, word overlap as before.
  Vectors are kept with their model and dimensions and recomputed for a new model. A similar match never acts
  alone, at any act threshold; an entry a person answered differently is never offered again.
- Strict decision shape: `{"decision", "confidence", "reason"}` with `none_of_these`, which sends the decision to a
  person. New adapter kinds `ollama` (schema as `format`) and `openai-chat` (`response_format` `json_schema`,
  strict) decode only that shape.
- Agreement only across sources: adapters carry a `family`; one model repeating itself neither verifies nor gets
  promoted into the cache.
- `systemone` answers are taken at the probability of the chosen option.
- Keys from the keychain: `keychain` on an adapter fills its environment; the example Jev adapter runs curl with
  `--variable`/`--expand-header`, no shell, no key in the file.
- `data/candidates.json`: Jev's price from TypeSafe ($42 per billion input tokens, output free), early access,
  no ZDR; JevBench figures name their version.

## 0.1.3 — 2026-10-03

- Wording: no disclaimers. The bench results in README stay as measured, stated as data with their conditions;
  SECURITY.md and the skill speak plainly of the company's own adapters.
- Tests: a tone check reddens on disclaimers, excuses and apologies in what people read (five languages).

## 0.1.2 — 2026-10-03

- README: the Zenodo DOI badge (the concept DOI always points to the latest version).

## 0.1.1 — 2026-10-03

- Zenodo DOI: the repository is archived on Zenodo; this release is the first one it records (same content as 0.1.0).

## 0.1.0 — 2026-10-02

- First release: `decide`, `label`, `promote`, `bench`, `route`, `candidates` and `schema`, one skill.
- The chain cache -> rules -> local -> cheap -> strong -> person, with the act gate 0.95 and the verify band
  from 0.70; a similar cached message never acts alone.
- Adapters of three kinds: `systemone` (strands-decider server, OpenJev, JevK5, a hosted Jev), `strands-cli`
  (the `strands-decider ask` line) and `decision-json` (ollama, `claude -p --json-schema`), every answer checked
  against the task's output schema.
- The company policy file: red data local, `privacy_level` local-only or zdr, allowed and denied providers,
  the cheapest admitted adapter first, `max_usd_per_day`.
- `data/tasks/support-triage.json` and a 65-example bench set `data/bench/support-triage.jsonl`;
  `data/candidates.json` with every candidate program's licence, page and day read.
