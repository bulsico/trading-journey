"""End to end: fake RH + fake HL -> compute.  Checks the gains policy and that
outputs are byte-identical across runs."""
import datetime as dt
import os
import random
import shutil
import tempfile
import unittest

import json

from lsl import compute, hl, page
from lsl.util import UTC
from tests.fakes import FakeHL, FakeRH, drive, eq_order, option_order
from tests.test_hl import ADDR, fill

NOW = "2026-01-01T00:00:00Z"
FILES = ("data_quality.json", "open_positions.json", "page.json", "series.json", "summary.json")


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def build(run_dir):
    rng = random.Random(42)
    eq = [eq_order(rng, T("2024-06-03T15:00:00"), "TSTA", "buy", 10, 10.0),
          eq_order(rng, T("2024-07-03T15:00:00"), "TSTA", "sell", 10, 12.0),     # before coverage -> FIFO
          eq_order(rng, T("2025-01-10T15:00:00"), "TSTA", "buy", 5, 10.0),
          eq_order(rng, T("2025-02-03T15:00:00"), "TSTA", "sell", 5, 11.0),      # RH says +12.34
          eq_order(rng, T("2025-03-03T15:00:00"), "TSTC", "buy", 2, 50.0)]       # still open
    opt = [option_order(rng, T("2025-03-03T15:00:00"), "TSTB", "o1", "2025-03-07", "buy", "open", 1, 1.0)]
    hist = {"TSTA": [{"timestamp": "2025-02-03T15:00:00Z", "side": "sell", "symbol": "TSTA",
                      "quantity": "5", "price": "11", "realized_gain": "12.34"}],
            "TSTB": [{"timestamp": "2025-03-07T21:00:00Z", "side": "", "symbol": "TSTB",
                      "quantity": "1", "price": "0", "realized_gain": "-100"}]}
    pos = {"equity": [{"symbol": "TSTC", "quantity": "2", "instrument_id": "inst-TSTC"}]}
    srv = FakeRH({("TESTACCT1", "equity"): eq, ("TESTACCT1", "option"): opt}, hist=hist,
                 pnl_from="2025-01", positions=pos)
    res, _ = drive(run_dir, srv, now=NOW)
    assert res["status"] == "done"
    fills = [fill(1700000000000, 1, coin="TST", px="10", sz="2"),
             fill(1700000100000, 2, coin="TST", px="12", sz="2", side="A", dir="Close Long", closedPnl="4"),
             fill(1700000200000, 3, coin="TST", px="12", sz="1", side="A", dir="Open Short"),
             fill(1700000300000, 4, coin="TST", px="15", sz="1", side="B", dir="Close Short", closedPnl="-3",
                  liquidation={"liquidatedUser": ADDR, "markPx": "15", "method": "market"})]
    funding = [{"time": 1700000150000, "hash": "0x0",
                "delta": {"type": "funding", "coin": "TST", "usdc": "-0.5", "szi": "2"}}]
    hl.fetch(run_dir, ADDR, NOW, post=FakeHL(fills, funding=funding))


class ComputeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        build(cls.tmp)
        cls.res = compute.run(cls.tmp, os.path.join(cls.tmp, "out1"), "zh")
        cls.docs = compute.compute_all(compute.load_rh(cls.tmp), compute.load_hl(cls.tmp), "zh")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_gains_policy(self):
        s = self.docs["summary.json"]
        g = s["scopes"]["rh"]["gain_sources"]
        self.assertEqual(g["fifo"], {"trades": 1, "share": 0.3333, "realized_pnl": 20.0})
        self.assertEqual(g["rh"]["trades"], 2)
        self.assertEqual(g["rh"]["realized_pnl"], 12.34 - 100)
        self.assertEqual(s["approximate"]["trades"], 1)
        dq = self.docs["data_quality.json"]["rh"]["accounts"][0]
        self.assertEqual(dq["gains"]["rh_rows_matched"], 2)
        self.assertEqual(dq["coverage_start"], "2025-02")

    def test_hl(self):
        h = self.docs["summary.json"]["scopes"]["hl"]
        self.assertEqual((h["closed_trades"], h["wins"], h["liquidations"]), (2, 1, 1))
        self.assertEqual(h["funding"], -0.5)

    def test_checks_and_positions(self):
        dq = self.docs["data_quality.json"]
        self.assertTrue(dq["pass"], [c for c in dq["checks"] if not c["pass"]])
        self.assertEqual(self.docs["summary.json"]["open_positions"]["rh"], 1)

    def test_page(self):
        out = os.path.join(self.tmp, "out1")
        with open(os.path.join(out, "page.json"), "rb") as f:
            from_compute = f.read()
        # re-derivable from the four outputs alone (raw state not needed), byte-identical
        self.assertEqual(page.run(out, "zh")["status"], "done")
        with open(os.path.join(out, "page.json"), "rb") as f:
            self.assertEqual(f.read(), from_compute)
        p = json.loads(from_compute)
        j = p["journey"]
        self.assertEqual(len(j["labels"]), len(j["cumulative_k"]["all"]))
        self.assertEqual(sorted(j["cumulative_k"]), ["all", "hl", "rh"])
        for w in j["windows"]:
            vals = [v for s in j["cumulative_k"].values() for v in s[w["start"]:]]
            self.assertLessEqual(max(vals), w["ceiling"])
            self.assertGreaterEqual(min(vals), w["floor"])
        # forward-filled cumulative ends at the summary's net P&L
        self.assertEqual(j["cumulative_k"]["all"][-1],
                         round(self.docs["summary.json"]["overall"]["net_pnl"] / 1000.0, 2))
        self.assertEqual(p["headline"][0]["exact"], page.money(self.docs["summary.json"]["overall"]["net_pnl"], True))
        self.assertEqual(p["positions"]["counts"]["rh"], 1)
        self.assertTrue(all(f in p["flag_labels"] for r in p["positions"]["rh"] for f in r["flags"]))
        self.assertTrue(p["data_quality"]["pass"])
        g = p["assets"]["groups"]
        keys = [r["group"] for r in g["rows"]]
        self.assertEqual(keys, sorted(set(keys)))  # each group row stored once
        for name in ("best", "worst", "multi_venue"):
            self.assertTrue(set(g[name]) <= set(keys), name)
        self.assertEqual(len(g["multi_venue"]), min(g["multi_venue_count"], page.MULTI_TOP_N))
        self.assertTrue(all(r["venues"] for r in g["rows"]))
        en = page.build(*(self.docs[n] for n in ("summary.json", "series.json", "open_positions.json",
                                                   "data_quality.json")), lang="en")
        self.assertEqual(en["persona"]["archetype_label"],
                         json.load(open(os.path.join(os.path.dirname(page.__file__), "labels", "en.json")))
                         ["archetypes"][en["persona"]["archetype"]])

    def test_page_format(self):
        self.assertEqual(page.money(-1234.5), "-$1,234.50")
        self.assertEqual(page.money(2.0, True), "+$2.00")
        self.assertEqual(page.money_k(-115338.54, True), "-$115.3K")
        self.assertEqual(page.money_k(2500000), "$2.50M")
        self.assertEqual(page.pct(0.6182), "61.8%")
        self.assertEqual(page.nice_up(252.32), 300.0)
        self.assertEqual(page.axis([-151.52, 252.69]), {"ceiling": 300.0, "floor": -200.0})
        self.assertEqual(page.month_seq("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_byte_identical(self):
        out2 = os.path.join(self.tmp, "out2")
        compute.run(self.tmp, out2, "zh")
        for f in FILES:
            with open(os.path.join(self.tmp, "out1", f), "rb") as a, open(os.path.join(out2, f), "rb") as b:
                self.assertEqual(a.read(), b.read(), f)
        # a fully independent fetch + compute gives the same bytes too
        other = tempfile.mkdtemp()
        try:
            build(other)
            compute.run(other, os.path.join(other, "out"), "zh")
            for f in FILES:
                with open(os.path.join(self.tmp, "out1", f), "rb") as a, \
                        open(os.path.join(other, "out", f), "rb") as b:
                    self.assertEqual(a.read(), b.read(), f)
        finally:
            shutil.rmtree(other)


if __name__ == "__main__":
    unittest.main()
