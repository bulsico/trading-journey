"""Small pure functions: cursors, page classification, DST, FIFO, expiry, persona."""
import datetime as dt
import json
import unittest

from lsl import fifo, persona, rhmodel
from lsl.rhpage import (COMPLETE, EMPTY, ERROR, MANGLED, TRUNC_MARK, TRUNCATED, classify,
                        decode_cursor, make_cursor, mangled_head)
from lsl.util import UTC, et_day, et_month, et_offset_hours, et_wall_to_utc, to_utc

ID = "0123abcd-0000-4000-8000-00000000beef"


def _page(recs, cut=None):
    body = json.dumps({"data": {"orders": recs}}, separators=(",", ":"))
    if cut is None:
        return "result\n" + body + "\n"
    return "result\n" + body[:cut] + "\n" + TRUNC_MARK + "\n"


class RhPageTest(unittest.TestCase):
    def test_cursor_roundtrip(self):
        c = make_cursor("2025-03-04T05:06:07.000008Z")
        self.assertNotIn("=", c)
        self.assertEqual(decode_cursor(c), "p=2025-03-04 05:06:07.000008+00:00")

    def test_classify(self):
        recs = [{"id": ID, "created_at": "2025-01-01T00:00:00Z", "x": "y" * 50},
                {"id": ID.replace("beef", "bee2"), "created_at": "2025-01-01T00:00:00Z", "x": "y" * 50}]
        self.assertEqual(classify(_page([]), "orders")[0], EMPTY)
        self.assertEqual(classify(_page(recs), "orders")[0], COMPLETE)
        st, got, _ = classify(_page(recs, cut=len(json.dumps(recs[0])) + 40), "orders")
        self.assertEqual((st, len(got)), (TRUNCATED, 1))
        mangled = _page(recs, cut=70)
        self.assertEqual(classify(mangled, "orders")[0], MANGLED)
        self.assertEqual(mangled_head(mangled, ("orders",)), ID)
        self.assertEqual(classify("Error: boom", "orders")[0], ERROR)
        self.assertEqual(classify(None, "orders")[0], ERROR)
        # an empty list that was cut is never EMPTY
        self.assertEqual(classify('{"data":{"orders":[' + "\n" + TRUNC_MARK, "orders")[0], MANGLED)


class DstTest(unittest.TestCase):
    def test_offsets(self):
        self.assertEqual(et_offset_hours(to_utc("2026-03-08T06:59:59Z")), -5)
        self.assertEqual(et_offset_hours(to_utc("2026-03-08T07:00:00Z")), -4)
        self.assertEqual(et_offset_hours(to_utc("2026-11-01T05:59:59Z")), -4)
        self.assertEqual(et_offset_hours(to_utc("2026-11-01T06:00:00Z")), -5)

    def test_days(self):
        self.assertEqual(et_day("2026-07-01T03:59:00Z"), "2026-06-30")
        self.assertEqual(et_day("2026-07-01T04:00:00Z"), "2026-07-01")
        self.assertEqual(et_month("2026-01-01T04:59:00Z"), "2025-12")
        self.assertEqual(et_wall_to_utc("2026-01-16", 16), dt.datetime(2026, 1, 16, 21, tzinfo=UTC))
        self.assertEqual(et_wall_to_utc("2026-07-17", 16), dt.datetime(2026, 7, 17, 20, tzinfo=UTC))


def _f(ts, side, qty, px, eff=None, fee=0.0, **kw):
    d = {"ts": to_utc(ts), "side": side, "qty": qty, "px": px, "eff": eff, "fee": fee, "ref": ts}
    d.update(kw)
    return d


class FifoTest(unittest.TestCase):
    def test_long_partial_closes(self):
        closes, lots = fifo.match([
            _f("2025-01-01T00:00:00Z", "buy", 10, 10.0, fee=1.0),
            _f("2025-01-02T00:00:00Z", "buy", 10, 20.0),
            _f("2025-01-03T00:00:00Z", "sell", 15, 30.0, fee=1.5)])
        self.assertEqual(len(closes), 1)
        c = closes[0]
        self.assertAlmostEqual(c["gross"], 10 * 20 + 5 * 10)
        self.assertAlmostEqual(c["fees"], 1.0 + 1.5)
        self.assertAlmostEqual(c["hold_s"], (10 * 2 * 86400 + 5 * 86400) / 15)
        self.assertAlmostEqual(fifo.net_open(lots), 5)

    def test_unmatched_and_shorts(self):
        closes, _ = fifo.match([_f("2025-01-01T00:00:00Z", "sell", 3, 5.0)])
        self.assertTrue(closes[0]["unmatched"])
        closes, lots = fifo.match([_f("2025-01-01T00:00:00Z", "sell", 3, 5.0, shorts=True),
                                   _f("2025-01-02T00:00:00Z", "buy", 3, 4.0, shorts=True)])
        self.assertEqual(closes[0]["side"], "short")
        self.assertAlmostEqual(closes[0]["gross"], 3.0)
        self.assertEqual(lots, [])

    def test_option_effects(self):
        closes, lots = fifo.match([_f("2025-01-01T00:00:00Z", "sell", 100, 2.0, eff="open"),
                                   _f("2025-01-02T00:00:00Z", "buy", 100, 1.0, eff="close")])
        self.assertAlmostEqual(closes[0]["gross"], 100.0)
        closes, _ = fifo.match([_f("2025-01-02T00:00:00Z", "buy", 100, 1.0, eff="close")])
        self.assertTrue(closes[0]["unmatched"])

    def test_expiry_closes(self):
        fills = [{"ts": "2025-01-06T15:00:00Z", "acct": "a0", "cls": "option", "key": "o1", "sym": "TSTB",
                  "exp": "2025-01-10", "side": "buy", "qty": 100.0, "px": 1.0, "ref": "r1", "eff": "open"},
                 {"ts": "2025-01-06T15:00:00Z", "acct": "a0", "cls": "option", "key": "o2", "sym": "TSTB",
                  "exp": "2026-06-19", "side": "buy", "qty": 100.0, "px": 1.0, "ref": "r2", "eff": "open"}]
        out = rhmodel.add_expiry_closes(fills, "2026-01-01T00:00:00Z")
        exp = [f for f in out if f.get("expiry")]
        self.assertEqual(len(exp), 1)                    # o2 has not expired yet
        self.assertEqual(to_utc(exp[0]["ts"]), to_utc("2025-01-10T21:00:00Z"))
        self.assertEqual((exp[0]["side"], exp[0]["px"], exp[0]["eff"]), ("sell", 0.0, "close"))


class PersonaTest(unittest.TestCase):
    BASE = {"fills": 200, "closed": 80, "fpd": 2.0, "win_rate": 0.6, "avg_win": 50.0, "avg_loss": -20.0,
            "unique_assets": 4, "concentration": 0.5, "liq_rate": 0.0,
            "monthly_fill_counts": [10, 10, 10], "net": 1000.0}

    def test_trend_master_and_grade(self):
        p = persona.persona(self.BASE, "en")
        # tm = (0.6-0.4)*200 + 50/20*10 = 65
        self.assertEqual(p["scores"]["trend_master"], 65)
        self.assertEqual(p["archetype"], "TREND_MASTER")
        self.assertEqual(p["grade"], "S")
        self.assertEqual(set(p["ratings"]), {"patience", "fomo_resistance", "stop_loss_discipline",
                                             "trend_awareness", "resilience"})

    def test_modifiers_and_ties(self):
        st = dict(self.BASE, net=-10.0, avg_win=10.0, avg_loss=-30.0, fpd=12.0)
        p = persona.persona(st, "zh")
        self.assertIn("STOP_LOSS_PHOBIA", p["modifiers"])
        self.assertIn("TREND_AWAKENING", p["modifiers"])
        sc = {"overtrade": 40, "knife_catcher": 40, "scatter_gun": 40, "panic_trader": 40,
              "diamond_hands": 40, "trend_master": 0}
        self.assertEqual(persona.archetype(sc, 0.4, -1, 1, 1)[0], "OVERTRADE")   # stable tie order

    def test_grades(self):
        self.assertEqual(persona.grade(0.52, -100, 0.0), "A")
        self.assertEqual(persona.grade(0.30, 5, 0.5), "B")
        self.assertEqual(persona.grade(0.41, -5, 0.5), "C")
        self.assertEqual(persona.grade(0.20, -5, 0.01), "D")
        self.assertEqual(persona.grade(0.20, -5, 0.2), "F")

    def test_rounding_and_unknown(self):
        self.assertEqual(persona._jsround(2.5), 3)
        self.assertEqual(persona._jsround(-2.5), -2)
        p = persona.persona(dict(self.BASE, closed=0), "en")
        self.assertEqual((p["archetype"], p["grade"]), ("UNKNOWN", "?"))
        for lang in ("zh", "en"):
            L = persona.labels(lang)
            for k in persona.ARCHETYPES + ("UNKNOWN",):
                self.assertIn(k, L["archetypes"])
            for k in persona.GRADES:
                self.assertIn(k, L["grades"])


if __name__ == "__main__":
    unittest.main()
