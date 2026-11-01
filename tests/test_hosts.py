"""Language prefs, data-dir resolution and the self-contained HTML report.

Fixtures are synthetic. The HTML report must be byte-identical across runs and
must not rewrite page.json.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

from lsl import card, cli, compute, htmlreport, persona, prefs
from lsl.util import dumps
from tests.test_hl import ADDR
from tests.test_sources import add_hl, add_rh


def run_cli(argv, environ=None):
    buf = io.StringIO()
    old_out, old_env = sys.stdout, os.environ
    if environ is not None:
        os.environ = environ
    sys.stdout = buf
    try:
        rc = cli.main(argv)
    finally:
        sys.stdout = old_out
        if environ is not None:
            os.environ = old_env
    text = buf.getvalue()
    return rc, json.loads(text), text


class DataDirTest(unittest.TestCase):
    def test_resolution_order(self):
        legacy = os.path.expanduser("~/trading-journey-data")
        local = os.path.expanduser("~/.trading-journey")
        nowhere = lambda p: False
        self.assertEqual(cli.data_dir_info(environ={}, isdir=nowhere),
                         {"data_dir": local, "source": "default"})
        self.assertEqual(cli.data_dir_info(environ={}, isdir=lambda p: p == legacy)["source"], "legacy")
        self.assertEqual(cli.data_dir_info(environ={}, isdir=lambda p: p == legacy)["data_dir"], legacy)
        # an explicit env wins even when the legacy directory exists
        info = cli.data_dir_info(environ={"LSL_DATA_DIR": "~/custom-data"}, isdir=lambda p: True)
        self.assertEqual(info["source"], "env")
        self.assertEqual(info["data_dir"], os.path.expanduser("~/custom-data"))
        self.assertEqual(cli.default_data_dir(environ={}, isdir=nowhere), local)

    def test_cli_flag_beats_env(self):
        env = dict(os.environ, LSL_DATA_DIR="/env/data")
        rc, res, _ = run_cli(["data-dir", "--data-dir", "/flag/data"], env)
        self.assertEqual(rc, 0)
        self.assertEqual(res["source"], "flag")
        self.assertEqual(res["data_dir"], "/flag/data")
        self.assertEqual(res["version"], "0.3.0")
        rc, res, _ = run_cli(["data-dir"], env)
        self.assertEqual(res["source"], "env")
        self.assertEqual(res["data_dir"], "/env/data")


class PrefsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_label_keys_match(self):
        en, zh = persona.labels("en"), persona.labels("zh")
        for sec in ("html", "prefs"):
            self.assertEqual(sorted(en[sec]), sorted(zh[sec]), sec)
            for key in en[sec]:
                self.assertEqual(set(re.findall(r"\{(\w+)\}", en[sec][key])),
                                 set(re.findall(r"\{(\w+)\}", zh[sec][key])), "%s.%s" % (sec, key))

    def test_ask_then_set_is_deterministic(self):
        res = prefs.show(self.d, {})
        self.assertEqual(res["status"], "ask")
        self.assertEqual(res["reason"], "unset")
        self.assertIsNone(res["lang"])
        self.assertIn("报告语言", res["question"])
        self.assertEqual([o["id"] for o in res["options"]], ["en", "zh", "system"])
        saved = prefs.set_choice(self.d, "zh", {})
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["lang"], "zh")
        with open(prefs.path(self.d), encoding="utf-8") as f:
            self.assertEqual(f.read(), dumps({"lang": "zh"}))
        again = prefs.set_choice(self.d, "zh", {})
        with open(prefs.path(self.d), encoding="utf-8") as f:
            self.assertEqual(f.read(), dumps({"lang": "zh"}))
        self.assertEqual(again["lang"], "zh")
        # language lives at the data-dir root, not under state/
        self.assertFalse(prefs.path(self.d).startswith(os.path.join(self.d, "state") + os.sep))
        self.assertEqual(prefs.set_choice(self.d, "fr")["error"][:4], "lang")

    def test_system_locale(self):
        prefs.set_choice(self.d, "system", {"LANG": "zh_CN.UTF-8"})
        zh = prefs.show(self.d, {"LANG": "zh_CN.UTF-8"})
        self.assertEqual((zh["status"], zh["lang"], zh["note"]), ("ready", "zh", None))
        # resolved again on each read, so a locale change is picked up without rewriting the file
        en = prefs.show(self.d, {"LC_ALL": "en_US.UTF-8", "LANG": "zh_CN.UTF-8"})
        self.assertEqual(en["lang"], "en")
        self.assertIsNone(en["note"])
        other = prefs.show(self.d, {"LANG": "ja_JP.UTF-8"})
        self.assertEqual(other["lang"], "en")
        self.assertEqual(other["reason"], "unsupported")
        self.assertIn("ja_JP", other["note"])
        self.assertIn("暂不支持", other["note"])
        for loc in ("", "C", "POSIX", "C.UTF-8"):
            missing = prefs.show(self.d, {"LANG": loc})
            self.assertEqual(missing["status"], "ask", loc)
            self.assertEqual(missing["reason"], "no_locale", loc)
            self.assertIsNone(missing["lang"])
        # the file stores the choice, never the resolved language
        with open(prefs.path(self.d), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"lang": "system"})

    def test_corrupt_file(self):
        with open(prefs.path(self.d), "w", encoding="utf-8") as f:
            f.write("{")
        self.assertIn("error", prefs.show(self.d, {}))

    def test_purge_keeps_language(self):
        prefs.set_choice(self.d, "en", {})
        os.makedirs(os.path.join(self.d, "state"))
        cli.purge(self.d, "all", yes=True)
        self.assertFalse(os.path.exists(os.path.join(self.d, "state")))
        self.assertEqual(prefs.show(self.d, {})["lang"], "en")

    def test_cli(self):
        rc, res, _ = run_cli(["prefs", "--data-dir", self.d])
        self.assertEqual((rc, res["status"]), (0, "ask"))
        rc, res, _ = run_cli(["prefs", "set", "--data-dir", self.d])
        self.assertEqual(rc, 1)
        self.assertIn("error", res)
        rc, res, _ = run_cli(["prefs", "set", "--lang", "en", "--data-dir", self.d])
        self.assertEqual((rc, res["lang"]), (0, "en"))


class HtmlTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def compute(self, lang="en"):
        res = compute.run(self.d, None, lang, "all")
        self.assertEqual(res.get("status"), "done", res)
        return res["out"]

    def test_render_escapes_and_is_stable(self):
        page = {"lang": "en", "html_lang": "en", "scope": {"id": "all", "label": "All"},
                "period": {"display": "a to b", "days": 3}, "as_of_display": "2026-01-01 00:00 UTC",
                "tldr": ["a < b & c"], "has": {}, "headline": []}
        first = htmlreport.render(page, [], [], None, "0.3.0")
        self.assertEqual(first, htmlreport.render(page, [], [], None, "0.3.0"))
        self.assertIn("a &lt; b &amp; c", first)
        self.assertIn("No share cards", first)
        self.assertIn("trading-journey 0.3.0", first)
        self.assertNotIn("https://", first)
        self.assertTrue(first.startswith("<!DOCTYPE html>\n"))
        self.assertTrue(first.endswith("</html>\n"))
        self.assertEqual(first.count("<main>"), 1)
        self.assertEqual(first.count("</main>"), 1)

    def test_png_only_is_inlined(self):
        import base64
        add_hl(self.d, (ADDR,))
        out = self.compute("en")
        png = os.path.join(out, "share-card-1-stats.png")
        raw = b"\x89PNG\r\n\x1a\n" + b"synthetic-png"
        with open(png, "wb") as f:
            f.write(raw)
        res = htmlreport.run(out)
        self.assertEqual(res["pngs"], [os.path.abspath(png)])
        self.assertTrue(res["cards"][0]["embedded"])
        with open(res["out"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn(base64.b64encode(raw).decode("ascii"), text)
        with open(res["out"], "rb") as f:
            first = f.read()
        htmlreport.run(out)
        with open(res["out"], "rb") as f:
            self.assertEqual(f.read(), first)

    def test_sources_cards_and_determinism(self):
        cases = (("hl", False, True), ("rh", True, False), ("both", True, True))
        for name, rh, hl in cases:
            shutil.rmtree(self.d)
            self.d = tempfile.mkdtemp()
            if rh:
                add_rh(self.d)
            if hl:
                add_hl(self.d, (ADDR,))
            out = self.compute("en")
            with open(os.path.join(out, "page.json"), "rb") as f:
                before = f.read()
            card.run(out, lang="en")
            res = htmlreport.run(out)
            self.assertEqual(res.get("status"), "done", (name, res))
            self.assertTrue(res["cards"] and all(c["embedded"] for c in res["cards"]), name)
            with open(res["out"], encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("https://", text, name)
            self.assertNotRegex(text, r"\bNaN\b|Infinity", name)
            self.assertIn("TL;DR", text, name)
            self.assertIn('width="1080"', text, name)
            for c in res["cards"]:
                if c["svg"]:
                    with open(c["svg"], encoding="utf-8") as f:
                        svg = f.read()
                    self.assertIn(svg, text)
                    self.assertNotIn("$", svg)
            with open(os.path.join(out, "page.json"), "rb") as f:
                self.assertEqual(f.read(), before, name)
            again = htmlreport.run(out)
            self.assertEqual(again, res, name)
            with open(res["out"], "rb") as f:
                first = f.read()
            htmlreport.run(out)
            with open(res["out"], "rb") as f:
                self.assertEqual(f.read(), first, name)
            heading = "<h2>By account</h2>"
            if rh:
                self.assertIn(heading, text, name)  # two Robinhood accounts
            else:
                self.assertNotIn(heading, text, name)  # one Hyperliquid address

    def test_chinese_and_dropped_card(self):
        add_rh(self.d)
        out = self.compute("zh")
        card.run(out, lang="zh")
        res = htmlreport.run(out)
        with open(res["out"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("交易旅程", text)
        self.assertIn("<h2>分账户</h2>", text)
        self.assertIn('lang="zh-Hans"', text)
        facts = os.path.join(out, "share-card-2-facts.svg")
        self.assertTrue(os.path.exists(facts), res["cards"])
        os.remove(facts)
        res = htmlreport.run(out)
        self.assertNotIn("share-card-2-facts", [c["id"] for c in res["cards"]])
        self.assertIn("share-card-1-stats", [c["id"] for c in res["cards"]])
        # rebuilding in the other language does not rewrite page.json
        with open(os.path.join(out, "page.json"), "rb") as f:
            before = f.read()
        en = htmlreport.run(out, lang="en")
        self.assertEqual(en["lang"], "en")
        with open(en["out"], encoding="utf-8") as f:
            self.assertIn("TL;DR", f.read())
        with open(os.path.join(out, "page.json"), "rb") as f:
            self.assertEqual(f.read(), before)

    def test_per_account_and_copy(self):
        add_rh(self.d)
        out = self.compute("en")
        card.run(out, lang="en", per_account_cards=True)
        copy = {"persona": "Steady and patient.", "verdict": "Keep the log.",
                "rules": ["Wait. Then act.", "Size small. Stay in.", "One market. Learn it."],
                "question": "What repeats?"}
        path = os.path.join(out, "card-copy.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(copy, f)
        res = htmlreport.run(out)
        self.assertEqual(res["copy"], "card-copy.json")
        with open(res["out"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Steady and patient.", text)
        self.assertIn("Per-account cards", text)
        self.assertTrue(res["accounts"])
        self.assertTrue(res["pngs"] == [] or all(p.endswith(".png") for p in res["pngs"]))
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"persona": "$5"}')
        bad = htmlreport.run(out)
        self.assertIn("problems", bad)
        # a rejected copy does not replace the previous report
        with open(res["out"], encoding="utf-8") as f:
            self.assertIn("Steady and patient.", f.read())

    def test_missing_inputs(self):
        empty = os.path.join(self.d, "empty")
        os.makedirs(empty)
        res = htmlreport.run(empty)
        self.assertIn("summary", res["error"])
        rc, body, _ = run_cli(["html", "--data-dir", self.d, "--scope", "all"])
        self.assertEqual(rc, 1)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
