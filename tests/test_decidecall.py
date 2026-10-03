"""decidecall: the chain stops at the first sure link, a person's answer is never asked twice, the policy
and the budget decide where a decision may go, and bench says what did not work as plainly as what did."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "decidecall", "scripts", "decidecall.py")
STUB = os.path.join(ROOT, "tests", "fixtures", "stub_jev.py")
EMBED = os.path.join(ROOT, "tests", "fixtures", "stub_embed.py")
_spec = importlib.util.spec_from_file_location("decidecall", SCRIPT)
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)

TASK = dc.load_task("support-triage")


def stub(id="jev", tier="cheap", provider="typesafe", location="cloud", price=0.042, kind="systemone", **extra):
    a = {"id": id, "tier": tier, "kind": kind, "command": [sys.executable, STUB], "provider": provider,
         "location": location, "usd_per_mtok_in": price, "usd_per_mtok_out": 0}
    a.update(extra)
    return dc.check_adapter(a, "test")


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = dc.state_dir(os.path.join(self.tmp, "state"))
        self.log = os.path.join(self.tmp, "calls")
        self.old = dict(os.environ)
        self.embed_log = os.path.join(self.tmp, "embeds")
        os.environ.update(STUB_LOG=self.log, STUB_CHOICE="billing", STUB_CONF="0.97", EMBED_LOG=self.embed_log)
        for k in ("STUB_KIND", "STUB_BROKEN", "STUB_PROBS", "STUB_REASON", "STUB_REQUEST", "EMBED_DOWN", "EMBED_STYLE"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    def calls(self):
        return open(self.log).read().count("called") if os.path.exists(self.log) else 0


class Rules(Env):
    def test_one_rule_decides_without_a_model(self):
        rec = dc.decide(TASK, "Where is my invoice for March?", state=self.state)
        self.assertEqual((rec["decision"], rec["tier"], rec["status"], rec["cost_usd"]), ("billing", "rules", "act", 0))

    def test_rules_that_disagree_abstain(self):
        rec = dc.decide(TASK, "I was charged for a demo I never asked for.", state=self.state)
        self.assertEqual(rec["status"], "human")
        self.assertTrue(any("rules disagree" in s.get("note", "") for s in rec["steps"]))

    def test_a_word_inside_another_word_is_not_a_match(self):
        self.assertEqual(dc.from_rules(TASK, "Our errorless setup")["note"], "no rule matches")


class Cache(Env):
    def test_a_persons_answer_is_never_asked_twice(self):
        text = "Can we pay by bank transfer instead of card for 40 seats?"
        first = dc.decide(TASK, text, adapters=[stub()], state=self.state, tiers=["cache", "rules"])
        self.assertEqual(first["status"], "human")
        dc.remember(self.state, TASK["id"], text, "sales", 1.0, "a person's answer", "green")
        again = dc.decide(TASK, "  can we PAY by bank transfer instead of card, for 40 seats  ", state=self.state)
        self.assertEqual((again["decision"], again["tier"]), ("sales", "cache"))

    def test_a_similar_message_is_only_offered_for_verification(self):
        dc.remember(self.state, TASK["id"], "Sync between the phone and the desktop is stuck at 99%",
                    "technical", 1.0, "a person's answer", "green")
        res = dc.from_cache(TASK, "Sync between the phone and the desktop is stuck at 98%", dc.read_cache(self.state, TASK["id"]))
        self.assertEqual(res["decision"], "technical")
        self.assertLess(res["confidence"], TASK["thresholds"]["act"])

    def test_red_text_is_kept_only_as_a_fingerprint(self):
        dc.remember(self.state, TASK["id"], "patient 4411 billing", "billing", 1.0, "a person's answer", "red")
        raw = open(dc.cache_path(self.state, TASK["id"])).read()
        self.assertNotIn("patient", raw)
        rec = dc.decide(TASK, "secret client list attached", color="red", state=self.state)
        self.assertNotIn("text", rec)


class Chain(Env):
    def test_the_stand_in_jev_flips_a_decision_that_would_go_to_a_person(self):
        text = "Can we pay by bank transfer instead of card for 40 seats?"
        without = dc.decide(TASK, text, state=self.state)
        self.assertEqual(without["status"], "human")
        os.environ["STUB_CHOICE"] = "sales"
        with_jev = dc.decide(TASK, text, adapters=[stub()], state=self.state)
        self.assertEqual((with_jev["decision"], with_jev["tier"], with_jev["adapter"]), ("sales", "cheap", "jev"))
        self.assertGreater(with_jev["cost_usd"], 0)

    def test_an_unsure_answer_escalates_and_two_links_that_agree_settle_it(self):
        os.environ["STUB_CONF"] = "0.80"
        os.environ["STUB_CHOICE"] = "sales"
        text = "Can we pay by bank transfer instead of card for 40 seats?"
        rec = dc.decide(TASK, text, adapters=[stub("cheap1"), stub("strong1", tier="strong", price=4.0)], state=self.state)
        self.assertEqual((rec["decision"], rec["status"], rec["tier"]), ("sales", "verified", "strong"))
        self.assertEqual(self.calls(), 2)

    def test_an_unsure_answer_alone_goes_to_a_person_with_its_guess(self):
        os.environ["STUB_CONF"] = "0.60"
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[stub()], state=self.state)
        self.assertEqual(rec["status"], "human")
        self.assertEqual(rec["best_guess"]["decision"], "billing")

    def test_a_sure_link_stops_the_chain(self):
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[stub("a"), stub("b", tier="strong")], state=self.state)
        self.assertEqual(rec["tier"], "cheap")
        self.assertEqual(self.calls(), 1)

    def test_an_answer_outside_the_schema_is_not_a_decision(self):
        os.environ["STUB_BROKEN"] = "1"
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[stub()], state=self.state)
        self.assertEqual(rec["status"], "human")
        self.assertTrue(any("output schema" in s.get("note", "") for s in rec["steps"]))

    def test_a_missing_program_is_a_note_not_a_crash(self):
        a = dc.check_adapter({"id": "gone", "tier": "local", "kind": "decision-json", "command": ["no-such-program-xyz"]}, "t")
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[a], state=self.state)
        self.assertTrue(any("not installed" in s.get("note", "") for s in rec["steps"]))

    def test_claude_p_json_answer_and_its_reported_cost(self):
        os.environ["STUB_KIND"] = "json"
        os.environ["STUB_CHOICE"] = "sales"
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[stub("haiku", kind="decision-json", price=1.0)],
                        state=self.state)
        self.assertEqual((rec["decision"], rec["cost_usd"]), ("sales", 0.0037))


class Policy(Env):
    def test_red_data_never_reaches_a_cloud_adapter(self):
        rec = dc.decide(TASK, "Can we pay by bank transfer?", color="red", adapters=[stub()], state=self.state)
        self.assertEqual(self.calls(), 0)
        self.assertEqual(rec["status"], "human")
        self.assertTrue(any("red data" in s.get("note", "") for s in rec["steps"]))

    def test_red_data_may_use_a_local_adapter(self):
        rec = dc.decide(TASK, "Can we pay by bank transfer?", color="red",
                        adapters=[stub("local1", tier="local", provider="local", location="local", price=0)], state=self.state)
        self.assertEqual(rec["tier"], "local")

    def test_local_only_policy_keeps_green_data_local_too(self):
        dc.decide(TASK, "Can we pay?", policy={"privacy_level": "local-only"}, adapters=[stub()], state=self.state)
        self.assertEqual(self.calls(), 0)

    def test_zdr_policy_admits_only_zero_retention_and_takes_the_cheapest(self):
        adapters = [stub("plain", price=0.01), stub("zdr-dear", price=2.0, zdr=True), stub("zdr-cheap", price=0.5, zdr=True)]
        ok, refused = dc.tier_adapters(adapters, "cheap", {"privacy_level": "zdr"}, "green", 1000)
        self.assertEqual([a["id"] for a in ok], ["zdr-cheap", "zdr-dear"])
        self.assertEqual([a["id"] for a, _ in refused], ["plain"])

    def test_denied_and_not_allowed_providers(self):
        a = stub(provider="deepseek")
        self.assertIn("denies", dc.adapter_allowed(a, {"deny_providers": ["DeepSeek"]}, "green"))
        self.assertIn("allowed", dc.adapter_allowed(a, {"allowed_providers": ["anthropic"]}, "green"))
        self.assertIsNone(dc.adapter_allowed(stub(provider="local", location="local"), {"allowed_providers": ["anthropic"]}, "green"))

    def test_policy_lookup_order_starts_with_the_managed_copy(self):
        paths = dc.policy_paths("/proj")
        managed = dc.MANAGED_DIRS.get(sys.platform)
        if managed:
            self.assertEqual(paths[0], os.path.join(managed, dc.POLICY_NAME))
        self.assertIn(os.path.join("/proj", ".claude", dc.POLICY_NAME), paths)


class Budget(Env):
    def test_todays_budget_stops_paid_links(self):
        dc.append_journal(self.state, {"day": dc.today(), "cost_usd": 4.99, "task": "x"})
        rec = dc.decide(TASK, "Can we pay by bank transfer?", policy={"max_usd_per_day": 5}, adapters=[stub(price=50000)],
                        state=self.state)
        self.assertEqual(self.calls(), 0)
        self.assertEqual(rec["status"], "human")
        self.assertTrue(any("budget" in s.get("note", "") for s in rec["steps"]))

    def test_free_local_links_run_past_the_budget(self):
        dc.append_journal(self.state, {"day": dc.today(), "cost_usd": 99, "task": "x"})
        rec = dc.decide(TASK, "Can we pay?", policy={"max_usd_per_day": 5},
                        adapters=[stub("l", tier="local", provider="local", location="local", price=0)], state=self.state)
        self.assertEqual(rec["tier"], "local")


class Promote(Env):
    def test_agreeing_model_decisions_become_a_cache_entry(self):
        os.environ["STUB_CHOICE"] = "sales"
        text = "Can we pay by bank transfer?"
        for a in (stub(), stub("haiku", family="claude-haiku")):
            dc.append_journal(self.state, dc.decide(TASK, text, adapters=[a], state=self.state))
        dry = dc.promote(TASK, self.state)
        self.assertEqual(len(dry["promoted"]), 1)
        self.assertEqual(dc.read_cache(self.state, TASK["id"]), {})
        dc.promote(TASK, self.state, apply=True)
        rec = dc.decide(TASK, text, adapters=[stub()], state=self.state)
        self.assertEqual((rec["tier"], rec["cost_usd"]), ("cache", 0))

    def test_one_model_decision_or_a_split_vote_is_not_promoted(self):
        text = "Can we pay by bank transfer?"
        dc.append_journal(self.state, dc.decide(TASK, text, adapters=[stub()], state=self.state))
        self.assertEqual(dc.promote(TASK, self.state)["promoted"], [])
        os.environ["STUB_CHOICE"] = "sales"
        dc.append_journal(self.state, dc.decide(TASK, text, adapters=[stub()], state=self.state))
        self.assertEqual(dc.promote(TASK, self.state)["promoted"], [])

    def test_one_model_agreeing_with_itself_is_one_voice(self):
        # Haiku twice on the same repeat at 0.98 was twice wrong in the 2026-10-02 bench: not agreement.
        os.environ.update(STUB_CHOICE="technical", STUB_CONF="0.98")
        text = "I get logged out every five minutes"
        for _ in range(3):
            dc.append_journal(self.state, dc.decide(TASK, text, adapters=[stub("haiku", family="claude-haiku")],
                                                    state=self.state))
        self.assertEqual(dc.promote(TASK, self.state)["promoted"], [])

    def test_rule_ideas_come_from_words_seen_only_with_one_decision(self):
        for i in range(3):
            dc.append_journal(self.state, {"task": TASK["id"], "key": "k%d" % i, "decision": "sales",
                                           "status": "act", "tier": "cheap", "confidence": 0.99,
                                           "text": "wire transfer number %d" % i})
        ideas = dc.promote(TASK, self.state)["rule_suggestions"]
        self.assertIn({"label": "sales", "word": "wire", "seen": 3}, ideas)


class Bench(Env):
    def test_the_shipped_dataset_is_big_enough_and_labelled_with_options(self):
        rows = dc.read_dataset(os.path.join(ROOT, "data", "bench", "support-triage.jsonl"))
        self.assertGreaterEqual(len(rows), 50)
        self.assertTrue(all(r["label"] in TASK["options"] for r in rows))

    def test_bench_on_cache_and_rules_counts_every_example_once(self):
        rows = dc.read_dataset(os.path.join(ROOT, "data", "bench", "support-triage.jsonl"))
        res = dc.bench(TASK, rows, tiers=["cache", "rules"])
        decided = sum(v["decided"] for v in res["per_tier"].values())
        self.assertEqual(decided, len(rows))
        self.assertGreater(res["per_tier"]["cache"]["decided"], 0)      # repeats of a person's answers
        self.assertEqual(res["cost_usd_total"], 0)
        self.assertGreater(res["baseline"]["cost_usd_total"], 0)

    def test_missing_links_and_wrong_answers_are_said_plainly(self):
        os.environ["STUB_CHOICE"] = "account"
        rows = [{"id": "a", "input": "Can we pay by bank transfer?", "label": "sales"},
                {"id": "b", "input": "Is a wire ok?", "label": "billing"}]
        res = dc.bench(TASK, rows, tiers=["cache", "rules", "local", "cheap"], adapters=[stub()])
        self.assertEqual(res["accuracy_automatic"], 0.0)
        self.assertEqual(len(res["wrong"]), 2)
        joined = " ".join(res["negative_results"])
        self.assertIn("below", joined)
        self.assertIn("link local was asked for but has no adapter", joined)

    def test_a_chain_dearer_than_the_baseline_is_said(self):
        rows = [{"id": "a", "input": "Can we pay by bank transfer?", "label": "billing"}]
        res = dc.bench(TASK, rows, tiers=["cache", "rules", "cheap"], adapters=[stub(price=5000.0)])
        self.assertIn("more than the", " ".join(res["negative_results"]))

    def test_calibration_bins_and_error(self):
        bins, ece = dc.calibration([(0.99, True), (0.99, False), (0.8, True)])
        self.assertEqual([b["count"] for b in bins], [1, 2])
        self.assertAlmostEqual(ece, (abs(0.8 - 1) * 1 + abs(0.99 - 0.5) * 2) / 3, places=3)


class Cli(Env):
    def run_cli(self, *args, stdin=None):
        env = dict(os.environ, DECIDECALL_HOME=self.state, HOME=self.tmp)
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=stdin, capture_output=True, text=True,
                              env=env, cwd=self.tmp, timeout=120)

    def test_decide_label_then_cache(self):
        p = self.run_cli("--json", "decide", "--task", "support-triage", "--input", "Is a wire ok?")
        self.assertEqual(p.returncode, 0, p.stderr)
        rec = json.loads(p.stdout)
        self.assertEqual(rec["status"], "human")
        p = self.run_cli("label", "--id", rec["id"], "--answer", "billing")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_cli("--json", "decide", "--task", "support-triage", "--input", "is a WIRE ok")
        self.assertEqual(json.loads(p.stdout)["tier"], "cache")

    def test_label_refuses_an_answer_that_is_not_an_option(self):
        p = self.run_cli("--json", "decide", "--task", "support-triage", "--input", "Is a wire ok?")
        p = self.run_cli("label", "--id", json.loads(p.stdout)["id"], "--answer", "maybe")
        self.assertEqual(p.returncode, 2)

    def test_route_names_why_an_adapter_is_refused(self):
        path = os.path.join(self.tmp, "adapters.json")
        json.dump({"adapters": [{"id": "jev", "tier": "cheap", "kind": "systemone", "command": ["x"],
                                 "provider": "typesafe", "location": "cloud"}]}, open(path, "w"))
        p = self.run_cli("--adapters", path, "route", "--task", "support-triage", "--color", "red")
        self.assertIn("red data never leaves this computer", p.stdout)

    def test_bench_writes_its_report_in_the_policy_language(self):
        json.dump({"language": "es"}, open(os.path.join(self.tmp, "company-ai-policy.json"), "w"))
        p = self.run_cli("bench", "--task", "support-triage", "--tiers", "cache,rules", "--report", self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "prueba-de-decisiones.md")))

    def test_schema_and_candidates(self):
        schema = json.loads(self.run_cli("schema", "--task", "support-triage").stdout)
        self.assertEqual(schema["properties"]["decision"]["enum"], sorted(TASK["options"]) + ["none_of_these"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(self.run_cli("candidates").returncode, 0)

    def test_a_broken_policy_is_one_line_exit_2(self):
        open(os.path.join(self.tmp, "company-ai-policy.json"), "w").write("{nope")
        p = self.run_cli("decide", "--task", "support-triage", "--input", "x")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(len(p.stderr.strip().splitlines()), 1)


def embedder(**extra):
    e = {"id": "meaning", "kind": "embed", "model": "stub-embed", "command": [sys.executable, EMBED]}
    e.update(extra)
    return dc.check_adapter(e, "test")


class MeaningCache(Env):
    def setUp(self):
        super().setUp()
        dc.remember(self.state, TASK["id"], "I was charged twice this month", "billing", 1.0, "a person's answer", "green")

    def embeds(self):
        return open(self.embed_log).read().splitlines() if os.path.exists(self.embed_log) else []

    def test_the_same_question_in_other_words_is_found_by_meaning(self):
        text = "Why was my card billed two times?"
        self.assertEqual(dc.from_cache(TASK, text, dc.read_cache(self.state, TASK["id"]))["note"], "not in the cache")
        rec = dc.decide(TASK, text, adapters=[embedder()], state=self.state, tiers=["cache"])
        step = rec["steps"][0]
        self.assertEqual(step["decision"], "billing")
        self.assertIn("meaning, stub-embed", step["note"])
        self.assertEqual(self.calls(), 0)

    def test_a_meaning_match_never_acts_alone_and_a_second_source_settles_it(self):
        text = "Why was my card billed two times?"
        rec = dc.decide(TASK, text, adapters=[embedder()], state=self.state, tiers=["cache"])
        self.assertEqual(rec["status"], "human")
        self.assertEqual(rec["best_guess"]["decision"], "billing")
        os.environ["STUB_CONF"] = "0.80"
        rec = dc.decide(TASK, text, adapters=[embedder(), stub(id="j", tier="local", provider="local",
                                                               location="local", price=0)], state=self.state)
        self.assertEqual((rec["decision"], rec["status"]), ("billing", "verified"))
        self.assertEqual(rec["families"], ["cache", "j"])

    def test_a_similar_match_stays_below_even_a_low_act_gate(self):
        task = json.loads(json.dumps(TASK))
        task["thresholds"].update(act=0.5, verify=0.4)
        rec = dc.decide(task, "Why was my card billed two times?", adapters=[embedder()], state=self.state,
                        tiers=["cache"])
        self.assertEqual(rec["status"], "human")

    def test_vectors_are_kept_and_recomputed_only_for_a_new_model(self):
        for _ in range(2):
            dc.decide(TASK, "my card was billed two times", adapters=[embedder()], state=self.state, tiers=["cache"])
        self.assertEqual(self.embeds(), ["stub-embed 2", "stub-embed 1"])
        entry = next(iter(dc.read_cache(self.state, TASK["id"]).values()))
        self.assertEqual((entry["vec_model"], entry["vec_dims"]), ("stub-embed", 5))
        dc.decide(TASK, "my card was billed two times", adapters=[embedder(model="other")], state=self.state,
                  tiers=["cache"])
        self.assertEqual(self.embeds()[-1], "other 2")

    def test_no_server_falls_back_to_word_overlap(self):
        os.environ["EMBED_DOWN"] = "1"
        cache = dc.read_cache(self.state, TASK["id"])
        self.assertEqual(dc.from_cache(TASK, "I was charged twice this month!!! really", cache, embedder())["note"],
                         dc.from_cache(TASK, "I was charged twice this month!!! really", cache)["note"])
        self.assertIn("overlap", dc.from_cache(TASK, "I was charged twice this month again", cache, embedder())["note"])

    def test_the_v1_embeddings_shape_is_read_too(self):
        os.environ["EMBED_STYLE"] = "openai"
        res = dc.from_cache(TASK, "card billed two times", dc.read_cache(self.state, TASK["id"]), embedder())
        self.assertEqual(res["decision"], "billing")

    def test_a_refuted_entry_is_never_offered_again(self):
        text = "Why was my card billed two times?"
        rec = dc.decide(TASK, text, adapters=[embedder()], state=self.state, tiers=["cache"])
        cache = dc.read_cache(self.state, TASK["id"])
        dc.refute_similar(cache, rec, "sales")
        self.assertTrue(any(e.get("refuted") for e in cache.values()))
        self.assertNotIn("decision", dc.from_cache(TASK, text, cache, embedder()))

    def test_an_entry_may_carry_its_own_threshold(self):
        text = "card billed two times and then it crashes"
        cache = dc.read_cache(self.state, TASK["id"])
        self.assertEqual(dc.from_cache(TASK, text, cache, embedder())["decision"], "billing")
        for e in cache.values():
            e["similar_min"] = 0.97
        self.assertNotIn("decision", dc.from_cache(TASK, text, cache, embedder()))

    def test_an_embedder_must_be_local(self):
        with self.assertRaises(dc.Problem):
            embedder(location="cloud")


class StrictShape(Env):
    def test_ollama_kind_sends_the_schema_as_format(self):
        req = os.path.join(self.tmp, "req")
        os.environ.update(STUB_KIND="ollama", STUB_REQUEST=req, STUB_CHOICE="sales", STUB_REASON="asks about seats")
        a = stub("ol", tier="local", provider="local", location="local", price=0, kind="ollama", model="qwen")
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[a], state=self.state)
        self.assertEqual((rec["decision"], rec["reason"]), ("sales", "asks about seats"))
        body = json.load(open(req))
        self.assertEqual(body["format"], dc.output_schema(TASK))
        self.assertFalse(body["stream"])

    def test_openai_chat_kind_sends_a_strict_json_schema(self):
        req = os.path.join(self.tmp, "req")
        os.environ.update(STUB_KIND="chat", STUB_REQUEST=req, STUB_CHOICE="sales")
        a = stub("lc", tier="local", provider="local", location="local", price=0, kind="openai-chat")
        rec = dc.decide(TASK, "Can we pay by bank transfer?", adapters=[a], state=self.state)
        self.assertEqual(rec["decision"], "sales")
        rf = json.load(open(req))["response_format"]
        self.assertEqual((rf["type"], rf["json_schema"]["strict"]), ("json_schema", True))

    def test_none_of_these_sends_the_decision_to_a_person(self):
        os.environ.update(STUB_KIND="json", STUB_CHOICE="none_of_these")
        rec = dc.decide(TASK, "Can we pay by bank transfer?",
                        adapters=[stub("haiku", kind="decision-json"), stub("opus", tier="strong", kind="decision-json")],
                        state=self.state)
        self.assertEqual(rec["status"], "human")
        self.assertEqual(rec["none_of_these"]["adapter"], "haiku")
        self.assertEqual(self.calls(), 1)

    def test_systemone_confidence_is_the_probability_of_the_choice(self):
        os.environ.update(STUB_CONF="0.99", STUB_PROBS=json.dumps({"billing": 0.8, "sales": 0.1, "account": 0.05,
                                                                   "technical": 0.05}))
        res = dc.run_adapter(stub(), TASK, "Can we pay by bank transfer?")
        self.assertEqual(res["confidence"], 0.8)

    def test_keychain_secrets_reach_the_adapter_environment_only(self):
        a = stub(keychain={"DC_TEST_SECRET": "dc-test"})
        orig = dc.keychain_secret
        dc.keychain_secret = lambda service: "s3cret" if service == "dc-test" else None
        try:
            self.assertEqual(dc.adapter_env(a)["DC_TEST_SECRET"], "s3cret")
        finally:
            dc.keychain_secret = orig
        self.assertNotIn("DC_TEST_SECRET", os.environ)


class Data(unittest.TestCase):
    def test_every_candidate_names_its_source_day_and_status(self):
        doc = json.load(open(os.path.join(ROOT, "data", "candidates.json")))
        ids = set()
        for c in doc["candidates"]:
            ids.add(c["id"])
            self.assertTrue(c["url"].startswith("https://"), c["id"])
            self.assertRegex(c["checked"], r"^2026-\d\d-\d\d$")
            self.assertIn(c["status"], ("verified", "secondary", "unverified"))
            self.assertTrue(c.get("license"), c["id"])
        for must in ("strands-decider", "openjev", "jevk5", "typesafe-jev", "llm-typesafe", "setfit", "outlines",
                     "xgrammar", "baml", "opensmartroute", "agent-model-router"):
            self.assertIn(must, ids)

    def test_the_example_adapters_load(self):
        adapters, _ = dc.load_adapters(os.path.join(ROOT, "data", "examples", "decidecall-adapters.example.json"))
        self.assertEqual({a["tier"] for a in adapters}, {"embed", "local", "cheap", "strong"})

    def test_every_dictionary_has_every_word(self):
        en = json.load(open(os.path.join(ROOT, "lang", "en.json")))
        keys = set(en) - {"_purpose", "name"}
        for code in ("es", "pt", "ru", "uk"):
            other = json.load(open(os.path.join(ROOT, "lang", "%s.json" % code), encoding="utf-8"))
            self.assertEqual(keys - set(other), set(), code)


class NoNetwork(unittest.TestCase):
    def test_no_network_module_in_the_plugin_code(self):
        pattern = re.compile(r"^\s*(?:import|from)\s+(urllib|http\.client|http|socket|requests|httpx|ftplib|smtplib)\b", re.M)
        for folder, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "tests")]
            for name in names:
                if name.endswith(".py"):
                    text = open(os.path.join(folder, name), encoding="utf-8").read()
                    self.assertIsNone(pattern.search(text), os.path.join(folder, name))

    def test_adapters_run_without_a_shell(self):
        text = open(SCRIPT, encoding="utf-8").read()
        self.assertNotIn("shell=True", text)

    def test_no_example_adapter_starts_a_shell_or_holds_a_key(self):
        doc = json.load(open(os.path.join(ROOT, "data", "examples", "decidecall-adapters.example.json")))
        for a in doc["adapters"]:
            self.assertNotIn(os.path.basename(a["command"][0]), ("sh", "bash", "zsh", "cmd", "powershell"), a["id"])
            self.assertNotIn("$", " ".join(a["command"]), a["id"])


if __name__ == "__main__":
    unittest.main()
