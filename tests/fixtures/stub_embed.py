#!/usr/bin/env python3
"""A stand-in for a local embedding server (ollama /api/embed), run as an embedder command.

Reads {"model", "input": [texts]} on standard input and answers {"embeddings": [...]}: each text becomes a
vector of the topics it names, so "charged twice" and "billed two times" land together. Every call is
appended to $EMBED_LOG with the number of texts. $EMBED_DOWN=1 behaves like a server that is not running
(exit 7, as curl does on a refused connection). $EMBED_STYLE=openai answers in the /v1/embeddings shape.
"""
import json
import os
import sys

TOPICS = (
    ("charged", "billed", "payment", "card", "twice", "two times", "money"),
    ("crash", "freezes", "broken", "error", "stuck", "hangs"),
    ("password", "sign in", "log in", "locked out", "login"),
    ("price", "quote", "seats", "discount"),
)

if os.environ.get("EMBED_DOWN") == "1":
    sys.stderr.write("curl: (7) Failed to connect to 127.0.0.1 port 11434\n")
    sys.exit(7)
req = json.loads(sys.stdin.read())
texts = req["input"]
log = os.environ.get("EMBED_LOG")
if log:
    with open(log, "a") as fh:
        fh.write("%s %d\n" % (req["model"], len(texts)))
vecs = []
for t in texts:
    low = t.lower()
    v = [float(sum(1 for w in words if w in low)) for words in TOPICS] + [0.05]
    vecs.append(v)
if os.environ.get("EMBED_STYLE") == "openai":
    print(json.dumps({"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)]}))
else:
    print(json.dumps({"model": req["model"], "embeddings": vecs}))
