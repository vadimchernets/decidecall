#!/usr/bin/env python3
"""Break decidecall's own rules on purpose, in a copy, and watch the tests redden.

Each mutation copies the plugin folder to a temporary place, changes one exact text in one file of the
copy (the text must be there exactly once, or the mutation itself is reported as broken), runs the named
tests in the copy, and expects them red. The control mutation changes a comment and expects green. After
every run the sha256 of every original file is compared with the one taken at the start: the plugin itself
is never touched. The last line counts the mutations that misbehaved; anything but 0 is a failure.

  python3 tools/mutate_code.py            (about a minute; needs pytest, like the tests themselves)

"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = "skills/decidecall/scripts/decidecall.py"
TESTS = "tests/test_decidecall.py"
SKIP = (".git", "__pycache__", ".pytest_cache", "dist")

# (what is broken, file, exact text, replacement, tests to run, expected outcome)
MUTATIONS = (
    ("control: a comment reworded", SCRIPT,
     "# one answer per link: the cheapest that answered", "# one answer for each link: the cheapest that answered",
     TESTS + "::Chain", "green"),
    ("the stand-in Jev's answer is ignored, so it can no longer flip a decision", SCRIPT,
     'decision = ans.get("choice")', "decision = None",
     TESTS + "::Chain::test_the_stand_in_jev_flips_a_decision_that_would_go_to_a_person", "red"),
    ("any confidence acts: the 0.95 gate is gone", SCRIPT,
     'if res["confidence"] >= act and not res.get("similar"):', "if True:",
     TESTS + "::Chain", "red"),
    ("an answer outside the options passes the output schema", SCRIPT,
     "if decision not in options and decision != NONE_OF_THESE:", "if False:",
     TESTS + "::Chain", "red"),
    ("a similar cached message acts on its own", SCRIPT,
     "conf = min(score, SIMILAR_CAP)", "conf = max(score, 1.0)",
     TESTS + "::Cache", "red"),
    ("red data reaches a cloud adapter", SCRIPT,
     'if color == "red":', "if False:",
     TESTS + "::Policy", "red"),
    ("the company policy's denied vendor is used", SCRIPT,
     'if provider in policy_list(policy, "deny_providers"):', "if False:",
     TESTS + "::Policy", "red"),
    ("max_usd_per_day is passed: paid links run past the budget", SCRIPT,
     "if budget is not None and est > 0 and spent + cost + est > float(budget):", "if False:",
     TESTS + "::Budget", "red"),
    ("a person's answer in bench is not remembered, repeats go to a person again", SCRIPT,
     'remember(state, task["id"], row["input"], row["label"], 1.0, "a person\'s answer", color)', "pass",
     TESTS + "::Bench", "red"),
    ("a match by meaning acts on its own under a low act gate", SCRIPT,
     'if res["confidence"] >= act and not res.get("similar"):', 'if res["confidence"] >= act:',
     TESTS + "::MeaningCache", "red"),
    ("a refuted cache entry is offered again", SCRIPT,
     'if e.get("text") and not e.get("refuted")]', 'if e.get("text")]',
     TESTS + "::MeaningCache", "red"),
    ("one model repeating itself counts as agreement", SCRIPT,
     "if len(sources) >= min_count and", "if len(g[\"votes\"]) >= min_count and",
     TESTS + "::Promote", "red"),
    ("the ollama runner gets no schema to decode against", SCRIPT,
     '"prompt": prompt, "format": schema,', '"prompt": prompt, "format": "json",',
     TESTS + "::StrictShape", "red"),
    ("none_of_these is read as an ordinary answer", SCRIPT,
     'if res.get("none_of_these") and res["confidence"] >= verify:', "if False:",
     TESTS + "::StrictShape", "red"),
    ("red text is kept in the cache", SCRIPT,
     '    if color != "red":\n        entry["text"] = text', '    if True:\n        entry["text"] = text',
     TESTS + "::Cache", "red"),
)


def digest(root):
    """sha256 of every file under root (junk folders left out), keyed by relative path."""
    out = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def run_one(tmp, n, mutation):
    """-> ("red" | "green" | "error", detail)."""
    _name, rel, old, new, tests, _expect = mutation
    copy = os.path.join(tmp, "m%02d" % n)
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(*SKIP))
    target = os.path.join(copy, rel)
    with open(target, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(old) != 1:
        return "error", "%r is in %s %d times, not once" % (old, rel, text.count(old))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text.replace(old, new, 1))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests],
                              cwd=copy, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "error", repr(exc)
    last = (done.stdout.strip().splitlines() or [""])[-1]
    if done.returncode == 0:
        return "green", last
    if done.returncode == 1:
        return "red", last
    return "error", "pytest exit %d: %s" % (done.returncode, (done.stdout + done.stderr).strip()[-300:])


def main():
    before = digest(ROOT)
    bad = 0
    tmp = tempfile.mkdtemp(prefix="decidecall-mutate-")
    try:
        for n, mutation in enumerate(MUTATIONS):
            name, expect = mutation[0], mutation[5]
            outcome, detail = run_one(tmp, n, mutation)
            if outcome == expect:
                print("ok   %s: %s as expected (%s)" % (name, expect, detail))
            else:
                print("BAD  %s: expected %s, got %s (%s)" % (name, expect, outcome, detail))
                bad += 1
            after = digest(ROOT)
            if after != before:
                print("BAD  the plugin's own files changed during '%s'" % name)
                bad += 1
                before = after
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("misbehaving: %d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
