"""v0.2.1 By-account breakdown: reconciliation to the cent, ordering, cross-account insights,
per-account share cards.  All data is generated."""
import json
import math
import os
import random

import unittest

from lsl import accounts, card, hl, persona
from tests.fakes import FakeHL
from tests.test_hl import ADDR, OTHER, fill
from tests.test_sources import NOW, Base, add_hl, add_rh, hl_fills


def short_fills(t0, coin, pnls):
    out, tid = [], t0 // 1000 + 500
    for i, p in enumerate(pnls):
        t = t0 + i * 86400000 * 3
        out.append(fill(t, tid + 2 * i, coin=coin, px="12", sz="2", side="A", dir="Open Short"))
        out.append(fill(t + 3600000, tid + 2 * i + 1, coin=coin, px="10", sz="2", side="B", dir="Close Short",
                        closedPnl=str(p)))
    return out


def load(out, name):
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return json.load(f)


class Allocate(unittest.TestCase):
    def test_exact_sum(self):
        rng = random.Random(3)
        for _ in range(300):
            raws = [rng.uniform(-5000, 5000) for _ in range(rng.randint(1, 7))]
            total = round(math.fsum(raws), 2)
            out = accounts.allocate(raws, total)
            self.assertEqual(int(round(math.fsum(x * 100 for x in out))), int(round(total * 100)))
            for a, b in zip(raws, out):
                self.assertLessEqual(abs(a - b), 0.01 + 1e-9)

    def test_ties_go_to_earlier_row(self):
        self.assertEqual(accounts.allocate([0.005, 0.005], 0.01), [0.01, 0.0])
        self.assertEqual(accounts.allocate([], 0), [])


class Breakdown(Base):
    def setUp(self):
        super().setUp()
        add_rh(self.d)
        add_hl(self.d, (ADDR, OTHER))

    def test_reconcile_and_order(self):
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            A = sm["accounts"]
            self.assertEqual(A["count"], 4)
            self.assertTrue(A["reconciliation"]["all_match"], A["reconciliation"])
            o = sm["overall"]
            for k in ("net_pnl", "net_pnl_incl_funding", "fees"):
                self.assertEqual(int(round(math.fsum(r[k] * 100 for r in A["rows"]))), int(round(o[k] * 100)), k)
            for k in ("fills", "closed_trades", "wins"):
                self.assertEqual(sum(r[k] for r in A["rows"]), o[k], k)
            self.assertEqual(int(round(math.fsum(r["volume"] * 100 for r in A["rows"]))),
                             int(round(sm["profile"]["all"]["volume"] * 100)))
            # each row matches its own scope within the cent it may have been rounded by
            for r in A["rows"]:
                s = sm["scopes"][r["key"]]
                self.assertLessEqual(abs(r["net_pnl"] - s["net_pnl"]), 0.01, r["key"])
                self.assertEqual((r["closed_trades"], r["fills"]), (s["closed_trades"], s["fills"]), r["key"])
            keys = [(-abs(r["net_pnl_incl_funding"]), r["label"], r["key"]) for r in A["rows"]]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual({r["key"] for r in A["rows"]}, {"rh.a0", "rh.a1"} | {k for k in sm["scopes"] if k.startswith("hl.")})
            # page rows follow the same order, localized labels, nothing secret
            self.assertEqual([r["key"] for r in pg["accounts"]["rows"]], [r["key"] for r in A["rows"]])
            if lang == "zh":
                self.assertTrue(any("罗斯" in r["label"] or "Robinhood" in r["label"] for r in pg["accounts"]["rows"]))
            txt = json.dumps(pg, ensure_ascii=False)
            for secret in ("TEST0001", "TEST0002", "RHS1", "RHS2", ADDR[2:], OTHER[2:]):
                self.assertNotIn(secret, txt)
            # TL;DR names the accounts, stays <= 5 lines
            self.assertLessEqual(len(pg["tldr"]), 5)
            self.assertTrue(any(str(A["count"]) in line and ("account" in line or "账户" in line) for line in pg["tldr"]))

    def test_zh_wording(self):
        # the Chinese Page reads as Chinese: document language, full-width punctuation, no stray spaces
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            self.assertEqual(pg["html_lang"], "zh-Hans" if lang == "zh" else "en")
            txt = json.dumps(pg, ensure_ascii=False)
            ends = "。" if lang == "zh" else "."
            for ins in pg["accounts"]["insights"]:
                self.assertTrue(ins["body"].endswith(ends), ins["body"])
            if lang != "zh":
                continue
            self.assertNotIn("传统IRA", txt)
            self.assertNotIn("笔交易, ", txt)
            self.assertNotIn("亏了 -$", txt)
            for line in pg["tldr"]:
                self.assertNotIn("。 ", line)

    def test_per_account_cards(self):
        res, sm, pg = self.compute()
        out = res["out"]
        r1 = card.run(out, per_account_cards=True)
        self.assertEqual(r1["status"], "done", r1)
        rows = sm["accounts"]["rows"]
        self.assertEqual([c["account"] for c in r1["cards"]], [r["key"] for r in rows])
        made = [c for c in r1["cards"] if "svg" in c]
        self.assertEqual(sorted(os.listdir(os.path.join(out, "cards"))), sorted(c["slug"] + ".svg" for c in made))
        self.assertTrue(made)
        blobs = {}
        for c in made:
            with open(c["svg"], "rb") as f:
                blobs[c["slug"]] = f.read()
        card.run(out, per_account_cards=True)
        for c in made:
            with open(c["svg"], "rb") as f:
                self.assertEqual(f.read(), blobs[c["slug"]], c["slug"])  # deterministic
        se = load(out, "series.json")
        for c, r in ((c, next(r for r in rows if r["key"] == c["account"])) for c in made):
            s = blobs[c["slug"]].decode()
            self.assertIn(r["label"].replace("&", "&amp;"), s)
            d = card.data(sm, se, "all", r["key"])
            self.assertEqual(d["scope_kind"], "account")
            self.assertEqual(d["sources"], [r["venue"]])
            # the card's all-time P&L (before funding) is the account row's net, to the rounding cent
            self.assertLessEqual(abs(d["p"]["net_pnl"] - r["net_pnl"]), 0.011, r["key"])
            self.assertEqual((d["p"]["closed_trades"], d["p"]["win_rate"]), (r["closed_trades"], r["win_rate"]))
            pa = next(x for x in pg["card"]["accounts"] if x["key"] == r["key"])
            self.assertEqual(pa["variants"]["all"], s)
            self.assertNotIn("$", s)
            # per-account persona (all-time, that account's own trades) is what the card shows
            pz = sm["scopes"][r["key"]]["persona"]
            self.assertEqual({k: d["persona"][k] for k in ("archetype", "grade", "modifiers", "ratings")},
                             {k: pz[k] for k in ("archetype", "grade", "modifiers", "ratings")})
            self.assertIn(persona.labels("en")["archetypes"][pz["archetype"]].replace("&", "&amp;"), s)
            for secret in ("TEST0001", "TEST0002", ADDR[2:], OTHER[2:]):
                self.assertNotIn(secret, s)
        with open(r1["svg"], encoding="utf-8") as f:
            self.assertIn("4 accounts", f.read())
        zh = card.run(out, lang="zh", per_account_cards=True)
        with open(zh["svg"], encoding="utf-8") as f:
            self.assertIn("4 个账户", f.read())

    def test_compact_page(self):
        from lsl import page as page_mod
        for lang in ("en", "zh"):
            res, sm, full = self.compute(lang=lang)
            out = res["out"]
            r = page_mod.run(out, lang, compact_=True)
            self.assertTrue(r["compact"])
            with open(os.path.join(out, "page.json"), "rb") as f:
                b1 = f.read()
            page_mod.run(out, lang, compact_=True)
            with open(os.path.join(out, "page.json"), "rb") as f:
                self.assertEqual(f.read(), b1)  # deterministic
            pg = json.loads(b1)
            self.assertEqual(r["inline_bytes"], len(json.dumps(pg, ensure_ascii=False, separators=(",", ":"),
                                                                sort_keys=True).encode()))
            self.assertTrue(pg["compact"] and pg["card"]["compact"])
            self.assertFalse(full["compact"])
            per = full["card"]["default"]["period"]
            # the one card kept is byte-identical to the full payload's; per-account cards move to chat/CLI
            self.assertEqual(pg["card"]["variants"], {per: full["card"]["variants"][per]})
            self.assertEqual(set(pg["card"]["sizes"]), {per})
            self.assertEqual(pg["card"]["accounts"], [])
            self.assertEqual([x["id"] for x in pg["card"]["periods"]], [per])
            # numbers the page shows are unchanged; only lists are shortened
            for k in ("tldr", "headline", "years", "journey", "persona", "profile", "data_quality", "ui"):
                self.assertEqual(pg[k], full[k], k)
            self.assertEqual([r_["key"] for r_ in pg["accounts"]["rows"]], [r_["key"] for r_ in full["accounts"]["rows"]])
            for a, b in zip(pg["accounts"]["rows"], full["accounts"]["rows"]):
                self.assertEqual({k: v for k, v in a.items() if k != "spark"}, {k: v for k, v in b.items() if k != "spark"})
                self.assertLessEqual(len(a["spark"]["values"]), page_mod.COMPACT["spark_points"])
                self.assertEqual(a["spark"]["values"][-1], b["spark"]["values"][-1])
            G = pg["assets"]["groups"]
            self.assertLessEqual(len(G["multi_venue"]), page_mod.COMPACT["groups_multi"])
            self.assertEqual({x["group"] for x in G["rows"]}, set(G["best"] + G["worst"] + G["multi_venue"]))
            self.assertEqual(G["best"], full["assets"]["groups"]["best"][:len(G["best"])])
            P, FP = pg["positions"], full["positions"]
            self.assertEqual(len(P["rh"]) + P["rh_more"], len(FP["rh"]))
            self.assertLess(r["inline_bytes"], len(json.dumps(full, ensure_ascii=False, separators=(",", ":")).encode()))

    def test_card_period_and_skip(self):
        res, sm, _ = self.compute()
        r = card.run(res["out"], period="7d", per_account_cards=True)
        if "error" in r:
            self.assertNotIn("cards", r)
            return
        for c in r["cards"]:
            self.assertEqual("7d" in c["available_periods"], "svg" in c, c)


class Insights(Base):
    def test_opposing_and_offsets(self):
        hl.fetch(self.d, ADDR, NOW, post=FakeHL(hl_fills(1735700000000, "TS0", wins=(9, 8, 7, 6))))
        hl.fetch(self.d, OTHER, NOW, post=FakeHL(short_fills(1735707200000, "TS0", (-5, -4, -3, 1))))
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            I = sm["accounts"]["insights"]
            self.assertEqual(I["opposing_count"], 1)
            o = I["opposing"][0]
            self.assertEqual(o["symbol"], "TS0")
            self.assertEqual([x["key"] for x in o["long"]], [k for k in sm["scopes"] if k.startswith("hl.") and
                                                             sm["scopes"][k]["net_pnl"] > 0])
            self.assertEqual(len(o["short"]), 1)
            self.assertEqual(I["shared_assets"], 1)
            off = I["offsets"]
            rows = sm["accounts"]["rows"]
            self.assertEqual(off["gross_gain"], rows[0]["net_pnl_incl_funding"])
            self.assertAlmostEqual(off["given_back"], round(-off["gross_loss"] / off["gross_gain"], 4))
            self.assertEqual(off["losers"][0]["share_of_gains"], off["given_back"])
            kinds = [x["kind"] for x in pg["accounts"]["insights"]]
            self.assertEqual(kinds, ["opposing", "offsets"])
            for x in pg["accounts"]["insights"]:
                self.assertNotRegex(x["lead"] + x["body"], r"\{\w+\}")

    def test_same_direction_no_conflict(self):
        add_hl(self.d, (ADDR, OTHER))  # both long, both winning, different coins
        _, sm, pg = self.compute()
        I = sm["accounts"]["insights"]
        self.assertEqual((I["opposing"], I["offsets"], I["shared_assets"]), ([], None, 0))
        self.assertEqual(pg["accounts"]["insights"], [])
        self.assertTrue(pg["accounts"]["none"])

    def test_min_trades_rule(self):
        hl.fetch(self.d, ADDR, NOW, post=FakeHL(hl_fills(1735700000000, "TS0", wins=(9, 8, 7, 6))))
        hl.fetch(self.d, OTHER, NOW, post=FakeHL(short_fills(1735707200000, "TS0", (-5, -4))))  # 2 < MIN_DIR_TRADES
        _, sm, _ = self.compute()
        self.assertEqual(sm["accounts"]["insights"]["opposing"], [])


class SingleAccount(Base):
    def test_hidden(self):
        add_hl(self.d, (ADDR,))
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            self.assertEqual(sm["accounts"]["count"], 1)
            self.assertTrue(sm["accounts"]["reconciliation"]["all_match"])
            self.assertFalse(pg["accounts"]["show"])
            self.assertEqual(pg["card"]["accounts"], [])
            r = card.run(res["out"], lang=lang, per_account_cards=True)
            self.assertEqual(r["cards"], [])
            self.assertFalse(os.path.exists(os.path.join(res["out"], "cards")))
            with open(r["svg"], encoding="utf-8") as f:
                self.assertNotRegex(f.read(), r"\d+ (accounts|个账户)")
            self.assertFalse(any("account" in l or "账户" in l for l in pg["tldr"]))


if __name__ == "__main__":
    unittest.main()
