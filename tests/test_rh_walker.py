"""Robinhood walker against a fake MCP: every order is collected despite
gateway truncation, mangled pages, upstream errors and dense years."""
import datetime as dt
import random
import re
import tempfile
import unittest

from lsl import rhsync
from lsl.util import UTC
from tests.fakes import FakeRH, drive, equity_orders, option_order, _ts

NOW = "2026-01-01T00:00:00Z"


def _ids(run, cls):
    return {o["id"] for o in rhsync._orders_of(run, "a0", cls)}


class WalkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, orders, **kw):
        opts = {k: kw.pop(k) for k in ("option_universe",) if k in kw}
        srv = FakeRH(orders, **kw)
        res, rounds = drive(self.dir, srv, now=NOW, **opts)
        return rhsync.Run(self.dir), srv, res

    def test_collects_everything_with_truncation_and_lanes(self):
        rng = random.Random(1)
        eq = equity_orders(rng, dt.datetime(2015, 3, 1, tzinfo=UTC), dt.datetime(2025, 12, 1, tzinfo=UTC),
                           120, dense=(2021, 150))
        run, srv, res = self._run({("TESTACCT1", "equity"): eq}, limit=3000)
        self.assertEqual(res["status"], "done")
        self.assertEqual(_ids(run, "equity"), {o["id"] for o in eq})
        S = run.st["streams"]
        self.assertTrue(any(".y20" in k for k in S), "year lanes spawned")
        self.assertTrue(any(re.search(r"\.y\d{4}\.m\d\d$", k) for k in S), "month lanes spawned")
        st = S["a0.orders.equity"]["stats"]
        self.assertGreater(st.get("TRUNCATED", 0), 0)
        self.assertTrue(all(c[0] in rhsync.READ_ONLY_TOOLS for c in srv.calls))
        self.assertEqual(S["a0.orders.crypto"]["status"], "na")

    def test_mangled_page_single_fetch(self):
        rng = random.Random(2)
        eq = equity_orders(rng, dt.datetime(2024, 1, 1, tzinfo=UTC), dt.datetime(2025, 6, 1, tzinfo=UTC), 30)
        seen = set()

        def mangle(tool, args, recs):            # mangle each distinct page once
            k = (args.get("cursor"), args.get("created_at_gte"))
            if k in seen:
                return False
            seen.add(k)
            return len(seen) % 3 == 0
        run, srv, res = self._run({("TESTACCT1", "equity"): eq}, mangle=mangle)
        self.assertEqual(_ids(run, "equity"), {o["id"] for o in eq})
        self.assertGreater(sum(s.get("stats", {}).get("single_fetch", 0)
                               for s in run.st["streams"].values()), 0)
        self.assertTrue(any(".one." in k for k in run.st["streams"]))

    def test_errors_split_windows(self):
        rng = random.Random(3)
        eq = equity_orders(rng, dt.datetime(2024, 1, 1, tzinfo=UTC), dt.datetime(2025, 6, 1, tzinfo=UTC), 40)
        from lsl.rhpage import decode_cursor
        from lsl.util import to_utc

        def fail(tool, args):                     # windows wider than 10 days time out
            if "created_at_gte" not in args or "cursor" not in args:
                return False
            hi = to_utc(decode_cursor(args["cursor"])[2:])
            return hi - to_utc(args["created_at_gte"]) > dt.timedelta(days=10)
        run, srv, res = self._run({("TESTACCT1", "equity"): eq}, fail=fail)
        s = run.st["streams"]["a0.orders.equity"]
        self.assertGreater(s["stats"].get("split", 0), 0)
        self.assertEqual(s["failed"], [])
        self.assertEqual(_ids(run, "equity"), {o["id"] for o in eq})

    def test_tie_overflow_is_reported(self):
        rng = random.Random(4)
        eq = equity_orders(rng, dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 6, 1, tzinfo=UTC), 5)
        t = _ts(dt.datetime(2025, 3, 3, 15, 0, tzinfo=UTC))
        for o in equity_orders(rng, dt.datetime(2025, 3, 1, tzinfo=UTC), dt.datetime(2025, 3, 2, tzinfo=UTC), 15):
            o["created_at"] = t
            eq.append(o)
        run, srv, res = self._run({("TESTACCT1", "equity"): eq}, limit=2500)
        failed = run.st["streams"]["a0.orders.equity"]["failed"]
        self.assertTrue(any(f[2] == "TIE_OVERFLOW" for f in failed))
        got = _ids(run, "equity")
        self.assertTrue(got < {o["id"] for o in eq})           # some ties lost, and we said so
        self.assertTrue({o["id"] for o in eq if o["created_at"] != t} <= got)

    def test_history_and_option_universe(self):
        rng = random.Random(5)
        eq = equity_orders(rng, dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 6, 1, tzinfo=UTC), 6,
                           sym_pool=("TSTA",))
        opt, contracts = [], []
        for i in range(40):
            exp = (dt.date(2025, 1, 3) + dt.timedelta(days=7 * (i % 20))).isoformat()
            oid = "opt-%02d" % i
            t0 = dt.datetime.fromisoformat(exp + "T15:00:00+00:00") - dt.timedelta(days=3)
            opt.append(option_order(rng, t0, "TSTB", oid, exp, "buy", "open", 1, 1.5))
            if i % 2:
                opt.append(option_order(rng, t0 + dt.timedelta(days=1), "TSTB", oid, exp, "sell", "close", 1, 2.0))
            contracts.append({"option_id": oid, "type": "long", "quantity": "0", "expiration_date": exp,
                              "pad": "x" * 200})
        hist = {"TSTA": [{"timestamp": "2025-02-01T15:00:00Z", "side": "sell", "symbol": "TSTA",
                          "quantity": "1", "price": "10", "realized_gain": "1"}] * 5}
        run, srv, res = self._run({("TESTACCT1", "equity"): eq, ("TESTACCT1", "option"): opt},
                                  hist=hist, option_contracts=contracts, uni_limit=3000,
                                  option_universe=True)
        S = run.st["streams"]
        self.assertEqual(res["status"], "done")
        self.assertEqual(run.st["flags"]["coverage:a0"], "2025-01")
        hs = [k for k in S if k.startswith("a0.hist.")]
        self.assertEqual(sorted(S[k]["status"] for k in hs), ["done", "done"])
        self.assertEqual(sum(len(run.load_data(k, [])) for k in hs), 5)   # 3 cursor pages
        uni = [s for s in S.values() if s["type"] == "optuni"]
        self.assertTrue(any(s["status"] == "split" for s in uni))
        got = {}
        for k in S:
            if S[k]["type"] == "optuni":
                got.update(run.load_data(k, {}))
        self.assertEqual(len(got), 40)

    def test_option_universe_opt_out(self):
        rng = random.Random(6)
        opt = [option_order(rng, dt.datetime(2025, 1, 2, 15, tzinfo=UTC), "TSTB", "o1", "2025-01-10",
                            "buy", "open", 1, 1.0)]
        run, srv, res = self._run({("TESTACCT1", "option"): opt})
        self.assertFalse(any(s["type"] == "optuni" for s in run.st["streams"].values()))
        # turning it on later plans it; turning it off again skips what is left
        r = rhsync.plan(run, 8, option_universe=True)
        self.assertEqual(r["status"], "calls")
        self.assertTrue(all(c["tool"] == "get_option_positions" for c in r["calls"]))
        rhsync.set_option_universe(run, False)
        self.assertEqual(run.st["pending"], {})
        self.assertEqual(rhsync.plan(run, 8)["status"], "done")
        self.assertTrue(all(s["status"] == "skipped" for s in run.st["streams"].values()
                            if s["type"] == "optuni"))

    def test_plan_is_deterministic(self):
        rng = random.Random(7)
        eq = equity_orders(rng, dt.datetime(2023, 1, 1, tzinfo=UTC), dt.datetime(2025, 6, 1, tzinfo=UTC), 50)
        logs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                srv = FakeRH({("TESTACCT1", "equity"): eq}, limit=3000)
                drive(d, srv, now=NOW)
                logs.append(srv.calls)
        self.assertEqual(logs[0], logs[1])


if __name__ == "__main__":
    unittest.main()
