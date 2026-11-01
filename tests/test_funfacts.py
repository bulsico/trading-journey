"""v0.2.10 share-card fun facts: script-computed, no dollars, deterministic, hidden when the data is
missing, and every number they print passes the copy.json validator.  All data is generated."""
import datetime as dt
import json
import os
import re

from lsl import card, funfacts
from tests.test_sources import ACCTS, Base, add_hl, add_rh
from tests.test_hl import ADDR, OTHER

UTC = dt.timezone.utc


def tr(day, sym="AAA", pnl=1.0, src="hl", cls="perp", acct="hl", side="long", hold_s=3600, liq=False):
    return {"src": src, "acct": acct, "cls": cls, "sym": sym, "day": day, "pnl": pnl, "side": side,
            "hold_s": hold_s, "liquidation": liq,
            "ts": dt.datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=UTC)}


def fill(day, src="hl", acct="hl", cls="perp", sym="AAA"):
    return {"day": day, "src": src, "acct": acct, "cls": cls, "sym": sym}


class Unit(Base):
    def test_hidden_when_missing(self):
        self.assertEqual(funfacts.facts([], [], "2025-01-01", "2025-12-31"), {})
        # one RH equity trade, no hold time, no fills: only the facts that exist
        f = funfacts.facts([tr("2025-02-03", src="rh", cls="equity", acct="a0", hold_s=None)], [],
                           "2025-01-01", "2025-12-31")
        self.assertEqual(sorted(f), ["long_short", "most_traded", "streaks"])
        for k in ("time_held", "liquidated", "loved", "hated", "venues", "top_account", "classes", "busiest_day",
                  "weekday"):
            self.assertNotIn(k, f)
        d = {"fun": f, "account_labels": {}}
        keys = [c[0] for c in card.fun_cells(d, "en")]
        self.assertEqual(keys, ["most_traded", "long_short", "streaks"])
        # nothing at all -> no fun-facts section on the card
        self.assertEqual(card.fun_cells({"fun": {}}, "zh"), [])

    def test_values(self):
        t = [tr("2025-03-0%d" % i, sym="$AAA" if i % 2 else "BBB/USDC", cls="perp" if i % 2 else "spot",
                pnl=1 if i < 5 else -1, side="short" if i == 1 else "long", hold_s=60 * i, liq=(i == 7))
             for i in range(1, 9)]
        fl = [fill(x["day"]) for x in t] + [fill("2025-03-03")]
        f = funfacts.facts(t, fl, "2025-01-01", "2025-12-31")
        self.assertEqual(f["most_traded"], {"ticker": "AAA", "trades": 4, "share": 0.5})
        self.assertEqual(f["long_short"]["short_share"], 0.125)
        self.assertEqual(f["streaks"], {"streak_w": 4, "streak_l": 4})
        self.assertEqual(f["time_held"]["median_hold"], {"num": 4, "unit": "m"})  # (4m + 5m)/2 rounds to 4
        self.assertEqual(f["liquidated"], {"trades": 1})
        self.assertEqual(f["busiest_day"], {"date": "2025-03-03", "fills": 2})
        self.assertIn("weekday", f)
        self.assertNotIn("classes", f)  # spot + perp are both crypto
        # liquidations: zero is a real fact on a perp scope, shown as such
        z = funfacts.facts([tr("2025-03-01")], [], "2025-01-01", "2025-12-31")
        self.assertEqual(z["liquidated"], {"trades": 0})

    def test_window(self):
        t = [tr("2025-01-05", sym="OLD"), tr("2025-06-05", sym="NEW")]
        f = funfacts.facts(t, [], "2025-06-01", "2025-06-30")
        self.assertEqual(f["most_traded"]["ticker"], "NEW")


class Mixes(Base):
    def check(self, rh, hl_n):
        if rh:
            add_rh(self.d)
        if hl_n:
            add_hl(self.d, (ADDR, OTHER)[:hl_n])
        for lang in ("en", "zh"):
            res, sm, pg = self.compute(lang=lang)
            with open(os.path.join(res["out"], "series.json")) as f:
                se = json.load(f)
            self.assertEqual(sorted(sm["fun"]), sorted(["all"] + list(sm["scopes"])))
            res2, sm2, _ = self.compute(lang=lang)
            self.assertEqual(sm["fun"], sm2["fun"])  # deterministic
            allf = sm["fun"]["all"]["all"]
            self.assertEqual("venues" in allf, bool(rh and hl_n))
            self.assertEqual("liquidated" in allf, bool(hl_n))
            n_acct = (len(ACCTS) if rh else 0) + hl_n
            if not (rh and hl_n):
                self.assertEqual("top_account" in allf, n_acct > 1)
            self.assertNotIn("$", json.dumps(sm["fun"]))
            T = card.L(lang)
            self.assertGreaterEqual(len(card.fun_cells(card.data(sm, se, "all", "all"), lang)), 4)
            for key in ["all"] + sorted(sm["scopes"]):
                for per in card.available(sm, key):
                    d = card.data(sm, se, per, key)
                    cells = card.fun_cells(d, lang)
                    self.assertLessEqual(len(cells), card.FUN_MAX)
                    s = card.svg(d, lang)
                    self.assertNotIn("$", s)
                    self.assertEqual(s, card.svg(card.data(sm, se, per, key), lang))
                    self.assertEqual(bool(cells), card.esc(T["h_fun"][1]) in s)
                    for c in cells:
                        self.assertIn(card.esc(c[2]), s)
                    # every number on a fun-fact cell passes the copy.json validator
                    for c in cells:
                        # (the go-to account cell's value is the account's display name, masked last 4)
                        for txt in ((None if c[0] == "top_account" else c[3]), c[4]):
                            if not txt:
                                continue
                            txt = re.sub(r"\b[A-Za-z]+\d+\b", "X", txt)  # synthetic tickers like TSR0 / TS1
                            probs = card.check_text(txt, card.allowed_numbers(sm), lang, "question", 9999, 10)
                            self.assertEqual([p for p in probs if "number" in p or "date" in p], [], (key, per, txt))

    def test_rh_only(self):
        self.check(True, 0)

    def test_hl_only(self):
        self.check(False, 1)

    def test_hl_multi(self):
        self.check(False, 2)

    def test_both(self):
        self.check(True, 2)
