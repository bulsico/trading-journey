"""SMA trend tags (lsl/trend.py) and the candle cache (lsl/candles.py).  All data is generated."""
import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

from lsl import candles, trend
from lsl.util import UTC
from tests.fakes import FakeHL, FakeRH, synth_close


def days(start, n, step=1):
    x = dt.date.fromisoformat(start)
    return [(x + dt.timedelta(days=i * step)).isoformat() for i in range(n)]


def bars_from(closes, start="2024-01-01"):
    return list(zip(days(start, len(closes)), closes))


def trade(src, cls, sym, side, entry, pnl, label=None, acct="a1", hold_h=2):
    ts = dt.datetime.fromisoformat(entry).replace(tzinfo=UTC) + dt.timedelta(hours=hold_h)
    return {"src": src, "cls": cls, "sym": sym, "side": side, "ts": ts, "hold_s": hold_h * 3600,
            "day": ts.date().isoformat(), "pnl": pnl, "acct": acct, "label": label or sym}


class SMA(unittest.TestCase):
    def test_sma_math(self):
        self.assertEqual(trend.sma([1, 2, 3, 4, 5], 3), 4.0)
        self.assertEqual(trend.sma([2.0] * 200, 200), 2.0)
        self.assertIsNone(trend.sma([1, 2], 3))

    def test_context_uptrend(self):
        b = bars_from([100 + i for i in range(260)])           # closes 100..359
        day = b[-1][0]                                       # entry on the last bar's day
        c = trend.context(b, day, (7, 50, 200), "crypto")
        self.assertEqual(c["status"], "ok")
        self.assertEqual(c["ref_date"], b[-2][0])           # last *closed* bar before the entry day
        self.assertEqual(c["price"], 358.0)
        self.assertEqual(c["sma"]["7"], 355.0)               # mean(352..358)
        self.assertEqual(c["sma"]["50"], 333.5)
        self.assertEqual(c["sma"]["200"], 258.5)
        self.assertEqual(c["dist_pct"]["7"], round(358 / 355 - 1, 4))
        self.assertEqual((c["regime"], c["stack"]), ("golden", "bullish"))
        self.assertTrue(all(c["above"].values()))

    def test_downtrend_and_alignment(self):
        b = bars_from([500 - i for i in range(260)])
        c = trend.context(b, "2030-01-01", (15, 50, 200), "stock")
        self.assertEqual(c["status"], "stale")
        c = trend.context(b, b[-1][0], (15, 50, 200), "stock")
        self.assertEqual((c["regime"], c["stack"]), ("death", "bearish"))
        self.assertEqual(trend.alignment("short", c), "with")
        self.assertEqual(trend.alignment("long", c), "counter")
        self.assertEqual(trend.alignment(None, c), "neutral")
        # pullback: price above the fast SMA while the regime is still a death cross
        b2 = b[:-1] + [(b[-1][0], 260.0)]
        nxt = (dt.date.fromisoformat(b2[-1][0]) + dt.timedelta(days=1)).isoformat()
        c2 = trend.context(b2, nxt, (15, 50, 200), "stock")
        self.assertEqual(c2["regime"], "death")
        self.assertEqual(trend.alignment("long", c2), "neutral")
        self.assertEqual(trend.alignment("short", c2), "neutral")

    def test_insufficient_history_never_guesses(self):
        b = bars_from([100 + i for i in range(199)])
        nxt = (dt.date.fromisoformat(b[-1][0]) + dt.timedelta(days=1)).isoformat()
        c = trend.context(b, nxt, (7, 50, 200), "crypto")
        self.assertEqual(c, {"status": "insufficient_history", "bars": 199})
        self.assertIsNone(trend.alignment("long", c))
        self.assertEqual(trend.context(b, b[0][0], (7, 50, 200), "crypto")["status"], "insufficient_history")

    def test_option_direction(self):
        self.assertEqual(trend.direction(trade("rh", "option", "AAA", "long", "2025-01-01T15:00:00", 1, "AAA 100C")), "long")
        self.assertEqual(trend.direction(trade("rh", "option", "AAA", "short", "2025-01-01T15:00:00", 1, "AAA 100P")), "long")
        self.assertEqual(trend.direction(trade("rh", "option", "AAA", "long", "2025-01-01T15:00:00", 1, "AAA 100P")), "short")


class Tagging(unittest.TestCase):
    def series(self):
        up = [100 + i for i in range(400)]
        return {"rh:AAA": bars_from(up, "2024-01-01"), "hl:BTC": bars_from(up, "2024-01-01"),
                "hl:ETH": bars_from([900 - i for i in range(400)], "2024-01-01")}

    def test_periods_stock_vs_crypto(self):
        s = self.series()
        a = trend.tag(trade("rh", "equity", "AAA", "long", "2025-01-20T15:00:00", 5), s)
        b = trend.tag(trade("hl", "perp", "BTC", "long", "2025-01-20T15:00:00", 5), s)
        o = trend.tag(trade("rh", "option", "AAA", "long", "2025-01-20T15:00:00", 5, "AAA 2025-02-21 100C"), s)
        self.assertEqual((a["periods"], a["series"], a["alignment"]), ([15, 50, 200], "rh:AAA", "with"))
        self.assertEqual((b["periods"], b["series"], b["alignment"]), ([7, 50, 200], "hl:BTC", "with"))
        self.assertEqual((o["series"], o["periods"]), ("rh:AAA", [15, 50, 200]))   # option -> underlying
        self.assertIn("15", a["sma"])
        self.assertIn("7", b["sma"])

    def test_unavailable_and_insufficient(self):
        s = self.series()
        self.assertEqual(trend.tag(trade("rh", "equity", "ZZZ", "long", "2025-01-20T15:00:00", 5), s)["status"],
                         "unavailable")
        self.assertEqual(trend.tag(trade("rh", "equity", "AAA", "long", "2024-03-01T15:00:00", 5), s)["status"],
                         "insufficient_history")

    def test_aggregates(self):
        s = self.series()
        ts = [trade("rh", "equity", "AAA", "long", "2025-01-%02dT15:00:00" % (i + 2), 10 if i % 4 else -5)
              for i in range(12)]
        ts += [trade("hl", "perp", "ETH", "long", "2025-01-%02dT15:00:00" % (i + 2), -8 if i % 3 else 2, acct="h1")
               for i in range(12)]
        tr, rows = trend.build(ts, s, [{"key": "rh:a1", "trades": ts[:12]}, {"key": "hl:h1", "trades": ts[12:]}])
        self.assertEqual(tr["coverage"]["tagged"], 24)
        w, c = tr["by_alignment"]["with"], tr["by_alignment"]["counter"]
        self.assertEqual((w["trades"], w["wins"], w["pnl"]), (12, 9, 75.0))
        self.assertEqual((c["trades"], c["wins"], c["pnl"]), (12, 4, -56.0))
        self.assertEqual(tr["with_share"], 0.5)
        self.assertEqual(tr["by_account"]["hl:h1"]["counter_share"], 1.0)
        self.assertEqual(tr["by_direction"]["long"]["counter"]["trades"], 12)
        self.assertEqual(tr["by_regime"]["death"]["trades"], 12)
        self.assertIn("with_trend_better", tr["notable"])
        self.assertIn("fights_trend_often", tr["notable"])
        self.assertEqual(len(rows), 24)
        self.assertEqual(trend.build(ts, {}, [])[0]["status"], "no_candles")

    def test_persona_uses_trend(self):
        from lsl import persona
        s = self.series()
        ts = [trade("rh", "equity", "AAA", "long", "2025-01-%02dT15:00:00" % (i + 2), 10) for i in range(25)]
        tr, _ = trend.build(ts, s, [])
        st = {"fills": 200, "closed": 80, "fpd": 2.0, "win_rate": 0.3, "avg_win": 50.0, "avg_loss": -20.0,
              "unique_assets": 4, "concentration": 0.5, "liq_rate": 0.0,
              "monthly_fill_counts": [10, 10, 10], "net": 1000.0}
        p = persona.persona(st, "en", trend=tr)
        self.assertEqual(p["trend_basis"], "sma")
        self.assertEqual(p["ratings"]["trend_awareness"], 10)
        self.assertEqual(p["archetype"], "TREND_MASTER")       # 100% with-trend, profitable
        old = persona.persona(st, "en")
        self.assertEqual(old["trend_basis"], "formula")
        self.assertEqual(old["ratings"]["trend_awareness"], 5)  # 0.3*10 + 2 (old formula)
        self.assertNotEqual(old["archetype"], "TREND_MASTER")


class CandleCache(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def trades(self, last="2025-06-10"):
        out = [trade("hl", "perp", "BTC", "long", "2025-01-15T12:00:00", 3),
               trade("hl", "perp", "ETH", "short", "2025-03-01T12:00:00", -2),
               trade("hl", "perp", "NOPE", "long", "2025-03-01T12:00:00", -2)]
        if last:
            out.append(trade("hl", "perp", "BTC", "long", last + "T12:00:00", 4))
        return out

    def run_fetch(self, d, trades, as_of, hl=None):
        hl = hl or FakeHL([], missing_coins={"NOPE", "kNOPE"})
        res = candles.fetch_hl(d, post=hl, sleep=lambda s: None, log=lambda m: None, trades_as_of=(trades, as_of))
        return res, hl

    def files(self, d):
        cd = candles.cache_dir(d)
        from lsl.util import read_json
        return {f: json.dumps(read_json(os.path.join(cd, f))) for f in sorted(os.listdir(cd))}

    def test_closed_candles_and_unavailable(self):
        res, hl = self.run_fetch(self.d, self.trades(), "2025-06-20T00:00:00Z")
        s = candles.load_all(self.d)
        self.assertEqual(s["hl:BTC"][-1][0], "2025-06-09")    # nothing on/after an entry day is needed
        self.assertEqual(s["hl:BTC"][0][1], synth_close("BTC", s["hl:BTC"][0][0]))
        self.assertIn("hl:NOPE", res["unavailable"])
        self.assertEqual(len([c for c in hl.calls if c["req"]["coin"] == "BTC"]), 1)   # one call per series
        res2, hl2 = self.run_fetch(self.d, self.trades(), "2025-06-20T00:00:00Z")
        self.assertEqual(len(hl2.calls), 0)                   # resumable: nothing left to fetch

    def test_incremental_equals_full(self):
        inc = self.d
        self.run_fetch(inc, self.trades(last="2025-05-01"), "2025-05-05T00:00:00Z")
        self.run_fetch(inc, self.trades(last="2025-08-01"), "2025-08-02T00:00:00Z")
        full = tempfile.mkdtemp()
        try:
            self.run_fetch(full, self.trades(last="2025-08-01"), "2025-08-02T00:00:00Z")
            a, b = self.files(inc), self.files(full)
            strip = lambda t: {k: v for k, v in json.loads(t).items() if k != "covered"}
            self.assertEqual(sorted(a), sorted(b))
            for k in a:
                self.assertEqual(strip(a[k]), strip(b[k]), k)
        finally:
            shutil.rmtree(full)

    def test_rh_windows_and_truncation(self):
        tr = [trade("rh", "equity", "AAA", "long", "2025-06-02T15:00:00", 1),
              trade("rh", "equity", "GONE", "long", "2025-06-02T15:00:00", 1)]
        srv = FakeRH({}, unknown_symbols={"GONE"}, bars_limit=6000)
        n = 0
        for _ in range(50):
            calls, _left = candles.rh_plan(self.d, 8, (tr, "2025-06-10T00:00:00Z"))
            if not calls:
                break
            for c in calls:
                n += 1
                candles.rh_ingest(self.d, dict(c, candles=c), srv.call(c["tool"], c["args"]))
        st = candles.status(self.d, (tr, "2025-06-10T00:00:00Z"))
        self.assertEqual(st["rh"], {"series": 2, "complete": 1, "missing": 1, "windows_left": 0})
        bars = candles.load_all(self.d)["rh:AAA"]
        self.assertTrue(all(dt.date.fromisoformat(x[0]).weekday() < 5 for x in bars))
        self.assertGreaterEqual(len(bars), 200)
        self.assertLess(candles._cstate(self.d)["rh_window"]["AAA"], candles.RH_WEEKDAYS_PER_CALL)
        t = trend.tag(tr[0], candles.load_all(self.d))
        self.assertEqual(t["status"], "ok")

    @staticmethod
    def yahoo(url):
        """Fake Yahoo chart endpoint: synthetic weekday closes; symbol GONE is unknown."""
        sym = url.split("/chart/")[1].split("?")[0]
        if sym == "GONE":
            raise candles.Missing()
        p1, p2 = [int(x.split("=")[1]) for x in url.split("?")[1].split("&")[:2]]
        ts, cl = [], []
        for t in range(p1, p2, 86400):
            day = dt.datetime.fromtimestamp(t, UTC).date()
            if day.weekday() < 5:
                ts.append(t + 13 * 3600 + 1800)
                cl.append(synth_close(sym, day.isoformat()))
        return {"chart": {"result": [{"meta": {"gmtoffset": -14400}, "timestamp": ts,
                                      "indicators": {"quote": [{"close": cl}]}}]}}

    def test_yahoo_keyless(self):
        tr = [trade("rh", "equity", "AAA", "long", "2025-06-02T15:00:00", 1),
              trade("rh", "equity", "GONE", "long", "2025-06-02T15:00:00", 1)]
        get = self.yahoo
        res = candles.fetch_stocks_yahoo(self.d, get=get, sleep=lambda s: None, log=lambda m: None,
                                         trades_as_of=(tr, "2025-06-10T00:00:00Z"))
        self.assertEqual((res["calls"], res["fetched"], res["unavailable"]), (2, 1, ["rh:GONE"]))
        self.assertEqual(trend.tag(tr[0], candles.load_all(self.d))["status"], "ok")
        again = candles.fetch_stocks_yahoo(self.d, get=get, sleep=lambda s: None, log=lambda m: None,
                                           trades_as_of=(tr, "2025-06-10T00:00:00Z"))
        self.assertEqual(again["calls"], 0)


class EndToEnd(unittest.TestCase):
    """Fake RH + HL accounts -> candles -> compute: trend tags land in every output, byte-identical reruns."""

    def setUp(self):
        from tests import test_compute
        self.d = tempfile.mkdtemp()
        test_compute.build(self.d)
        shutil.rmtree(candles.cache_dir(self.d), ignore_errors=True)   # build() ran the RH candle loop; start bare

    def tearDown(self):
        shutil.rmtree(self.d)

    def fetch(self):
        get = lambda url: CandleCache.yahoo(url)
        h = candles.fetch_hl(self.d, post=FakeHL([]), sleep=lambda s: None, log=lambda m: None)
        y = candles.fetch_stocks_yahoo(self.d, get=get, sleep=lambda s: None, log=lambda m: None)
        return h, y

    def out(self, name, lang="en"):
        from lsl import compute
        o = os.path.join(self.d, name)
        compute.run(self.d, o, lang)
        out = {}
        for f in sorted(os.listdir(o)):
            with open(os.path.join(o, f), "rb") as fh:
                out[f] = fh.read()
        return out

    def test_compute_deterministic_with_candles(self):
        from lsl.util import read_json
        before = self.out("o0")
        sm0 = read_json(os.path.join(self.d, "o0", "summary.json"))
        self.assertEqual(sm0["trend"]["status"], "no_candles")
        self.assertFalse(read_json(os.path.join(self.d, "o0", "page.json"))["has"]["trend"])
        h, y = self.fetch()
        self.assertGreater(h["calls"] + y["calls"], 0)
        a = self.out("o1")
        h2, y2 = self.fetch()
        self.assertEqual(h2["calls"] + y2["calls"], 0)           # refresh with nothing new: no calls
        self.assertEqual(a, self.out("o2"))                       # byte-identical rerun
        sm = read_json(os.path.join(self.d, "o1", "summary.json"))
        self.assertEqual(sm["trend"]["status"], "ok")
        self.assertGreater(sm["trend"]["coverage"]["tagged"], 0)
        self.assertEqual(sm["persona"]["trend_basis"], "formula")   # too few tags for the persona
        # every non-trend number is unchanged by adding candles
        drop = lambda x: {k: v for k, v in x.items() if k != "trend"}
        self.assertEqual(drop(sm["overall"]), drop(sm0["overall"]))
        pg = read_json(os.path.join(self.d, "o1", "page.json"))
        self.assertTrue(pg["has"]["trend"])
        self.assertLessEqual(len(pg["tldr"]), 6)
        self.assertTrue(all(isinstance(x, str) for r in pg["trend"]["alignment"] for x in r.values()))
        se = read_json(os.path.join(self.d, "o1", "series.json"))
        self.assertTrue(any(r["status"] == "ok" for r in se["trend_entries"]))
        zh = self.out("o3", "zh")
        self.assertTrue(json.loads(zh["page.json"])["has"]["trend"])

    def test_tldr_trend_line(self):
        from lsl import report
        b = lambda n, w, pnl: {"trades": n, "wins": w, "win_rate": w / n, "pnl": pnl, "avg_pnl": pnl / n}
        tr = {"status": "ok", "tagged": 40, "with_share": 0.25, "counter_share": 0.5,
              "by_alignment": {"with": b(10, 7, 500.0), "counter": b(20, 6, -900.0), "neutral": b(10, 5, 0.0)},
              "alt_longs_in_btc_death_cross": b(12, 3, -1200.0)}
        tr["notable"] = trend.notable(tr)
        self.assertEqual(tr["notable"], ["with_trend_better", "fights_trend_often", "alt_longs_in_btc_death_cross_lost"])
        en = report.trend_line({"trend": tr}, "en")
        self.assertIn("70.0%", en)
        self.assertIn("-$1,200.00", en)
        self.assertTrue(report.trend_line({"trend": tr}, "zh").startswith("趋势"))
        self.assertIsNone(report.trend_line({"trend": dict(tr, notable=[])}, "en"))


if __name__ == "__main__":
    unittest.main()
