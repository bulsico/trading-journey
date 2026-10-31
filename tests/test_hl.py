"""Hyperliquid fetch against a fake info API."""
import tempfile
import unittest

from lsl import hl
from tests.fakes import FakeHL

ADDR = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


def fill(t, tid, coin="TST", **kw):
    d = {"time": t, "tid": tid, "oid": tid, "coin": coin, "px": "10", "sz": "1", "side": "B",
         "dir": "Open Long", "closedPnl": "0", "fee": "0.01", "feeToken": "USDC", "hash": "0x%x" % tid,
         "startPosition": "0", "crossed": True}
    d.update(kw)
    return d


class PagerTest(unittest.TestCase):
    def test_ties_on_page_boundary(self):
        fills = [fill(1000 + i, i) for i in range(5)] + [fill(2000, 100 + i) for i in range(4)] + \
                [fill(3000 + i, 200 + i) for i in range(5)]
        api = FakeHL(fills, page=5)
        rows, info = hl._page_forward(api, {"type": "userFillsByTime"}, hl._fill_key, lambda r: r["time"], 10 ** 6)
        self.assertEqual(len(rows), len(fills))
        self.assertEqual(info["possible_tie_gaps"], 0)

    def test_tie_overflow_counted_and_skipped(self):
        fills = [fill(1000, i) for i in range(7)] + [fill(5000, 50)]
        api = FakeHL(fills, page=5)
        rows, info = hl._page_forward(api, {"type": "userFillsByTime"}, hl._fill_key, lambda r: r["time"], 10 ** 6)
        self.assertEqual(info["possible_tie_gaps"], 1)
        self.assertEqual(len(rows), 6)          # 5 of the 7 tied rows + the later one
        self.assertIn(5000, [r["time"] for r in rows])

    def test_spot_names(self):
        meta = {"tokens": [{"index": 0, "name": "USDC"}, {"index": 7, "name": "TSTX"}],
                "universe": [{"index": 3, "tokens": [7, 0], "name": "@3"},
                             {"index": 0, "tokens": [7, 0], "name": "TSTX/USDC"}]}
        self.assertEqual(hl.spot_names(meta), {"@3": "TSTX/USDC", "@0": "TSTX/USDC", "TSTX/USDC": "TSTX/USDC"})

    def test_fetch(self):
        fills = [fill(1000, 1), fill(2000, 2, coin="dexa:TSTY"),
                 fill(3000, 3, dir="Close Long", closedPnl="-5",
                      liquidation={"liquidatedUser": ADDR, "markPx": "9", "method": "market"}),
                 fill(4000, 4, liquidation={"liquidatedUser": OTHER, "markPx": "9", "method": "market"}),
                 fill(5000, 5, coin="@3")]
        meta = {"tokens": [{"index": 0, "name": "USDC"}, {"index": 7, "name": "TSTX"}],
                "universe": [{"index": 3, "tokens": [7, 0], "name": "@3"}]}
        api = FakeHL(fills, spot_meta=meta)
        with tempfile.TemporaryDirectory() as d:
            res = hl.fetch(d, ADDR.upper().replace("0X", "0x"), "2026-01-01T00:00:00Z", post=api)
            info = res["info"]
            self.assertEqual((info["fills"]["count"], info["perp_dexes"], info["unresolved_spot_coins"]), (5, 2, 0))
            h = hl.load(d)[0]
            self.assertEqual([f["liquidation"]["is_self"] for f in h["fills"] if f.get("liquidation")],
                             [True, False])
            raw = ""
            for name in ("fills", "info"):
                with open(hl.addr_dir(d, ADDR) + "/%s.json" % name) as fh:
                    raw += fh.read()
            self.assertNotIn(ADDR[2:], raw)
            self.assertNotIn(OTHER[2:], raw)
            self.assertEqual(sorted(h["clearinghouse"]), ["dexa", "main"])
        self.assertIn("error", hl.fetch("/nonexistent", "0x123", "2026-01-01T00:00:00Z", post=api))


if __name__ == "__main__":
    unittest.main()
