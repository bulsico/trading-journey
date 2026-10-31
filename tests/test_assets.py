"""HL spot whitelist, cross-venue grouping, incremental == fresh, purge."""
import json
import os
import random
import shutil
import tempfile
import unittest

from lsl import assets, cli, compute, hl, rhsync
from lsl.util import to_utc
from tests.fakes import FakeHL, FakeRH, drive, eq_order
from tests.test_compute import T
from tests.test_hl import ADDR, fill

WL = assets.load_whitelist()
TID = {t["index"]: t["tokenId"] for t in WL["tokens"] + WL["quote_tokens"]}
HYPE = next(t["index"] for t in WL["tokens"] if t["underlying"] == "HYPE")
USDC = next(t["index"] for t in WL["quote_tokens"] if t["name"] == "USDC")
JUNK, SPOOF = 99001, 99002
META = {"tokens": [{"index": USDC, "name": "USDC", "tokenId": TID[USDC]},
                   {"index": HYPE, "name": "HYPE", "tokenId": TID[HYPE]},
                   {"index": JUNK, "name": "JUNKX", "tokenId": "0x01"},
                   # same name as a whitelisted Unit token, but a different index -> junk
                   {"index": SPOOF, "name": "UBTC", "tokenId": "0x02"}],
        "universe": [{"index": 1, "name": "@1", "tokens": [HYPE, USDC]},
                     {"index": 2, "name": "@2", "tokens": [JUNK, USDC]},
                     {"index": 3, "name": "@3", "tokens": [SPOOF, USDC]}]}


def hl_fills(base=1700000000000, n0=0):
    f = lambda i, **kw: fill(base + i * 1000, n0 + i, **kw)
    return [f(1, coin="TST", px="10", sz="1"),
            f(2, coin="TST", px="12", sz="1", side="A", dir="Close Long", closedPnl="2"),
            f(3, coin="hyna:TST", px="10", sz="1"),
            f(4, coin="hyna:TST", px="9", sz="1", side="A", dir="Close Long", closedPnl="-1"),
            f(5, coin="xyz:TSTA", px="10", sz="1"),
            f(6, coin="xyz:TSTA", px="13", sz="1", side="A", dir="Close Long", closedPnl="3"),
            f(7, coin="kTSTK", px="1", sz="1"),
            f(8, coin="kTSTK", px="2", sz="1", side="A", dir="Close Long", closedPnl="1"),
            f(9, coin="zzdex:TST", px="1", sz="1"),
            f(10, coin="@1", px="10", sz="1", dir="Buy"),
            f(11, coin="@1", px="11", sz="1", side="A", dir="Sell", closedPnl="1"),
            f(12, coin="@2", px="1", sz="5", dir="Buy"),
            f(13, coin="@2", px="2", sz="5", side="A", dir="Sell", closedPnl="5"),
            f(14, coin="@3", px="1", sz="1", dir="Buy"),
            f(15, coin="@3", px="3", sz="1", side="A", dir="Sell", closedPnl="2")]


class WhitelistAndGroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        hl.fetch(cls.tmp, ADDR, to_utc("2026-01-01T00:00:00Z"), post=FakeHL(hl_fills(), spot_meta=META))
        cls.h = compute.load_hl(cls.tmp)[0]
        cls.trades, cls.acts, _, cls.dq, cls.pos = compute.hl_process(cls.h)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_whitelist_by_index(self):
        c = self.dq["counts"]
        self.assertEqual(c["spot_fills_excluded_not_whitelisted"], 4)   # @2 junk + @3 spoofed name
        self.assertEqual(c["spot_trades_excluded_not_whitelisted"], 2)
        spot = [t for t in self.trades if t["cls"] == "spot"]
        self.assertEqual([(t["canon"], t["pnl"]) for t in spot], [("HYPE", 1.0)])
        self.assertEqual(self.dq["spot_whitelist"]["token_id_mismatch"], 0)

    def test_token_id_mismatch_excludes(self):
        meta = json.loads(json.dumps(META))
        meta["tokens"][1]["tokenId"] = "0xdead"
        wl = assets.SpotWhitelist(meta, WL)
        self.assertEqual(wl.resolve("@1"), (None, "token_id_mismatch"))
        self.assertEqual(assets.SpotWhitelist(None, WL).resolve("@1"), (None, "unresolved_pair"))

    def test_groups(self):
        g, q = assets.groups(self.trades, self.acts)
        by = {r["group"]: r for r in g}
        tst = by["crypto:TST"]
        self.assertEqual([v["venue"] for v in tst["venues"]], ["hl_perp", "hl_dex:hyna"])
        self.assertEqual((tst["closed_trades"], tst["wins"], tst["realized_pnl"]), (2, 1, 1.0))
        self.assertEqual(by["equity:TSTA"]["venues"][0]["venue"], "hl_dex:xyz")
        self.assertEqual(by["crypto:TSTK"]["venues"][0]["multiplier"], 1000)
        self.assertEqual(by["crypto:HYPE"]["venues"][0]["venue"], "hl_spot")
        self.assertEqual(q["ambiguous"], [{"venue": "hl_dex:zzdex", "raw_symbol": "zzdex:TST",
                                           "reason": "unknown_perp_dex"}])
        self.assertEqual(q["multi_venue_groups"], 1)
        self.assertEqual(q["symbol_kind_collisions"], [])

    def test_rules(self):
        c = assets.canonical
        self.assertEqual(c("rh_equity", "SQ")["group"], "equity:XYZ")          # public ticker rename
        self.assertEqual(c("rh_option", "FB")["group"], "equity:META")
        self.assertEqual(c("hl_dex:xyz", "xyz:SILVER")["group"], "commodity:SILVER")
        self.assertEqual(c("hl_perp", "1000TSTM")["multiplier"], 1000)
        self.assertTrue(c("rh_option", "TSTA1")["ambiguous"])
        self.assertTrue(c("hl_spot", "@9", None)["ambiguous"])
        self.assertNotEqual(c("rh_equity", "TST")["group"], c("rh_crypto", "TST")["group"])


def _outputs(rh_dir, hl_dir):
    rh = compute.load_rh(rh_dir) if rh_dir else None
    h = compute.load_hl(hl_dir) if hl_dir else None
    return json.dumps(compute.compute_all(rh, h, "en"), sort_keys=True)


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def d(self, name):
        return os.path.join(self.tmp, name)

    def test_hl_incremental_equals_fresh(self):
        old, new = hl_fills(), hl_fills(base=1700100000000, n0=100)
        fund = [{"time": 1700000150000, "hash": "0x0",
                 "delta": {"type": "funding", "coin": "TST", "usdc": "-0.5", "szi": "1"}}]
        fund2 = fund + [{"time": 1700100150000, "hash": "0x1",
                         "delta": {"type": "funding", "coin": "TST", "usdc": "0.25", "szi": "1"}}]
        hl.fetch(self.d("inc"), ADDR, to_utc("2023-11-20T00:00:00Z"),
                 post=FakeHL(old, funding=fund, spot_meta=META, page=4))
        hl.fetch(self.d("inc"), ADDR, to_utc("2026-01-01T00:00:00Z"),
                 post=FakeHL(old + new, funding=fund2, spot_meta=META, page=4))
        hl.fetch(self.d("fresh"), ADDR, to_utc("2026-01-01T00:00:00Z"),
                 post=FakeHL(old + new, funding=fund2, spot_meta=META, page=4))
        self.assertEqual(len(compute.load_hl(self.d("inc"))[0]["fills"]), 30)
        self.assertEqual(_outputs(None, self.d("inc")), _outputs(None, self.d("fresh")))

    def test_rh_incremental_equals_fresh(self):
        rng = random.Random(7)
        old = [eq_order(rng, T("2025-01-10T15:00:00"), "TSTA", "buy", 5, 10.0),
               eq_order(rng, T("2025-02-03T15:00:00"), "TSTA", "sell", 5, 11.0)]
        new = [eq_order(rng, T("2025-12-20T15:00:00"), "TSTB", "buy", 3, 20.0),
               eq_order(rng, T("2025-12-28T15:00:00"), "TSTB", "sell", 3, 25.0)]
        hist = {"TSTA": [{"timestamp": "2025-02-03T15:00:00Z", "side": "sell", "symbol": "TSTA",
                          "quantity": "5", "price": "11", "realized_gain": "5"}]}
        hist2 = dict(hist, TSTB=[{"timestamp": "2025-12-28T15:00:00Z", "side": "sell", "symbol": "TSTB",
                                  "quantity": "3", "price": "25", "realized_gain": "15"}])
        mk = lambda o, h: FakeRH({("TESTACCT1", "equity"): o}, hist=h, pnl_from="2025-01")
        n1, n2 = "2025-12-01T00:00:00Z", "2026-01-01T00:00:00Z"
        self.assertEqual(drive(self.d("inc"), mk(old, hist), now=n1)[0]["status"], "done")
        rhsync.start_incremental(rhsync.Run(self.d("inc")), to_utc(n2))
        self.assertEqual(drive(self.d("inc"), mk(old + new, hist2), now=n2)[0]["status"], "done")
        self.assertEqual(drive(self.d("fresh"), mk(old + new, hist2), now=n2)[0]["status"], "done")
        a, b = json.loads(_outputs(self.d("inc"), None)), json.loads(_outputs(self.d("fresh"), None))
        self.assertEqual(a["summary.json"]["overall"]["closed_trades"], 2)
        self.assertEqual(a, b)


class PurgeTest(unittest.TestCase):
    def test_purge(self):
        d = tempfile.mkdtemp()
        try:
            for p in ("state/hl/fills.json", "state/rh_state.json", "state/rh/x.json", "raw/1.txt",
                      "out/summary.json"):
                os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
                open(os.path.join(d, p), "w").close()
            r = cli.purge(d, "hl")
            self.assertEqual((r["status"], r["paths"]), ("dry_run", ["state/hl"]))
            self.assertTrue(os.path.exists(os.path.join(d, "state/hl/fills.json")))
            self.assertEqual(cli.purge(d, "rh", yes=True)["paths"],
                             ["raw", "state/rh", "state/rh_state.json", "out"])
            self.assertEqual(sorted(os.listdir(os.path.join(d, "state"))), ["hl"])
            cli.purge(d, "all", yes=True)
            self.assertEqual(os.listdir(d), [])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
