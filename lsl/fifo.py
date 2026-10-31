"""FIFO lot matching shared by Robinhood and Hyperliquid.

match(fills) consumes fills sorted by time for ONE instrument and returns
close records (one per closing fill) plus the remaining open lots.

A fill: ts (datetime), side buy|sell, qty>0, px, fee (USD),
        eff open|close|None, ref, airdrop (bool, optional), shorts (bool)
  * eff None:  buy closes shorts first then opens long; sell closes longs first,
               then opens a short only when shorts=True (derivatives), else the
               excess is "unmatched" (sold something we never saw bought).
  * eff open:  always opens (buy -> long, sell -> short).
  * eff close: closes the opposite side; excess is unmatched.
"""
import math
from collections import deque

EPS = 1e-9


def match(fills):
    lots = deque()          # [sign, qty, px, ts, fee_per_unit, airdrop]
    closes = []
    for f in fills:
        q, sign = f["qty"], (1 if f["side"] == "buy" else -1)
        if q <= EPS:
            continue
        fee_pu = f.get("fee", 0.0) / q
        eff = f.get("eff")
        rem = q
        if eff != "open":
            parts, gross, held, open_fee, air = [], [], [], [], 0.0
            while rem > EPS and lots and lots[0][0] == -sign:
                lot = lots[0]
                take = min(rem, lot[1])
                # long closed by a sell: (exit - entry); short closed by a buy: (entry - exit)
                gross.append((f["px"] - lot[2]) * take * lot[0])
                held.append((f["ts"] - lot[3]).total_seconds() * take)
                open_fee.append(lot[4] * take)
                if lot[5]:
                    air += take
                parts.append(take)
                lot[1] -= take
                rem -= take
                if lot[1] <= EPS:
                    lots.popleft()
            closed = math.fsum(parts)
            if closed > EPS:
                closes.append({
                    "ts": f["ts"], "ref": f.get("ref"), "qty": closed,
                    "side": "long" if sign < 0 else "short",
                    "gross": math.fsum(gross),
                    "fees": math.fsum(open_fee) + fee_pu * closed,
                    "hold_s": math.fsum(held) / closed,
                    "airdrop_qty": air, "fill": f,
                })
            if rem > EPS and (eff == "close" or (sign < 0 and not f.get("shorts"))):
                closes.append({"ts": f["ts"], "ref": f.get("ref"), "qty": rem, "unmatched": True,
                               "side": "long" if sign < 0 else "short", "fill": f,
                               "fees": fee_pu * rem})
                rem = 0.0
        if rem > EPS:
            lots.append([sign, rem, f["px"], f["ts"], fee_pu, bool(f.get("airdrop"))])
    return closes, [list(l) for l in lots]


def net_open(lots):
    return math.fsum(l[0] * l[1] for l in lots)
