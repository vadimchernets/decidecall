#!/usr/bin/env python3
"""decidecall - a decision layer for a company: pay a model only for the decisions that need one.

A repeated small decision (which team gets this message, approve or send back, is this urgent) walks a
chain and stops at the first link sure enough:

    cache -> rules -> local model -> cheap cloud model -> strong model -> a person

Each link answers with a decision and a confidence. At `act` (0.95 by default, the gate "confidence > .95"
of the JEV notes) the decision is taken. Between `verify` (0.70) and `act` the next link is asked, and two
links that agree settle it. Below that, or when no link is sure, a person decides - and the person's
answer goes into the cache, so the same question never reaches a model again. The cache also knows a
question asked in other words: with an embedder (a local model server - ollama, llama.cpp - reached through
the company's own command) it compares meanings, otherwise it compares words; a similar match is offered for
verification and never acts alone. `promote` turns decisions
the models kept agreeing on into cache entries and suggests rules; `bench` measures the whole chain on
labelled examples: accuracy, what each link decided, how honest the confidences were, the price of one
decision against sending everything to one model.

Model links are adapters: programs the company installs (strands-decider, an OpenJev server, ollama,
`claude -p`, its own CLI for a hosted model) named in decidecall-adapters.json. decidecall runs them with
no shell, feeds them the question on standard input, and checks the answer against the task's output
schema. It never opens a network connection itself (Python standard library only, no network module).

The company policy file company-ai-policy.json (shared with billcall, gatecall, routecall and firmcall)
decides where a decision may go: red data never leaves the computer, `privacy_level` "local-only" keeps
everything local and "zdr" admits only zero-data-retention cloud adapters, `allowed_providers` and
`deny_providers` filter vendors, and `max_usd_per_day` stops paid links once today's spend would pass it.
Among the adapters a link may use, the cheapest goes first.

    decidecall.py decide --task support-triage --input "I was charged twice"
    decidecall.py label --id <decision id> --answer billing
    decidecall.py promote --task support-triage [--apply]
    decidecall.py bench --task support-triage [--tiers cache,rules,local,cheap]
    decidecall.py route --task support-triage [--color red]
    decidecall.py candidates | schema --task support-triage

decidecall decides and counts; it buys nothing.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DATA = os.path.join(ROOT, "data")
LANG_DIR = os.path.join(ROOT, "lang")
POLICY_NAME = "company-ai-policy.json"
ADAPTERS_NAME = "decidecall-adapters.json"
MANAGED_DIRS = {
    "darwin": "/Library/Application Support/ClaudeCode",
    "linux": "/etc/claude-code",
    "win32": "C:\\Program Files\\ClaudeCode",
}
CHAIN = ("cache", "rules", "local", "cheap", "strong", "human")
MODEL_TIERS = ("local", "cheap", "strong")
KINDS = ("systemone", "strands-cli", "decision-json", "ollama", "openai-chat")
EMBED_KIND = "embed"
NONE_OF_THESE = "none_of_these"
COLORS = ("green", "yellow", "red")
ACT = 0.95       # JEV notes (92.txt:636-648): confidence > .95 -> act
VERIFY = 0.70    # .70-.95 -> verify; below -> escalate
SIMILAR = 0.80   # token overlap from which a cached answer is offered for verification
SIMILAR_MEANING = 0.90   # cosine of two local embeddings from which a cached answer is offered
SIMILAR_CAP = 0.90   # ...and the most a similar answer may claim: always below the act gate
CHARS_PER_TOKEN = 4
BINS = ((0.0, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 1.0001))


class Problem(Exception):
    """Input decidecall cannot use. Printed as one line; exit 2."""


# ---------------------------------------------------------------- files and words -------------

def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise Problem("cannot read %s %s (%s)" % (what, path, exc.strerror))
    except ValueError as exc:
        raise Problem("%s %s is not valid JSON (%s)" % (what, path, exc))


def lang_words(code):
    words = load_json(os.path.join(LANG_DIR, "en.json"), "the dictionary")
    if code and code != "en":
        path = os.path.join(LANG_DIR, "%s.json" % code)
        if os.path.isfile(path):
            words.update(load_json(path, "the dictionary"))
    return words


def pick_lang(policy=None, env=None):
    env = os.environ if env is None else env
    code = (policy or {}).get("language") or env.get("DECIDECALL_LANG") or ""
    if not code:
        code = (env.get("LC_ALL") or env.get("LANG") or "en").split("_")[0].split(".")[0]
    code = code.lower()
    return code if os.path.isfile(os.path.join(LANG_DIR, "%s.json" % code)) else "en"


# ---------------------------------------------------------------- the company policy file -----

def policy_paths(cwd=None):
    """Where company-ai-policy.json is looked for, the binding one first: the managed copy an
    administrator installed, then $COMPANY_AI_POLICY, then the project, then the person's own."""
    out = []
    managed = MANAGED_DIRS.get(sys.platform)
    if managed:
        out.append(os.path.join(managed, POLICY_NAME))
    env = os.environ.get("COMPANY_AI_POLICY")
    if env:
        out.append(env)
    base = cwd or os.getcwd()
    out.append(os.path.join(base, POLICY_NAME))
    out.append(os.path.join(base, ".claude", POLICY_NAME))
    out.append(os.path.join(os.path.expanduser("~"), ".claude", POLICY_NAME))
    return out


def read_policy(explicit=None, cwd=None):
    """-> (policy dict, path) or ({}, None). A file that does not parse is a Problem, not silence."""
    for path in ([explicit] if explicit else policy_paths(cwd)):
        if path and os.path.isfile(path):
            data = load_json(path, "the company policy")
            if not isinstance(data, dict):
                raise Problem("%s must hold one JSON object" % path)
            return data, path
    if explicit:
        raise Problem("no company policy at %s" % explicit)
    return {}, None


def policy_list(policy, key):
    value = (policy or {}).get(key) or []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().lower() for v in value if str(v).strip()]


# ---------------------------------------------------------------- tasks -----------------------

def load_task(name):
    """A task by file path, or by id from the project's decidecall-tasks/ folder or data/tasks/."""
    if name.endswith(".json") or os.sep in name or "/" in name:
        candidates = [name]
    else:
        candidates = [os.path.join(os.getcwd(), "decidecall-tasks", name + ".json"),
                      os.path.join(DATA, "tasks", name + ".json")]
    for path in candidates:
        if os.path.isfile(path):
            task = load_json(path, "the task")
            return check_task(task, path)
    raise Problem("no task %r (looked in %s)" % (name, ", ".join(candidates)))


def check_task(task, path="the task"):
    if not isinstance(task, dict) or not task.get("id"):
        raise Problem("%s needs an id" % path)
    options = task.get("options")
    if isinstance(options, list):
        options = {str(o): "" for o in options}
        task["options"] = options
    if not isinstance(options, dict) or len(options) < 2:
        raise Problem("%s needs two or more options" % path)
    for rule in task.get("rules") or []:
        if rule.get("label") not in options:
            raise Problem("%s: a rule decides %r, which is not an option" % (path, rule.get("label")))
        if not rule.get("any"):
            raise Problem("%s: a rule for %s names no words" % (path, rule.get("label")))
    th = task.setdefault("thresholds", {})
    th.setdefault("act", ACT)
    th.setdefault("verify", VERIFY)
    if not 0 < th["verify"] <= th["act"] <= 1:
        raise Problem("%s: thresholds must be 0 < verify <= act <= 1" % path)
    chain = task.setdefault("chain", list(CHAIN))
    for tier in chain:
        if tier not in CHAIN:
            raise Problem("%s: unknown link %r in the chain (known: %s)" % (path, tier, ", ".join(CHAIN)))
    if chain[-1] != "human":
        chain.append("human")
    return task


def output_schema(task):
    """The outputSchema every model adapter's answer must match. Runners that decode against a schema
    (claude -p --json-schema, ollama `format`, llama.cpp / vLLM `response_format`) can only produce this shape;
    the rest are checked against it after the fact. `none_of_these` sends the decision to a person."""
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": sorted(task["options"]) + [NONE_OF_THESE]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["decision", "confidence"],
        "additionalProperties": False,
    }


def prompt_for(task, text):
    lines = [task.get("question") or "Choose one option.", "", "Options:"]
    for name in sorted(task["options"]):
        desc = task["options"][name]
        lines.append("- %s%s" % (name, ": " + desc if desc else ""))
    lines += ["",
              'Answer with JSON only: {"decision": one of %s, "confidence": the probability from 0 to 1 that '
              'your decision is right, "reason": a few words}. Answer "%s" when no option fits. Be calibrated: '
              'say 0.95 or more only when you are almost never wrong on messages like this.'
              % (json.dumps(sorted(task["options"])), NONE_OF_THESE),
              "", "Message:", "<<<", text, ">>>"]
    return "\n".join(lines)


# ---------------------------------------------------------------- text ------------------------

def normalize(text):
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.UNICODE).split())


def cache_key(task_id, text):
    return hashlib.sha256(("%s\n%s" % (task_id, normalize(text))).encode("utf-8")).hexdigest()


def tokens(text):
    return set(w for w in normalize(text).split() if len(w) > 2)


def overlap(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def estimate_tokens(text):
    return max(1, int(math.ceil(len(text or "") / float(CHARS_PER_TOKEN))))


# ---------------------------------------------------------------- state: cache and journal ----

def state_dir(explicit=None):
    path = explicit or os.environ.get("DECIDECALL_HOME") or os.path.join(os.path.expanduser("~"), ".decidecall")
    os.makedirs(os.path.join(path, "cache"), exist_ok=True)
    return path


def cache_path(state, task_id):
    return os.path.join(state, "cache", "%s.json" % re.sub(r"[^\w.-]", "_", task_id))


def read_cache(state, task_id):
    path = cache_path(state, task_id)
    if not os.path.isfile(path):
        return {}
    data = load_json(path, "the cache")
    return data if isinstance(data, dict) else {}


def write_cache(state, task_id, cache):
    path = cache_path(state, task_id)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def journal_path(state):
    return os.path.join(state, "journal.jsonl")


def read_journal(state):
    path = journal_path(state)
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def append_journal(state, entry):
    with open(journal_path(state), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def today():
    return datetime.date.today().isoformat()


def spent_today(state, day=None):
    day = day or today()
    return sum(float(e.get("cost_usd") or 0) for e in read_journal(state) if e.get("day") == day)


# ---------------------------------------------------------------- adapters --------------------

def adapter_paths(cwd=None):
    base = cwd or os.getcwd()
    out = []
    env = os.environ.get("DECIDECALL_ADAPTERS")
    if env:
        out.append(env)
    out += [os.path.join(base, ADAPTERS_NAME), os.path.join(base, ".claude", ADAPTERS_NAME),
            os.path.join(os.path.expanduser("~"), ".decidecall", "adapters.json")]
    return out


def load_adapters(explicit=None, cwd=None):
    """-> (list of adapters, path or None). No file = no model links, which is a valid chain."""
    for path in ([explicit] if explicit else adapter_paths(cwd)):
        if path and os.path.isfile(path):
            doc = load_json(path, "the adapters file")
            items = doc.get("adapters") if isinstance(doc, dict) else doc
            if not isinstance(items, list):
                raise Problem("%s must hold a list 'adapters'" % path)
            return [check_adapter(a, path) for a in items], path
    if explicit:
        raise Problem("no adapters file at %s" % explicit)
    return [], None


def check_adapter(a, path):
    if not isinstance(a, dict) or not a.get("id"):
        raise Problem("%s: every adapter needs an id" % path)
    if a.get("kind") == EMBED_KIND:
        return check_embedder(a, path)
    if a.get("tier") not in MODEL_TIERS:
        raise Problem("%s: adapter %s has tier %r (one of %s)" % (path, a["id"], a.get("tier"), ", ".join(MODEL_TIERS)))
    if a.get("kind") not in KINDS:
        raise Problem("%s: adapter %s has kind %r (one of %s)" % (path, a["id"], a.get("kind"), ", ".join(KINDS)))
    cmd = a.get("command")
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise Problem("%s: adapter %s needs a command (a list of words)" % (path, a["id"]))
    a = dict(a, command=cmd)
    a.setdefault("location", "local" if a.get("provider", "local") == "local" else "cloud")
    if a["location"] not in ("local", "cloud"):
        raise Problem("%s: adapter %s has location %r (local or cloud)" % (path, a["id"], a["location"]))
    a.setdefault("provider", "local" if a["location"] == "local" else "unknown")
    for key in ("usd_per_mtok_in", "usd_per_mtok_out"):
        value = a.get(key, 0)
        if not isinstance(value, (int, float)) or value < 0:
            raise Problem("%s: adapter %s: %s must be a number >= 0" % (path, a["id"], key))
        a[key] = float(value)
    a.setdefault("family", a["id"])
    check_keychain(a, path)
    return a


def check_command(a, path):
    cmd = a.get("command")
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise Problem("%s: adapter %s needs a command (a list of words)" % (path, a["id"]))
    return cmd


def check_keychain(a, path):
    secrets = a.get("keychain") or {}
    if not isinstance(secrets, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in secrets.items()):
        raise Problem("%s: adapter %s: keychain maps an environment variable to a keychain service name" % (path, a["id"]))


def check_embedder(a, path):
    """The meaning cache's embedder: a command that sends {"model", "input": [texts]} to a model server on
    this computer (ollama /api/embed, llama.cpp or any /v1/embeddings) and prints its answer."""
    a = dict(a, command=check_command(a, path), tier="embed")
    a.setdefault("location", "local")
    if a["location"] != "local":
        raise Problem("%s: embedder %s must be local: the meaning cache stays on this computer" % (path, a["id"]))
    if not a.get("model"):
        raise Problem("%s: embedder %s needs a model name" % (path, a["id"]))
    return a


def keychain_secret(service):
    """A secret from the computer's own keychain (macOS Keychain, the Secret Service on Linux), or None."""
    if sys.platform == "darwin":
        cmd = ["security", "find-generic-password", "-s", service, "-w"]
    elif sys.platform.startswith("linux"):
        cmd = ["secret-tool", "lookup", "service", service]
    else:
        return None
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


def adapter_env(a):
    """The adapter's environment: ours, plus every keychain secret it names that is not already set."""
    env = dict(os.environ)
    for var, service in (a.get("keychain") or {}).items():
        if not env.get(var):
            value = keychain_secret(service)
            if value:
                env[var] = value
    return env


def price_of(a, in_tok, out_tok=20):
    return in_tok * a["usd_per_mtok_in"] / 1e6 + out_tok * a["usd_per_mtok_out"] / 1e6


def adapter_allowed(a, policy, color):
    """-> None when the policy lets this adapter take this decision, else the reason in English."""
    provider = str(a.get("provider") or "").lower()
    level = str((policy or {}).get("privacy_level") or "standard").lower()
    if a["location"] == "cloud":
        if color == "red":
            return "red data never leaves this computer"
        if level in ("local-only", "local", "red"):
            return "the company policy keeps every decision on this computer (privacy_level %s)" % level
        if level == "zdr" and not a.get("zdr"):
            return "the company policy admits only zero-data-retention cloud adapters (privacy_level zdr)"
    if provider in policy_list(policy, "deny_providers"):
        return "the company policy denies %s" % provider
    allowed = policy_list(policy, "allowed_providers")
    if allowed and provider != "local" and provider not in allowed:
        return "%s is not among the company's allowed providers" % provider
    return None


def tier_adapters(adapters, tier, policy, color, in_tok):
    """The adapters this link may use, cheapest first, and the refused ones with their reasons."""
    ok, refused = [], []
    for a in adapters:
        if a["tier"] != tier:
            continue
        why = adapter_allowed(a, policy, color)
        if why:
            refused.append((a, why))
        else:
            ok.append(a)
    ok.sort(key=lambda a: (price_of(a, in_tok), a["id"]))
    return ok, refused


def fill(word, values):
    for key, value in values.items():
        word = word.replace("{%s}" % key, value)
    return word


def parse_answer(kind, out, task):
    """-> (decision, confidence, reported_cost or None, reason). Raises Problem when the answer is unusable.
    The decision may be NONE_OF_THESE: the model says no option fits."""
    options = task["options"]
    reason = ""
    if kind == "strands-cli":
        m = re.search(r"->\s*(\S+)\s*\(confidence\s*([0-9.]+)\)", out)
        if not m:
            raise Problem("no 'choice -> option (confidence x)' line in the answer")
        decision, conf, cost = m.group(1), float(m.group(2)), None
    else:
        try:
            doc = json.loads(out)
        except ValueError:
            m = re.search(r"\{.*\}", out, re.S)
            if not m:
                raise Problem("the answer is not JSON")
            try:
                doc = json.loads(m.group(0))
            except ValueError:
                raise Problem("the answer is not JSON")
        cost = None
        if kind == "systemone":
            ans = ((doc or {}).get("answers") or {}).get("decision") or {}
            decision = ans.get("choice")
            conf = ans.get("confidence")
            probs = ans.get("probabilities") or {}
            if decision in probs:
                # A systemone `confidence` is (p_max - 1/n) / (1 - 1/n), a margin, not a probability; the
                # probability of the chosen option is what the act gate compares with.
                conf = probs[decision]
        else:
            if isinstance(doc, dict) and isinstance(doc.get("total_cost_usd"), (int, float)):
                cost = float(doc["total_cost_usd"])
            if isinstance(doc, dict) and isinstance(doc.get("structured_output"), dict):
                doc = doc["structured_output"]
            elif isinstance(doc, dict) and isinstance(doc.get("result"), str):
                try:
                    doc = json.loads(doc["result"])
                except ValueError:
                    m = re.search(r"\{.*\}", doc["result"], re.S)
                    doc = json.loads(m.group(0)) if m else {}
            elif isinstance(doc, dict) and isinstance(doc.get("response"), str):
                doc = json.loads(doc["response"])          # ollama /api/generate
            elif isinstance(doc, dict) and isinstance(doc.get("choices"), list) and doc["choices"]:
                content = ((doc["choices"][0] or {}).get("message") or {}).get("content")   # /v1/chat/completions
                if not isinstance(content, str):
                    raise Problem("the chat answer has no message content")
                doc = json.loads(content)
            if not isinstance(doc, dict):
                raise Problem("the answer is not a JSON object")
            extra = set(doc) - {"decision", "confidence", "reason"}
            if extra:
                raise Problem("the answer has fields the schema does not allow: %s" % ", ".join(sorted(extra)))
            decision, conf = doc.get("decision"), doc.get("confidence")
            reason = doc.get("reason") or ""
            if not isinstance(reason, str):
                raise Problem("the reason is not text")
    if decision not in options and decision != NONE_OF_THESE:
        raise Problem("the answer %r is not one of the options" % (decision,))
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise Problem("the confidence %r is not a number from 0 to 1" % (conf,))
    return decision, float(conf), cost, reason[:300]


def request_body(a, task, prompt, text):
    """What goes on the adapter's standard input: the prompt, or the JSON request its runner takes. The
    `ollama` and `openai-chat` kinds hand the output schema to the runner, which decodes only that shape."""
    schema = output_schema(task)
    if a["kind"] == "systemone":
        request = {"state": text, "questions": {"decision": {
            "type": "choice", "instructions": task.get("question") or "",
            "criteria": {k: (v or k) for k, v in task["options"].items()}}}}
        if a.get("model"):
            request["model"] = a["model"]
        return json.dumps(request)
    if a["kind"] == "ollama":
        return json.dumps({"model": a.get("model") or "", "prompt": prompt, "format": schema, "stream": False,
                           "options": {"temperature": 0}})
    if a["kind"] == "openai-chat":
        return json.dumps({"model": a.get("model") or "", "temperature": 0,
                           "messages": [{"role": "user", "content": prompt}],
                           "response_format": {"type": "json_schema", "json_schema": {
                               "name": "decision", "strict": True, "schema": schema}}})
    return prompt


def run_adapter(a, task, text):
    """-> dict(decision, confidence, cost_usd) or dict(note=why it gave nothing)."""
    schema = json.dumps(output_schema(task), separators=(",", ":"))
    prompt = prompt_for(task, text)
    values = {"state": text, "question": task.get("question") or "", "prompt": prompt, "schema": schema,
              "options_csv": ",".join(sorted(task["options"]))}
    stdin = request_body(a, task, prompt, text)
    cmd = [fill(w, values) for w in a["command"]]
    in_tok = estimate_tokens(stdin if a["kind"] == "systemone" else prompt)
    try:
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=adapter_env(a),
                              timeout=float(a.get("timeout", 180)))
    except FileNotFoundError:
        return {"note": "%s is not installed (%s)" % (a["id"], cmd[0])}
    except subprocess.TimeoutExpired:
        return {"note": "%s gave no answer in time" % a["id"]}
    except OSError as exc:
        return {"note": "%s did not start (%s)" % (a["id"], exc.strerror)}
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
        return {"note": "%s ended with code %d: %s" % (a["id"], done.returncode, tail[0][:200])}
    try:
        decision, conf, reported, reason = parse_answer(a["kind"], done.stdout, task)
    except (Problem, ValueError) as exc:
        return {"note": "%s: the answer failed the output schema (%s)" % (a["id"], exc)}
    cost = reported if reported is not None else price_of(a, in_tok)
    if decision == NONE_OF_THESE:
        return {"none_of_these": True, "confidence": conf, "cost_usd": round(cost, 8), "family": a["family"],
                "note": "%s: none of the options fits%s" % (a["id"], " (%s)" % reason if reason else "")}
    res = {"decision": decision, "confidence": conf, "cost_usd": round(cost, 8), "family": a["family"]}
    if reason:
        res["reason"] = reason
    return res


# ---------------------------------------------------------------- the meaning cache -----------

def run_embedder(e, texts):
    """-> one vector per text from the local model server, or None when no server answered."""
    body = json.dumps({"model": e["model"], "input": list(texts)})
    try:
        done = subprocess.run(e["command"], input=body, capture_output=True, text=True,
                              timeout=float(e.get("timeout", 5)))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        doc = json.loads(done.stdout)
    except ValueError:
        return None
    vecs = None
    if isinstance(doc, dict) and isinstance(doc.get("embeddings"), list):
        vecs = doc["embeddings"]                                   # ollama /api/embed
    elif isinstance(doc, dict) and isinstance(doc.get("data"), list):
        rows = sorted(doc["data"], key=lambda r: r.get("index", 0) if isinstance(r, dict) else 0)
        vecs = [r.get("embedding") if isinstance(r, dict) else None for r in rows]   # /v1/embeddings
    if not vecs or len(vecs) != len(texts):
        return None
    for v in vecs:
        if not isinstance(v, list) or not v or not all(isinstance(x, (int, float)) for x in v):
            return None
    return [[float(x) for x in v] for v in vecs]


def embed_cache(e, cache, text):
    """Embed the question and every cached text that has no vector from this model yet (a new model or new
    dimensions are recomputed), all in one call. -> the question's vector, or None (no server)."""
    stale = [k for k, entry in cache.items() if entry.get("text") and not entry.get("refuted")
             and (entry.get("vec_model") != e["model"] or len(entry.get("vec") or []) != entry.get("vec_dims"))]
    vecs = run_embedder(e, [text] + [cache[k]["text"] for k in stale])
    if vecs is None:
        return None
    dims = len(vecs[0])
    for k, v in zip(stale, vecs[1:]):
        cache[k].update(vec=v, vec_model=e["model"], vec_dims=len(v))
    return vecs[0] if all(len(v) == dims for v in vecs) else None


def similar_answer(best, score, how, key):
    # A similar message is never acted on alone (JEV notes: "similar cache -> reuse / verify"): its
    # confidence stays under the act gate, it is marked similar, and the next link has to agree.
    conf = min(score, SIMILAR_CAP) * float(best.get("confidence", 1.0))
    return {"decision": best["decision"], "confidence": round(conf, 4), "cost_usd": 0.0, "similar": True,
            "similar_to": key, "note": "similar to a cached message (%s %.2f)" % (how, score)}


# ---------------------------------------------------------------- the chain -------------------

def from_cache(task, text, cache, embedder=None):
    """Exact repeats first; then the cached message closest in meaning (local embeddings, when an embedder
    answers) or in words (overlap, otherwise). An entry a person refuted is never offered again; an entry may
    carry its own `similar_min`. `cache` gains vectors in place; the result says so with `cache_changed`."""
    key = cache_key(task["id"], text)
    hit = cache.get(key)
    if hit:
        return {"decision": hit["decision"], "confidence": float(hit.get("confidence", 1.0)), "cost_usd": 0.0,
                "note": "exact repeat (%s)" % hit.get("source", "cache")}
    live = [(k, e) for k, e in sorted(cache.items()) if e.get("text") and not e.get("refuted")]
    if embedder and live:
        qvec = embed_cache(embedder, cache, text)
        if qvec is not None:
            floor = float(task["thresholds"].get("similar_meaning", SIMILAR_MEANING))
            best, score, best_key = None, 0.0, None
            for k, entry in live:
                s = cosine(qvec, entry.get("vec"))
                if s > score:
                    best, score, best_key = entry, s, k
            if best and score >= float(best.get("similar_min", floor)):
                res = similar_answer(best, score, "meaning, %s" % embedder["model"], best_key)
            else:
                res = {"note": "not in the cache (closest meaning %.2f)" % score}
            res["cache_changed"] = True
            return res
    best, score, best_key = None, 0.0, None
    for k, entry in live:
        s = overlap(text, entry["text"])
        if s > score:
            best, score, best_key = entry, s, k
    if best and score >= float(best.get("similar_min", SIMILAR)):
        return similar_answer(best, score, "overlap", best_key)
    return {"note": "not in the cache"}


def from_rules(task, text):
    padded = " %s " % normalize(text)
    hits = {}
    for rule in task.get("rules") or []:
        for word in rule["any"]:
            if " %s " % normalize(word) in padded:
                label = rule["label"]
                hits[label] = max(hits.get(label, 0.0), float(rule.get("confidence", 0.99)))
                break
    if len(hits) == 1:
        label, conf = next(iter(hits.items()))
        return {"decision": label, "confidence": conf, "cost_usd": 0.0, "note": "rule"}
    if len(hits) > 1:
        return {"note": "rules disagree (%s)" % ", ".join(sorted(hits))}
    return {"note": "no rule matches"}


def embedder_of(adapters):
    """The first embedder among the adapters (kind "embed"), or None."""
    return next((a for a in adapters or () if a.get("kind") == EMBED_KIND), None)


def decide(task, text, color="green", policy=None, adapters=(), state=None, tiers=None):
    """Walk the chain. -> the decision record (also what goes into the journal)."""
    embedder = embedder_of(adapters)
    adapters = [a for a in adapters or () if a.get("kind") != EMBED_KIND]
    policy = policy or {}
    if color not in COLORS:
        raise Problem("color must be one of %s" % ", ".join(COLORS))
    act, verify = task["thresholds"]["act"], task["thresholds"]["verify"]
    chain = [t for t in task["chain"] if tiers is None or t in tiers or t == "human"]
    cache = read_cache(state, task["id"]) if state else {}
    budget = (policy or {}).get("max_usd_per_day")
    spent = spent_today(state) if (state and budget is not None) else 0.0
    in_tok = estimate_tokens(prompt_for(task, text))
    steps, heard, final, cost, none_fits = [], [], None, 0.0, None
    for tier in chain:
        if tier == "human":
            break
        if tier == "cache":
            res = from_cache(task, text, cache, embedder)
            if res.pop("cache_changed", False) and state:
                write_cache(state, task["id"], cache)
            results = [("cache", res)]
        elif tier == "rules":
            results = [("rules", from_rules(task, text))]
        else:
            ok, refused = tier_adapters(adapters, tier, policy, color, in_tok)
            for a, why in refused:
                steps.append({"tier": tier, "adapter": a["id"], "note": why})
            results = []
            for a in ok:
                est = price_of(a, in_tok)
                if budget is not None and est > 0 and spent + cost + est > float(budget):
                    steps.append({"tier": tier, "adapter": a["id"],
                                  "note": "today's budget of $%s would be passed (spent $%.4f)" % (budget, spent + cost)})
                    continue
                res = run_adapter(a, task, text)
                results.append((a["id"], res))
                if "decision" in res:
                    break           # one answer per link: the cheapest that answered
            if not ok and not refused:
                steps.append({"tier": tier, "note": "no adapter for this link"})
        for name, res in results:
            step = {"tier": tier, "adapter": name}
            step.update(res)
            steps.append(step)
            cost += float(res.get("cost_usd") or 0)
            if res.get("none_of_these") and res["confidence"] >= verify:
                none_fits = dict(res, tier=tier, adapter=name)
                break
            if "decision" not in res:
                continue
            if res["confidence"] >= act and not res.get("similar"):
                final = dict(res, tier=tier, adapter=name, status="act")
                break
            source = res.get("family") or name
            agree = [h for h in heard if h["decision"] == res["decision"] and h["source"] != source
                     and max(h["confidence"], res["confidence"]) >= verify]
            if agree:
                final = dict(res, tier=tier, adapter=name, status="verified",
                             confidence=max([res["confidence"]] + [h["confidence"] for h in agree]),
                             families=sorted(set([source] + [h["source"] for h in agree])))
                break
            heard.append(dict(res, tier=tier, source=source))
        if final or none_fits:
            break
    record = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "day": today(),
        "task": task["id"],
        "key": cache_key(task["id"], text),
        "color": color,
        "steps": steps,
        "cost_usd": round(cost, 8),
    }
    if color != "red":
        record["text"] = text
    if final:
        record.update(decision=final["decision"], confidence=final["confidence"], tier=final["tier"],
                      adapter=final["adapter"], status=final["status"],
                      families=final.get("families") or [final.get("family") or final["adapter"]])
        if final.get("reason"):
            record["reason"] = final["reason"]
    else:
        guess = max(heard, key=lambda h: h["confidence"]) if heard else None
        record.update(decision=None, tier="human", status="human",
                      best_guess=guess and {"decision": guess["decision"], "confidence": guess["confidence"],
                                            "tier": guess["tier"]})
        if none_fits:
            record["none_of_these"] = {"adapter": none_fits["adapter"], "confidence": none_fits["confidence"]}
    record["id"] = hashlib.sha256((record["ts"] + record["key"] + str(os.getpid())).encode()).hexdigest()[:12]
    return record


def remember(state, task_id, text, decision, confidence, source, color):
    cache = read_cache(state, task_id)
    entry = {"decision": decision, "confidence": confidence, "source": source}
    if color != "red":
        entry["text"] = text
    cache[cache_key(task_id, text)] = entry
    write_cache(state, task_id, cache)


# ---------------------------------------------------------------- promote ---------------------

def promote(task, state, min_count=2, min_agreement=1.0, apply=False):
    """Decisions the models kept making the same way, and every person's answer, become cache entries.
    Agreement counts only across sources: one model (one adapter `family`) answering a repeat twice is one
    voice, so `min_count` names how many different families must agree. Words that only ever came with one
    decision are offered as rules (never added on their own)."""
    act = task["thresholds"]["act"]
    groups = {}
    for e in read_journal(state):
        if e.get("task") != task["id"] or not e.get("decision"):
            continue
        g = groups.setdefault(e["key"], {"votes": [], "human": None, "text": e.get("text"), "color": e.get("color")})
        if e.get("status") == "human-answer":
            g["human"] = e["decision"]
        elif e.get("status") in ("act", "verified") and e.get("tier") in MODEL_TIERS:
            g["votes"].append((e["decision"], float(e.get("confidence") or 0),
                               tuple(e.get("families") or [e.get("adapter") or "?"])))
    cache = read_cache(state, task["id"])
    promoted = []
    for key, g in sorted(groups.items()):
        if key in cache:
            continue
        if g["human"]:
            promoted.append((key, g["human"], 1.0, "a person's answer", g))
            continue
        if not g["votes"]:
            continue
        labels = [d for d, _, _ in g["votes"]]
        top = max(sorted(set(labels)), key=labels.count)
        share = labels.count(top) / float(len(labels))
        mean = sum(c for d, c, _ in g["votes"] if d == top) / labels.count(top)
        sources = set(f for d, _, fams in g["votes"] if d == top for f in fams)
        if len(sources) >= min_count and share >= min_agreement and mean >= act:
            promoted.append((key, top, round(mean, 4), "%d agreeing model decisions from %s"
                             % (labels.count(top), ", ".join(sorted(sources))), g))
    if apply:
        for key, label, conf, why, g in promoted:
            entry = {"decision": label, "confidence": conf, "source": "promoted: " + why}
            if g.get("color") != "red" and g.get("text"):
                entry["text"] = g["text"]
            cache[key] = entry
        if promoted:
            write_cache(state, task["id"], cache)
    # rule suggestions from every decided, non-red text
    by_label = {}
    for e in read_journal(state):
        if e.get("task") == task["id"] and e.get("decision") and e.get("text"):
            by_label.setdefault(e["decision"], []).append(tokens(e["text"]))
    known = set(normalize(w) for r in task.get("rules") or [] for w in r["any"])
    suggestions = []
    for label, sets in sorted(by_label.items()):
        counts = {}
        for s in sets:
            for w in s:
                counts[w] = counts.get(w, 0) + 1
        others = set().union(*[x for l2, ss in by_label.items() if l2 != label for x in ss]) if len(by_label) > 1 else set()
        for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            if n >= 3 and w not in others and w not in known:
                suggestions.append({"label": label, "word": w, "seen": n})
    return {"promoted": [{"key": k[:12], "decision": l, "confidence": c, "why": w} for k, l, c, w, _ in promoted],
            "applied": bool(apply), "rule_suggestions": suggestions}


# ---------------------------------------------------------------- bench -----------------------

def read_dataset(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise Problem("%s line %d is not JSON" % (path, n))
            if not isinstance(row, dict) or "input" not in row or "label" not in row:
                raise Problem("%s line %d needs input and label" % (path, n))
            rows.append(row)
    if not rows:
        raise Problem("%s holds no examples" % path)
    return rows


def calibration(pairs):
    """pairs: (confidence, correct). -> (bins, expected calibration error)."""
    out, ece, n = [], 0.0, len(pairs)
    for lo, hi in BINS:
        inside = [(c, ok) for c, ok in pairs if lo <= c < hi]
        if not inside:
            continue
        mean = sum(c for c, _ in inside) / len(inside)
        acc = sum(1 for _, ok in inside if ok) / float(len(inside))
        out.append({"from": lo, "to": min(hi, 1.0), "count": len(inside),
                    "mean_confidence": round(mean, 4), "accuracy": round(acc, 4)})
        ece += abs(mean - acc) * len(inside) / n
    return out, round(ece, 4)


def bench(task, rows, tiers=None, adapters=(), policy=None, state=None, simulate_human=True):
    """Run every example through the chain in order. A person's answer (the label, when simulate_human)
    goes into the cache, as `label` does in real work, so a repeat of it is decided for free."""
    own = state is None
    state = state_dir(state or tempfile.mkdtemp(prefix="decidecall-bench-"))
    tiers = list(tiers) if tiers else list(task["chain"])
    per_tier = {t: {"decided": 0, "correct": 0} for t in task["chain"] if t in tiers or t == "human"}
    pairs, wrong, total_cost, auto, correct = [], [], 0.0, 0, 0
    unavailable = [t for t in tiers if t in MODEL_TIERS and not any(a["tier"] == t for a in adapters)]
    base = task.get("baseline") or {}
    base_cost = 0.0
    for row in rows:
        if row["label"] not in task["options"]:
            raise Problem("example %s has label %r, not an option" % (row.get("id"), row["label"]))
        color = row.get("color", "green")
        rec = decide(task, row["input"], color=color, policy=policy, adapters=adapters, state=state, tiers=tiers)
        rec["bench_id"] = row.get("id")
        total_cost += rec["cost_usd"]
        if base:
            in_tok = estimate_tokens(prompt_for(task, row["input"]))
            base_cost += in_tok * float(base.get("usd_per_mtok_in", 0)) / 1e6 + \
                int(base.get("output_tokens", 20)) * float(base.get("usd_per_mtok_out", 0)) / 1e6
        tier = rec["tier"]
        per_tier.setdefault(tier, {"decided": 0, "correct": 0})["decided"] += 1
        if rec["status"] == "human":
            if simulate_human:
                remember(state, task["id"], row["input"], row["label"], 1.0, "a person's answer", color)
            per_tier["human"]["correct"] += 1 if simulate_human else 0
            continue
        ok = rec["decision"] == row["label"]
        auto += 1
        correct += ok
        per_tier[tier]["correct"] += ok
        pairs.append((rec["confidence"], ok))
        if not ok:
            wrong.append({"id": row.get("id"), "expected": row["label"], "got": rec["decision"],
                          "tier": tier, "confidence": rec["confidence"]})
        append_journal(state, rec)
    n = len(rows)
    bins, ece = calibration(pairs)
    human = per_tier.get("human", {}).get("decided", 0)
    result = {
        "task": task["id"], "examples": n, "tiers": tiers, "tiers_without_adapter": unavailable,
        "decided_automatically": auto, "right_automatically": correct,
        "accuracy_automatic": round(correct / float(auto), 4) if auto else None,
        "to_a_person": human, "share_to_a_person": round(human / float(n), 4),
        "per_tier": per_tier, "calibration": bins, "expected_calibration_error": ece if pairs else None,
        "cost_usd_total": round(total_cost, 6), "cost_usd_per_decision": round(total_cost / n, 8),
        "wrong": wrong,
    }
    if base:
        result["baseline"] = {"name": base.get("name"), "cost_usd_total": round(base_cost, 6),
                              "url": base.get("url"), "checked": base.get("checked"),
                              "note": "the same examples, every one sent to this model at list price"}
    result["negative_results"] = negatives(result, task)
    if own:
        result["state"] = "a temporary folder, removed"
        import shutil
        shutil.rmtree(state, ignore_errors=True)
    return result


def negatives(result, task):
    """What did not work, said as plainly as what did."""
    out = []
    act = task["thresholds"]["act"]
    acc = result["accuracy_automatic"]
    if acc is not None and acc < act:
        out.append("automatic decisions were right %.1f%% of the time, below the %.0f%% the act threshold promises"
                   % (acc * 100, act * 100))
    if result["share_to_a_person"] > 0.5:
        out.append("%.0f%% of the examples still needed a person" % (result["share_to_a_person"] * 100))
    for t in result["tiers"]:
        if t in ("human",):
            continue
        if t in result["tiers_without_adapter"]:
            out.append("link %s was asked for but has no adapter on this computer - not measured" % t)
        elif result["per_tier"].get(t, {}).get("decided", 0) == 0:
            out.append("link %s decided nothing" % t)
    base = result.get("baseline")
    if base and result["cost_usd_total"] > base["cost_usd_total"]:
        out.append("the chain cost $%.4f, more than the reference (%s): $%.4f - an adapter carries more than the "
                   "question (a program's own instructions, or a dearer model)"
                   % (result["cost_usd_total"], base.get("name") or "one model for everything", base["cost_usd_total"]))
    for b in result["calibration"]:
        if b["count"] >= 3 and b["accuracy"] + 0.1 < b["mean_confidence"]:
            out.append("confidences %.2f-%.2f were overconfident: said %.2f, were right %.2f"
                       % (b["from"], b["to"], b["mean_confidence"], b["accuracy"]))
    return out


def bench_markdown(result, words):
    pct = lambda x: "-" if x is None else "%.1f%%" % (x * 100)
    lines = ["# %s: %s" % (words["bench_title"], result["task"]), "",
             "| | |", "|---|---|",
             "| %s | %d |" % (words["bench_examples"], result["examples"]),
             "| %s | %d (%s) |" % (words["bench_auto"], result["decided_automatically"], pct(result["accuracy_automatic"])),
             "| %s | %d (%s) |" % (words["bench_person"], result["to_a_person"], pct(result["share_to_a_person"])),
             "| %s | $%.6f |" % (words["bench_cost"], result["cost_usd_per_decision"])]
    if result.get("baseline"):
        lines.append("| %s | $%.6f |" % (words["bench_baseline"], result["baseline"]["cost_usd_total"] / result["examples"]))
    lines += ["", "## " + words["bench_tiers"], "", "| | decided | right |", "|---|---|---|"]
    for t, v in result["per_tier"].items():
        lines.append("| %s | %d | %d |" % (t, v["decided"], v["correct"]))
    lines += ["", "## " + words["bench_negative"], ""]
    lines += ["- " + s for s in result["negative_results"]] or ["-"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the command line ------------

def parser():
    p = argparse.ArgumentParser(prog="decidecall", description="A decision layer: cache, rules, models, a person.")
    p.add_argument("--policy", help="company-ai-policy.json (default: the usual places)")
    p.add_argument("--adapters", help="decidecall-adapters.json (default: the usual places)")
    p.add_argument("--state-dir", help="where the cache and the journal live (default ~/.decidecall)")
    p.add_argument("--json", action="store_true", help="print JSON")
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("decide", help="take one decision")
    d.add_argument("--task", required=True)
    d.add_argument("--input", help="the text to decide on (default: standard input)")
    d.add_argument("--color", default=None, help="green, yellow or red (default: the policy's default_color, else green)")
    l = sub.add_parser("label", help="record a person's answer to a decision that reached them")
    l.add_argument("--id", required=True)
    l.add_argument("--answer", required=True)
    pr = sub.add_parser("promote", help="turn repeated, agreed decisions into cache entries; suggest rules")
    pr.add_argument("--task", required=True)
    pr.add_argument("--min-count", type=int, default=2)
    pr.add_argument("--min-agreement", type=float, default=1.0)
    pr.add_argument("--apply", action="store_true")
    b = sub.add_parser("bench", help="measure the chain on labelled examples")
    b.add_argument("--task", required=True)
    b.add_argument("--dataset", help="JSONL with input and label (default: data/bench/<task>.jsonl)")
    b.add_argument("--tiers", help="links to use, e.g. cache,rules,local (default: the task's chain)")
    b.add_argument("--no-human-cache", action="store_true", help="do not put the person's answers into the cache")
    b.add_argument("--out", help="write the result as JSON here")
    b.add_argument("--report", help="write the result as Markdown into this folder")
    r = sub.add_parser("route", help="which adapter each link would use, and why not the others")
    r.add_argument("--task", required=True)
    r.add_argument("--color", default=None)
    sub.add_parser("candidates", help="programs that can stand behind a link, with licence and source")
    s = sub.add_parser("schema", help="the output schema an adapter's answer must match")
    s.add_argument("--task", required=True)
    return p


def out(args, data, text):
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else text)


def refute_similar(cache, rec, answer):
    """A cached message offered as similar, and answered otherwise by a person, is never offered again."""
    for step in rec.get("steps") or []:
        key = step.get("similar_to")
        if key and key in cache and step.get("decision") != answer:
            cache[key]["refuted"] = True


def run_label(args, words):
    """A person's answer to a decision that reached them: journal it and put it into the cache."""
    state = state_dir(args.state_dir)
    rec = next((e for e in read_journal(state) if e.get("id") == args.id), None)
    if not rec:
        raise Problem("no decision %s in the journal" % args.id)
    task = load_task(rec["task"])
    if args.answer not in task["options"]:
        raise Problem("%r is not an option of %s" % (args.answer, task["id"]))
    entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "day": today(), "task": rec["task"],
             "key": rec["key"], "color": rec.get("color"), "ref": rec["id"], "decision": args.answer,
             "confidence": 1.0, "tier": "human", "status": "human-answer", "cost_usd": 0}
    if rec.get("text"):
        entry["text"] = rec["text"]
    append_journal(state, entry)
    cache = read_cache(state, rec["task"])
    cached = {"decision": args.answer, "confidence": 1.0, "source": "a person's answer"}
    if rec.get("text"):
        cached["text"] = rec["text"]
    cache[rec["key"]] = cached
    refute_similar(cache, rec, args.answer)
    write_cache(state, rec["task"], cache)
    out(args, entry, words["label_saved"].format(answer=args.answer))
    return 0


def run(argv):
    args = parser().parse_args(argv)
    if not args.cmd:
        parser().print_help()
        return 2
    policy, _ = read_policy(args.policy)
    words = lang_words(pick_lang(policy))
    if args.cmd == "candidates":
        doc = load_json(os.path.join(DATA, "candidates.json"), "the candidates")
        rows = ["%-26s %-7s %-20s %-10s %s (%s)" % (c["id"], c["tier"], c["license"], c["status"], c["url"], c["checked"])
                for c in doc["candidates"]]
        out(args, doc, "\n".join(rows))
        return 0
    if args.cmd == "label":
        return run_label(args, words)
    task = load_task(args.task)
    if args.cmd == "schema":
        out(args, output_schema(task), json.dumps(output_schema(task), indent=2))
        return 0
    adapters, _ = load_adapters(args.adapters)
    if args.cmd == "route":
        color = args.color or policy.get("default_color") or "green"
        lines, data = [], []
        in_tok = estimate_tokens(prompt_for(task, "x" * 400))
        for tier in task["chain"]:
            if tier not in MODEL_TIERS:
                lines.append("%-7s built in" % tier)
                continue
            ok, refused = tier_adapters(adapters, tier, policy, color, in_tok)
            first = ok[0]["id"] if ok else "-"
            lines.append("%-7s %s%s" % (tier, first, "" if ok else "  (no adapter it may use)"))
            for a, why in refused:
                lines.append("        not %s: %s" % (a["id"], why))
            data.append({"tier": tier, "uses": first, "order": [a["id"] for a in ok],
                         "refused": [{"adapter": a["id"], "why": w} for a, w in refused]})
        out(args, data, "\n".join(lines))
        return 0
    state = state_dir(args.state_dir)
    if args.cmd == "decide":
        text = args.input if args.input is not None else sys.stdin.read()
        if not text.strip():
            raise Problem("nothing to decide on: give --input or standard input")
        color = args.color or policy.get("default_color") or "green"
        rec = decide(task, text, color=color, policy=policy, adapters=adapters, state=state)
        append_journal(state, rec)
        if rec["status"] == "human":
            guess = rec.get("best_guess")
            line = words["human_needed"].format(id=rec["id"], guess=guess["decision"] if guess else "-")
        else:
            line = "%s  (%s, %s %.2f, $%.6f, id %s)" % (rec["decision"], rec["status"], rec["tier"],
                                                       rec["confidence"], rec["cost_usd"], rec["id"])
        out(args, rec, line)
        return 0
    if args.cmd == "promote":
        res = promote(task, state, args.min_count, args.min_agreement, args.apply)
        lines = ["%s %s -> %s (%s)" % ("promoted" if res["applied"] else "would promote", p["key"], p["decision"], p["why"])
                 for p in res["promoted"]] or ["nothing to promote yet"]
        lines += ["rule idea: %s when the text has '%s' (seen %d times, never with another decision)"
                  % (s["label"], s["word"], s["seen"]) for s in res["rule_suggestions"]]
        out(args, res, "\n".join(lines))
        return 0
    if args.cmd == "bench":
        path = args.dataset or os.path.join(DATA, "bench", task["id"] + ".jsonl")
        rows = read_dataset(path)
        tiers = [t.strip() for t in args.tiers.split(",")] if args.tiers else None
        for t in tiers or []:
            if t not in CHAIN:
                raise Problem("unknown link %r" % t)
        res = bench(task, rows, tiers=tiers, adapters=adapters, policy=policy, simulate_human=not args.no_human_cache)
        res["dataset"] = os.path.basename(path)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False, indent=2)
        md = bench_markdown(res, words)
        if args.report:
            os.makedirs(args.report, exist_ok=True)
            with open(os.path.join(args.report, words["bench_file"]), "w", encoding="utf-8") as fh:
                fh.write(md)
        out(args, res, md)
        return 0
    return 2


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Problem as exc:
        print("decidecall: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
