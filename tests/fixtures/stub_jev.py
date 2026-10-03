#!/usr/bin/env python3
"""A stand-in for a Jev-compatible decision server (POST /v1/systemone), run as an adapter command.

Reads the systemone request on standard input and answers the question `decision` with the option in
$STUB_CHOICE at confidence $STUB_CONF. With $STUB_KIND=json it answers like `claude -p --output-format
json` instead (structured_output). Every call is appended to $STUB_LOG, so a test can prove it was never
called. $STUB_BROKEN=1 answers an option that does not exist.
"""
import json
import os
import sys

request = sys.stdin.read()
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a") as fh:
        fh.write("called\n")
choice = os.environ.get("STUB_CHOICE", "billing")
if os.environ.get("STUB_BROKEN") == "1":
    choice = "no-such-option"
conf = float(os.environ.get("STUB_CONF", "0.97"))
if os.environ.get("STUB_KIND") == "json":
    print(json.dumps({"type": "result", "total_cost_usd": 0.0037,
                      "structured_output": {"decision": choice, "confidence": conf}}))
else:
    json.loads(request)        # a systemone adapter must receive JSON
    print(json.dumps({"model": "stub-jev", "answers": {"decision": {"choice": choice, "confidence": conf}},
                      "usage": {"input_tokens": 40, "output_tokens": 0}}))
