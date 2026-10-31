"""Synthetic fakes for tests: a fake Robinhood MCP (tool results rendered the
way write_to_file saves them, with gateway truncation) and a fake Hyperliquid
info API.  All data is generated; no real accounts, symbols or addresses."""
import datetime as dt
import json
import os
import random
import uuid

from lsl import rhsync
from lsl.rhpage import TRUNC_MARK, decode_cursor
from lsl.util import UTC, et_month, iso, to_utc

SYMS = ("TSTA", "TSTB", "TSTC", "TSTD")


def _uid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128)))


def _ts(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def equity_orders(rng, start, end, n, sym_pool=SYMS, dense=None):
    """n buy/sell round trips spread over [start, end); dense=(year, k) adds k more."""
    out = []
    span = (end - start).total_seconds()

    def pair(t0, t1, sym):
        q = float(rng.randint(1, 20))
        p0 = round(rng.uniform(10, 200), 2)
        p1 = round(p0 * rng.uniform(0.8, 1.3), 2)
        iid = "inst-" + sym
        for side, t, p in (("buy", t0, p0), ("sell", t1, p1)):
            out.append({"id": _uid(rng), "created_at": _ts(t), "last_transaction_at": _ts(t),
                        "side": side, "state": "filled", "symbol": sym, "instrument_id": iid,
                        "cumulative_quantity": "%g" % q, "average_price": "%.2f" % p, "fees": "0.00",
                        "executions": [{"timestamp": _ts(t), "quantity": "%g" % q, "price": "%.2f" % p}]})
    for _ in range(n):
        t0 = start + dt.timedelta(seconds=rng.uniform(0, span * 0.9))
        t1 = t0 + dt.timedelta(days=rng.uniform(0.01, 20))
        pair(t0, min(t1, end - dt.timedelta(seconds=1)), rng.choice(sym_pool))
    if dense:
        y, k = dense
        a, b = dt.datetime(y, 1, 1, tzinfo=UTC), dt.datetime(y, 12, 1, tzinfo=UTC)
        for _ in range(k):
            t0 = a + dt.timedelta(seconds=rng.uniform(0, (b - a).total_seconds()))
            pair(t0, t0 + dt.timedelta(hours=rng.uniform(1, 200)), rng.choice(sym_pool))
    return out


def eq_order(rng, t, sym, side, q, p):
    return {"id": _uid(rng), "created_at": _ts(t), "last_transaction_at": _ts(t), "side": side,
            "state": "filled", "symbol": sym, "instrument_id": "inst-" + sym,
            "cumulative_quantity": "%g" % q, "average_price": "%.2f" % p, "fees": "0.00",
            "executions": [{"timestamp": _ts(t), "quantity": "%g" % q, "price": "%.2f" % p}]}


def option_order(rng, t, sym, option_id, exp, side, effect, qty, px):
    return {"id": _uid(rng), "created_at": _ts(t), "chain_symbol": sym, "state": "filled",
            "processed_quantity": "%g" % qty, "trade_value_multiplier": "100.0000",
            "direction": "debit" if side == "buy" else "credit", "regulatory_fees": "0", "contract_fees": "0",
            "legs": [{"id": _uid(rng), "option_id": option_id, "expiration_date": exp, "side": side,
                      "position_effect": effect, "option_type": "call", "strike_price": "100.0000",
                      "ratio_quantity": 1,
                      "executions": [{"timestamp": _ts(t), "quantity": "%g" % qty, "price": "%.2f" % px}]}]}


class FakeRH:
    """Serves get_* calls from in-memory datasets with byte-limit truncation."""

    def __init__(self, orders, limit=4000, page=100, crypto_na=True, hist=None,
                 fail=None, mangle=None, positions=None, option_contracts=None, uni_limit=None,
                 pnl_from=None, accounts=None):
        self.accounts = accounts or [{"account_number": "TESTACCT1", "rhs_account_number": "RHS1",
                                      "brokerage_account_type": "individual"}]
        self.orders = orders          # {(account_number, cls): [order]}
        self.limit, self.page = limit, page
        self.crypto_na = crypto_na
        self.hist = hist or {}        # {symbol: [rows]}
        self.fail = fail              # predicate(tool, args) -> True => ERROR result
        self.mangle = mangle or (lambda tool, args, recs: False)
        self.positions = positions or {}
        self.option_contracts = option_contracts or []
        self.uni_limit = uni_limit
        self.pnl_from = pnl_from      # RH realized-P&L coverage starts this month
        self.calls = []

    def _render(self, data, list_key=None, limit=None):
        body = json.dumps({"data": data}, separators=(",", ":"))
        lim = limit or self.limit
        head = "result\n"
        if len(body) <= lim:
            return head + body + "\n"
        return head + body[:lim] + "\n[+ %d bytes at .content[].text]\n%s\n" % (len(body) - lim, TRUNC_MARK)

    def call(self, tool, args):
        assert tool in rhsync.READ_ONLY_TOOLS, tool
        self.calls.append((tool, json.dumps(args, sort_keys=True)))
        if self.fail and self.fail(tool, args):
            return "Error: upstream timeout\n"
        if tool == "get_accounts":
            return self._render({"accounts": self.accounts})
        if tool in rhsync.ORDER_TOOLS.values():
            cls = [c for c, t in rhsync.ORDER_TOOLS.items() if t == tool][0]
            acct = args.get("account_number") or args.get("rhs_account_number")
            if cls == "crypto" and self.crypto_na:
                return 'Error: {"detail": "No crypto account linked"} no crypto account linked\n'
            recs = self.orders.get((acct, cls), [])
            if "order_id" in args:
                r = [o for o in recs if o["id"] == args["order_id"]]
                return self._render(r[0] if r else {})
            lt = to_utc(decode_cursor(args["cursor"])[2:]) if args.get("cursor") else dt.datetime.max.replace(tzinfo=UTC)
            gte = to_utc(args["created_at_gte"]) if args.get("created_at_gte") else None
            sel = [o for o in recs if to_utc(o["created_at"]) < lt and (gte is None or to_utc(o["created_at"]) >= gte)]
            sel.sort(key=lambda o: o["created_at"], reverse=True)
            sel = sel[:self.page]
            key = rhsync.ORDER_KEYS[cls][0]
            if sel and self.mangle(tool, args, sel):
                body = json.dumps({"data": {key: sel}}, separators=(",", ":"))
                cut = body.find('"%s":[' % key) + len(key) + 4 + 60
                return "result\n" + body[:cut] + "\n%s\n" % TRUNC_MARK
            return self._render({key: sel, "next": None})
        if tool == "get_realized_pnl":
            cls = args["asset_classes"][0]
            num = {a["rhs_account_number"]: a["account_number"] for a in self.accounts}
            recs = self.orders.get((num.get(args.get("account_number"), "TESTACCT1"), cls), [])
            from lsl import rhmodel
            daily = "start_date" in args
            counts = rhmodel.closing_counts(cls, recs, "2100-01-01T00:00:00Z", daily=daily)
            if self.pnl_from:
                counts = {k: v for k, v in counts.items() if k[:7] >= self.pnl_from}
            if daily:
                counts = {k: v for k, v in counts.items() if args["start_date"] <= k <= args["end_date"]}
            pts = [{"start_time": k if daily else k + "-01", "end_time": k, "number_of_trades": v,
                    "realized_gain": "0", "rate_of_realized_gain": "0"} for k, v in sorted(counts.items())]
            return self._render({"data_points": pts, "realized_gain": "0"}, limit=10 ** 7)
        if tool == "get_pnl_trade_history":
            rows = self.hist.get(args["symbol"], [])
            i = int(args.get("cursor") or 0)
            nxt = str(i + 2) if i + 2 < len(rows) else ""
            return self._render({"trades": rows[i:i + 2], "next_cursor": nxt}, limit=10 ** 7)
        if tool == "get_option_positions" and args.get("nonzero") is False:
            lo, hi = args["expiration_date_gte"], args["expiration_date_lte"]
            sel = [c for c in self.option_contracts if lo <= c["expiration_date"] <= hi]
            return self._render({"results": sel}, limit=self.uni_limit or 10 ** 7)
        if tool in rhsync.POS_TOOLS.values():
            cls = [c for c, t in rhsync.POS_TOOLS.items() if t == tool][0]
            first = self.accounts[0]
            if (args.get("account_number") or args.get("rhs_account_number")) not in (
                    first["account_number"], first["rhs_account_number"]):
                return self._render({"results": [], "next": None}, limit=10 ** 7)
            return self._render({"results": self.positions.get(cls, []), "next": None}, limit=10 ** 7)
        raise AssertionError(tool)


def drive(run_dir, server, max_rounds=3000, max_calls=8, now="2026-01-01T00:00:00Z",
          option_universe=None, on_round=None):
    """The bot loop: plan -> execute verbatim -> save -> ingest, until done."""
    run = rhsync.Run(run_dir)
    for i in range(max_rounds):
        res = rhsync.plan(run, max_calls, now=to_utc(now), option_universe=option_universe,
                          namespace="test-robinhood")
        option_universe = None
        if res["status"] == "done":
            return res, i
        for c in res["calls"]:
            os.makedirs(os.path.dirname(c["save_as"]), exist_ok=True)
            with open(c["save_as"], "w") as f:
                f.write(server.call(c["tool"], c["args"]))
        rhsync.ingest(run)
        if on_round:
            on_round(run, i)
    raise AssertionError("did not finish")


class FakeHL:
    """Hyperliquid info API with page limits; fills may share timestamps."""

    def __init__(self, fills, funding=(), ledger=(), page=2000, fund_page=500, spot_meta=None, ch=None):
        self.fills = sorted(fills, key=lambda f: (f["time"], f["tid"]))
        self.funding, self.ledger = list(funding), list(ledger)
        self.page, self.fund_page = page, fund_page
        self.spot_meta = spot_meta or {"tokens": [], "universe": []}
        self.ch = ch or {}
        self.calls = []

    def __call__(self, body):
        self.calls.append(body)
        t = body["type"]
        if t == "userFillsByTime":
            sel = [f for f in self.fills if body["startTime"] <= f["time"] <= body["endTime"]]
            return [dict(f) for f in sel[:self.page]]
        if t in ("userFunding", "userNonFundingLedgerUpdates"):
            src = self.funding if t == "userFunding" else self.ledger
            sel = sorted((u for u in src if body["startTime"] <= u["time"] <= body["endTime"]),
                         key=lambda u: u["time"])
            return sel[:self.fund_page]
        if t == "spotMeta":
            return self.spot_meta
        if t == "clearinghouseState":
            return self.ch.get(body.get("dex", "main"), {"assetPositions": []})
        if t == "spotClearinghouseState":
            return {"balances": []}
        raise AssertionError(t)
