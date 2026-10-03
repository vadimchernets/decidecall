# Changelog

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
