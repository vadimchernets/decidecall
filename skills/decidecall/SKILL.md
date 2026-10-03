---
name: decidecall
description: Take a company's repeated small decisions cheaply - which team gets a message, approve or send back, urgent or not - through a chain that stops at the first link sure enough - cache, rules, a local model, a cheap cloud model, a strong model, a person. Use it when someone from a company wants routine decisions automated without paying a big model for each one, wants a decision model such as Strands Decider, OpenJev, JevK5 or Jev tried, wants to know whether such a model is worth paying for (bench), or wants decisions on sensitive data kept on their own computer. It decides and counts; it buys nothing.
argument-hint: "[decide | label | promote | bench | route | candidates | schema] [what to decide]"
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py *) PowerShell(${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1 decidecall say skills/decidecall/scripts/decidecall.py *) Read Write
---

# decidecall: pay a model only for the decisions that need one

## Running decidecall's scripts (Mac, Linux, Windows)

Every script command on this page is written for the **Bash** tool and starts with
`sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/…`. If your shell tool is
**PowerShell** (Windows without Git Bash), only the start changes: write the launcher's path bare, with no quotes
and no `&` — `${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1 decidecall say skills/decidecall/scripts/…` — and keep the
rest, on one line; that is the form this skill's permission covers. Only if that path has a space in it, write
`& "${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1" …` instead (the person is then asked once). Never call `python3`,
`python` or `py` yourself: the launcher finds a real Python 3.8+ and never starts the Microsoft Store or Apple
stub. If it answers with one line saying decidecall "is paused" because this computer has no working Python 3 yet,
tell the person that in one plain line and stop.

The user said: $ARGUMENTS

Answer in the person's language. Say every figure plainly: how many decisions, how many right, what one decision
costs. **decidecall decides and counts; it buys nothing** and never asks for a card or a key — a paid model is
used only through a program the company has already installed and signed in itself.

## What it is, in two sentences for the person

Most of a company's small decisions repeat. decidecall answers each one with the cheapest thing that is sure
enough — a remembered answer, a simple rule, a model on this computer — and only the rest goes to a paid model
or to a person; a person's answer is remembered, so the same question never costs twice.

## 1. Describe the decision (a task)

A task is one JSON file: the question, the options with one line each, keyword rules, the thresholds. Read the
shipped example `${CLAUDE_PLUGIN_ROOT}/data/tasks/support-triage.json` first, then write the company's own with
them as `decidecall-tasks/<id>.json` in their folder. Ask, one at a time: what is decided, what the possible
answers are, which words make the answer certain ("invoice" means billing), and how sure it must be before it
acts (0.95 unless they say otherwise).

## 2. Say which models may help (adapters)

Show the candidates with their licences and where they were read:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py candidates
```

Copy `${CLAUDE_PLUGIN_ROOT}/data/examples/decidecall-adapters.example.json` to `decidecall-adapters.json` in
their folder and keep only what is installed on this computer (check with `command -v strands-decider ollama
claude`). Each adapter says its link (`local`, `cheap`, `strong`), its kind, its command, `location` (`local` or
`cloud`), `provider`, `zdr` and its price per million tokens. Then show which adapter each link would use and why
the others are refused by the company policy:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py route --task support-triage
```

## 3. Measure before anything is paid for (bench)

Labelled examples - a JSONL file, one `{"input": "...", "label": "..."}` per line, 50 or more from the
company's own past decisions - tell whether a model is worth its price. Run cache and rules alone first, then
with each link:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py bench --task support-triage --tiers cache,rules --report .
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py bench --task support-triage --tiers cache,rules,local,cheap --report .
```

Tell them: how many were decided without a person and how many of those were right, what each link decided, the
price of one decision against sending everything to one model, and the part "what did not work" word for word.
A hosted decision model is worth paying for only when bench shows it adds right decisions that cache, rules and
a local model did not.

## 4. Decide, and let a person answer the rest

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py decide --task support-triage --input "…" --color green
```

`--color red` keeps the decision on this computer (only cache, rules and local adapters). When the answer is "a
person decides", ask the person, then save their answer — it goes into the cache:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py label --id <id> --answer <option>
```

## 5. Make it cheaper every week (promote)

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" decidecall say skills/decidecall/scripts/decidecall.py promote --task support-triage
```

It lists the decisions the models kept making the same way and the words that only ever came with one answer.
With the person's yes, run it again with `--apply` (cache entries) and add the rule ideas they agree with to the
task file.

## The company policy file

`company-ai-policy.json` (the same file billcall, gatecall, routecall and firmcall use): `privacy_level`
(`local-only` keeps every decision on this computer, `zdr` admits only zero-data-retention cloud adapters),
`allowed_providers`, `deny_providers`, `max_usd_per_day` (paid links stop once today's spend would pass it;
free local ones go on), `default_color`, `language`.
