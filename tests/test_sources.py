"""v0.2 source combinations: 0..N Hyperliquid addresses, optional Robinhood (1..N accounts),
report scopes, trader profile, share card and both label languages.  All data is generated."""
import datetime as dt
import json
import math
import os
import random
import re
import shutil
import tempfile
import unittest

from lsl import card, compute, hl, page, persona, profile, scope
from lsl.util import UTC
from tests.fakes import FakeHL, FakeRH, drive, eq_order
from tests.test_hl import ADDR, OTHER, fill

NOW = "2026-01-01T00:00:00Z"
ACCTS = [{"account_number": "TEST0001", "rhs_account_number": "RHS1", "brokerage_account_type": "individual"},
         {"account_number": "TEST0002", "rhs_account_number": "RHS2", "brokerage_account_type": "ira_roth"}]
OUT_FILES = ("data_quality.json", "open_positions.json", "page.json", "series.json", "summary.json")


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def rh_orders(rng, acct, sym, month, n=4, base=10.0):
    out = []
    for i in range(n):
        d = "2025-%02d-%02dT15:00:00" % (month, 3 + i * 5)
        out.append(eq_order(rng, T(d), sym, "buy", 10, base))
        out.append(eq_order(rng, T(d.replace("T15", "T18")), sym, "sell", 10, base + (1 if i % 3 else -0.5)))
    return out


def add_rh(run_dir, accounts=ACCTS):
    rng = random.Random(7)
    orders = {}
    for i, a in enumerate(accounts):
        orders[(a["account_number"], "equity")] = rh_orders(rng, a["account_number"], "TSR%d" % i, 3 + i)
    res, _ = drive(run_dir, FakeRH(orders, accounts=accounts, pnl_from="2030-01"), now=NOW)
    assert res["status"] == "done", res


def hl_fills(t0, coin, wins=(4, 2, -3, 5)):
    out, tid = [], t0 // 1000
    for i, p in enumerate(wins):
        t = t0 + i * 86400000 * 3
        out.append(fill(t, tid + 2 * i, coin=coin, px="10", sz="2"))
        out.append(fill(t + 3600000, tid + 2 * i + 1, coin=coin, px="12", sz="2", side="A", dir="Close Long",
                        closedPnl=str(p)))
    return out


def add_hl(run_dir, addresses=(ADDR,)):
    for i, a in enumerate(addresses):
        hl.fetch(run_dir, a, NOW, post=FakeHL(hl_fills(1735700000000 + i * 7200000, "TS%d" % i)))


def no_nan(testcase, path):
    for f in OUT_FILES:
        with open(os.path.join(path, f)) as fh:
            txt = fh.read()
        testcase.assertNotRegex(txt, r"\bNaN\b|Infinity", f)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def compute(self, scope_id="all", lang="en", out=None):
        res = compute.run(self.d, out, lang, scope_id)
        self.assertEqual(res.get("status"), "done", res)
        with open(os.path.join(res["out"], "summary.json")) as f:
            sm = json.load(f)
        with open(os.path.join(res["out"], "page.json")) as f:
            pg = json.load(f)
        return res, sm, pg


class Combinations(Base):
    def check(self, rh, hl_n):
        if rh:
            add_rh(self.d)
        if hl_n:
            add_hl(self.d, (ADDR, OTHER)[:hl_n])
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            no_nan(self, res["out"])
            self.assertEqual(res["out"], os.path.join(os.path.abspath(self.d), "out", "all"))
            self.assertEqual((pg["has"]["rh"], pg["has"]["hl"], pg["has"]["both"]), (rh, bool(hl_n), rh and bool(hl_n)))
            self.assertEqual(pg["lang"], lang)
            self.assertTrue(1 <= len(pg["tldr"]) <= 5)
            self.assertEqual(pg["data_quality"]["hl"] is None, not hl_n)
            ids = [h["id"] for h in pg["headline"]]
            self.assertEqual("net_funding" in ids, bool(hl_n))
            venues = [v["key"] for v in pg["profile"]["venues"]]
            self.assertEqual(venues, ["all"] + (["rh", "hl"] if rh and hl_n else []))
            # one combined report; accounts broken out inside it (section hidden for one account)
            n_acct = (len(ACCTS) if rh else 0) + hl_n
            self.assertEqual(sm["accounts"]["count"], n_acct)
            self.assertTrue(sm["accounts"]["reconciliation"]["all_match"], sm["accounts"]["reconciliation"])
            self.assertEqual(pg["accounts"]["show"], n_acct > 1)
            self.assertEqual(pg["has"]["accounts"], n_acct > 1)
            self.assertEqual(len(pg["accounts"]["rows"]), n_acct if n_acct > 1 else 0)
            self.assertEqual(len(pg["card"]["accounts"]), n_acct if n_acct > 1 else 0)
            self.assertEqual(sm["profile"]["all"]["closed_trades"], sm["overall"]["closed_trades"])
            # card: every listed period renders, for both money modes; output is deterministic
            self.assertTrue(pg["card"]["periods"])
            for k, s in pg["card"]["variants"].items():
                self.assertTrue(s.startswith("<svg") and s.rstrip().endswith("</svg>"), k)
            r1 = card.run(res["out"], lang=lang)
            self.assertEqual(r1["status"], "done", r1)
            with open(r1["svg"], "rb") as f:
                a = f.read()
            card.run(res["out"], lang=lang)
            with open(r1["svg"], "rb") as f:
                self.assertEqual(f.read(), a)
            self.assertEqual(a.decode(), pg["card"]["variants"]["all.usd"])
            with open(os.path.join(res["out"], "series.json")) as f:
                se = json.load(f)
            pct_only = card.svg(card.data(sm, se), lang, percent_only=True)
            self.assertNotIn("$", pct_only)
        return sm, pg

    def test_rh_only(self):
        sm, pg = self.check(True, 0)
        self.assertEqual(sorted(sm["sources"].items()), [("hl", False), ("rh", True)])

    def test_hl_only(self):
        sm, pg = self.check(False, 1)
        self.assertNotIn("rh", sm["scopes"])

    def test_hl_multi_only(self):
        sm, pg = self.check(False, 2)
        self.assertTrue(pg["has"]["hl_multi"])
        self.assertEqual(len(sm["hl_addresses"]), 2)

    def test_both(self):
        self.check(True, 2)

    def test_zero_sources(self):
        res = compute.run(self.d, None, "en")
        self.assertIn("error", res)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out")))
        self.assertIn("error", card.run(os.path.join(self.d, "out", "all")))


    def test_thin_history(self):
        """One winning trade only: card still renders (metrics as "-"), TL;DR names no fake "worst"."""
        hl.fetch(self.d, ADDR, NOW, post=FakeHL(hl_fills(1735700000000, "TS0", wins=(5,))))
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            no_nan(self, res["out"])
            self.assertEqual(card.available(sm), ["all"])
            r = card.run(res["out"], lang=lang)
            self.assertEqual(r["status"], "done", r)
            text = " ".join(pg["tldr"])
            self.assertNotIn("Worst", text)
            self.assertNotIn("最亏", text)
            self.assertIsNone(sm["profile"]["all"]["periods"]["all"]["sharpe"])


class MultiAddress(Base):
    def test_merge(self):
        add_hl(self.d, (ADDR, OTHER))
        _, sm, _ = self.compute()
        parts = [v for k, v in sm["scopes"].items() if k.startswith("hl.")]
        self.assertEqual(len(parts), 2)
        for key in ("closed_trades", "wins", "fills"):
            self.assertEqual(sm["scopes"]["hl"][key], sum(p[key] for p in parts), key)
        self.assertAlmostEqual(sm["scopes"]["hl"]["net_pnl"], math.fsum(p["net_pnl"] for p in parts), places=6)
        # nothing in outputs carries a full address
        for f in OUT_FILES:
            with open(os.path.join(self.d, "out", "all", f)) as fh:
                txt = fh.read().lower()
            self.assertNotIn(ADDR[2:], txt)
            self.assertNotIn(OTHER[2:], txt)

    def test_duplicate_and_invalid(self):
        self.assertEqual(hl.add(self.d, ADDR).get("status"), "added")
        dup = hl.add(self.d, ADDR.upper().replace("0X", "0x"))
        self.assertNotEqual(dup.get("status"), "added")
        self.assertEqual(len(hl.load_config(self.d)), 1)
        self.assertIn("error", hl.add(self.d, "0x1234"))
        self.assertIn("error", hl.add(self.d, "not-an-address"))
        self.assertEqual(len(hl.load_config(self.d)), 1)
        hl.remove(self.d, ADDR)
        self.assertEqual(hl.load_config(self.d), [])

    def test_incremental_equals_full(self):
        later = "2026-03-01T00:00:00Z"
        f1 = hl_fills(1735700000000, "TS0")
        f2 = f1 + hl_fills(1767300000000, "TS0", wins=(1, -1))
        g = hl_fills(1735707200000, "TS1")
        inc, full = os.path.join(self.d, "inc"), os.path.join(self.d, "full")
        hl.add(inc, ADDR), hl.add(inc, OTHER)
        api = {ADDR: FakeHL(f1, page=3), OTHER: FakeHL(g, page=3)}
        for a in (ADDR, OTHER):
            hl.fetch(inc, a, NOW, post=api[a])
        api[ADDR] = FakeHL(f2, page=3)
        for a in (ADDR, OTHER):
            hl.fetch(inc, a, later, post=api[a])
        for a, fl in ((ADDR, f2), (OTHER, g)):
            hl.fetch(full, a, later, post=FakeHL(fl, page=3))
        ri = compute.run(inc, None, "en")
        rf = compute.run(full, None, "en")
        for f in ("summary.json", "series.json"):
            with open(os.path.join(ri["out"], f), "rb") as a, open(os.path.join(rf["out"], f), "rb") as b:
                self.assertEqual(a.read(), b.read(), f)

    def test_legacy_flat_layout(self):
        add_hl(self.d, (ADDR,))
        root = os.path.join(self.d, "state", "hl")
        src = hl.addr_dir(self.d, ADDR)
        for name in os.listdir(src):
            shutil.move(os.path.join(src, name), os.path.join(root, name))
        os.rmdir(src)
        os.remove(os.path.join(root, "addresses.json"))
        legacy = hl.load(self.d)
        self.assertEqual(len(legacy), 1)
        self.assertIsNone(legacy[0]["short"])
        _, sm_old, _ = self.compute()
        hl.add(self.d, ADDR)  # adopts the flat files
        self.assertEqual(len(hl.load(self.d)), 1)
        self.assertTrue(os.path.isdir(hl.addr_dir(self.d, ADDR)))
        _, sm_new, _ = self.compute()
        self.assertEqual(sm_old["overall"], sm_new["overall"])


class Scopes(Base):
    def setUp(self):
        super().setUp()
        add_rh(self.d)
        add_hl(self.d, (ADDR, OTHER))

    def test_available_and_masking(self):
        src = compute.sources(self.d)
        ids = [s["id"] for s in src["scopes"]]
        self.assertEqual(ids[:3], ["all", "rh", "rh:a0"])
        self.assertIn("hl", ids)
        self.assertEqual(sum(1 for i in ids if i.startswith("hl:")), 2)
        labels = [s["label"] for s in src["scopes"]]
        self.assertIn("Robinhood Roth IRA …0002", labels)
        self.assertIn(hl.short(ADDR), labels)

    def test_subsets_add_up(self):
        res_all, sm_all, _ = self.compute("all")
        _, sm_rh, pg_rh = self.compute("rh")
        _, sm_hl, _ = self.compute("hl")
        accts = [self.compute(s)[1] for s in ("rh:a0", "rh:0002")]
        addrs = [self.compute("hl:" + a)[1] for a in (ADDR, OTHER)]
        key = ("closed_trades", "wins", "fills", "losses")
        for k in key:
            self.assertEqual(sm_all["overall"][k], sm_rh["overall"][k] + sm_hl["overall"][k], k)
            self.assertEqual(sm_rh["overall"][k], sum(s["overall"][k] for s in accts), k)
            self.assertEqual(sm_hl["overall"][k], sum(s["overall"][k] for s in addrs), k)
        for k in ("net_pnl", "fees", "funding"):
            self.assertAlmostEqual(sm_all["overall"][k], sm_rh["overall"][k] + sm_hl["overall"][k], places=6)
            self.assertAlmostEqual(sm_rh["overall"][k], math.fsum(s["overall"][k] for s in accts), places=6)
            self.assertAlmostEqual(sm_hl["overall"][k], math.fsum(s["overall"][k] for s in addrs), places=6)
        # a scope equals the merge it filters: rh scope == rh part of the combined report
        self.assertEqual(sm_rh["scopes"]["rh"], sm_all["scopes"]["rh"])
        self.assertEqual(sm_hl["scopes"]["hl"], sm_all["scopes"]["hl"])
        self.assertEqual(pg_rh["scope"], {"id": "rh", "kind": "rh", "label": "Robinhood"})
        self.assertFalse(pg_rh["has"]["hl"])
        self.assertEqual([s["scope"]["label"] for s in accts], ["Robinhood Individual …0001",
                                                                 "Robinhood Roth IRA …0002"])
        self.assertEqual(addrs[0]["scope"]["label"], hl.short(ADDR))

    def test_scope_dirs_coexist_and_never_leak(self):
        outs = {}
        for s in ("all", "rh", "rh:a1", "hl", "hl:" + OTHER):
            res = compute.run(self.d, None, "en", s)
            outs[s] = res["out"]
            card.run(res["out"])
        self.assertEqual(len(set(outs.values())), 5)
        self.assertEqual(os.path.basename(outs["rh:a1"]), "rh-a1")
        self.assertEqual(compute.scope_out(self.d, "hl:" + OTHER), outs["hl:" + OTHER])
        for o in outs.values():
            for f in OUT_FILES + ("share-card.svg",):
                with open(os.path.join(o, f)) as fh:
                    txt = fh.read()
                for secret in ("TEST0001", "TEST0002", "RHS1", "RHS2", ADDR[2:], OTHER[2:]):
                    self.assertNotIn(secret, txt, (o, f))
        # the card names its scope
        with open(os.path.join(outs["rh:a1"], "share-card.svg")) as fh:
            self.assertIn("Robinhood Roth IRA …0002", fh.read())

    def test_bad_scope(self):
        for s in ("rh:9999", "hl:0x" + "ef" * 20, "venue:x"):
            res = compute.run(self.d, None, "en", s)
            self.assertIn("error", res, s)
            self.assertIn("all", res["scopes"])
        d2 = tempfile.mkdtemp()
        try:
            add_hl(d2, (ADDR,))
            self.assertIn("error", compute.run(d2, None, "en", "rh"))
        finally:
            shutil.rmtree(d2)


class ProfileFormulas(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual([profile.style(s) for s in (59, 3600, 86399, 86400, 30 * 86400)],
                         ["SCALPER", "DAY_TRADER", "DAY_TRADER", "SWING", "POSITION"])
        self.assertEqual([profile.pnl_cohort(x) for x in (-100000, -99999, -100.01, -100, 99.99, 100, 100000)],
                         ["REKT", "UNPROFITABLE", "UNPROFITABLE", "BREAKEVEN", "BREAKEVEN", "PROFITABLE",
                          "BIG_WINNER"])
        self.assertEqual([profile.size_cohort(x) for x in (99, 100, 999, 1000, 10000)],
                         ["SHRIMP", "FISH", "FISH", "DOLPHIN", "WHALE"])
        self.assertIsNone(profile.style(None))

    def test_formulas(self):
        v = [10.0, -5.0, 0.0, 20.0, -10.0, 5.0]
        self.assertEqual(profile.drawdown(v), (-10.0, 25.0))
        m = sum(v) / 6
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / 6)
        self.assertEqual(profile.sharpe(v), round(m / sd * math.sqrt(365), 2))
        dd = math.sqrt((25 + 100) / 6)
        self.assertEqual(profile.sortino(v), round(m / dd * math.sqrt(365), 2))
        self.assertIsNone(profile.sharpe([1.0, 2.0, 0, 0]))           # < MIN_ACTIVE_DAYS active days
        self.assertIsNone(profile.sortino([1.0] * 6))                  # no downside
        self.assertEqual(profile.streak([1, 1, 0, 1, 1, 1, 0]), 3)
        self.assertEqual(profile.median([3, 1, 2, 10]), 2.5)

    def test_profile_and_periods(self):
        def tr(day, pnl, side="long", hold=7200):
            return {"src": "hl", "acct": "hl", "cls": "perp", "sym": "X", "ts": day + "T00:00:00Z", "day": day,
                    "pnl": pnl, "hold_s": hold, "side": side, "liquidation": False, "approx": False}
        trades = [tr("2025-12-%02d" % d, p) for d, p in ((1, 10), (2, 10), (3, -5), (20, 4), (28, 6), (30, -1))]
        trades.append(tr("2025-12-29", 2, "short"))
        acts = [{"day": t["day"], "fee": 0.5, "notional": 500.0} for t in trades]
        p = profile.profile(trades, acts, 1.5, "2025-12-31")
        self.assertEqual((p["closed_trades"], p["wins"], p["losses"]), (7, 5, 2))
        self.assertEqual(p["strip"], "WWLWWWL")
        self.assertEqual(p["win_streak_trades"], 3)
        self.assertEqual(p["net_pnl"], 26 - 3.5)
        self.assertEqual(p["all_time_pnl"], 26 - 3.5 + 1.5)
        self.assertEqual(p["style"], "DAY_TRADER")
        self.assertEqual(p["size_cohort"], "FISH")
        self.assertEqual(p["direction"]["bias"], "LONG")
        per = p["periods"]
        self.assertEqual(per["7d"]["closed_trades"], 3)
        self.assertTrue(per["7d"]["enough"])
        self.assertTrue(per["30d"]["enough"])
        self.assertEqual(per["30d"]["closed_trades"], 6)  # window 12-02..12-31
        self.assertFalse(per["90d"]["enough"])               # history starts after the window opens
        self.assertIsNone(per["90d"]["sharpe"])
        self.assertTrue(per["all"]["enough"])
        self.assertEqual(per["all"]["volume"], 3500.0)
        self.assertEqual(per["all"]["return_on_volume"], round(22.5 / 3500, 4))


class Labels(unittest.TestCase):
    def keys(self, d, pre=""):
        out = set()
        for k, v in d.items():
            out.add(pre + k)
            if isinstance(v, dict):
                out |= self.keys(v, pre + k + ".")
        return out

    def test_parity(self):
        en, zh = persona.labels("en"), persona.labels("zh")
        self.assertEqual(self.keys(en), self.keys(zh))
        for k, v in en["page"].items():
            self.assertEqual(k, v)  # en is the identity map

    def test_page_strings_translated(self):
        with open(page.__file__, encoding="utf-8") as f:
            src = f.read()
        used = {"".join(json.loads(x) for x in re.findall(r'"[^"]*"', m))
                for m in re.findall(r'_\(((?:"[^"]*"\s*)+)\)', src)}
        zh = persona.labels("zh")["page"]
        self.assertEqual(sorted(u for u in used if u not in zh), [])
        for code in card.L("en")["badges"]:
            self.assertIn(code, card.L("zh")["badges"])

    def test_placeholders_match(self):
        en, zh = persona.labels("en"), persona.labels("zh")

        def walk(a, b, path):
            if isinstance(a, dict):
                for k in a:
                    walk(a[k], b[k], path + "." + k)
            elif isinstance(a, str):
                self.assertEqual(sorted(re.findall(r"%[sd]|\{\w+\}", a)), sorted(re.findall(r"%[sd]|\{\w+\}", b)),
                                 path)
        walk(en, zh, "")
        for k, v in zh["page"].items():
            self.assertEqual(re.findall(r"%[sd]", k), re.findall(r"%[sd]", v), k)


if __name__ == "__main__":
    unittest.main()
