"""Robinhood raw records -> normalized fills, RH-style closing counts, pnl buckets.

A fill is a dict:
  ts      ISO UTC string            src   "rh"
  acct    account tag (a0, a1, ...) cls   equity | option | crypto
  key     instrument key (instrument_id / option_id / currency_pair_id)
  sym     display symbol (equity symbol, option underlying, crypto base)
  side    buy | sell                qty   shares / option shares / coins
  px      price per unit            ref   order id ("EXP:<option>" for expiries)
  eff     open | close | None       fee   USD
"""
import datetime as dt
from collections import defaultdict

from .util import F, et_day, et_month, et_wall_to_utc, iso, to_utc

OPTION_MULTIPLIER = 100.0


def et_month_of(ts):
    return et_month(ts)


def is_filled(cls, o):
    if cls == "option":
        return F(o.get("processed_quantity")) > 0
    return F(o.get("cumulative_quantity")) > 0


def crypto_symbol(o):
    for k in ("symbol", "asset_code", "currency_code"):
        v = o.get(k)
        if v:
            return str(v).split("-")[0].upper()
    cp = o.get("currency_pair") or {}
    if isinstance(cp, dict):
        v = cp.get("symbol") or (cp.get("asset_currency") or {}).get("code")
        if v:
            return str(v).split("-")[0].upper()
    return None


def history_symbol(cls, o):
    """Symbol used to filter get_pnl_trade_history for this order."""
    if cls == "equity":
        return o.get("symbol")
    if cls == "option":
        return o.get("chain_symbol") or o.get("underlying_symbol")
    return crypto_symbol(o)


def option_label(o, leg):
    t = (leg.get("option_type") or "").upper()[:1]
    k = leg.get("strike_price")
    e = (leg.get("expiration_date") or "")[:10]
    return "%s %s %s%s" % (o.get("chain_symbol") or "?", e, ("%g" % F(k)) if k else "?", t)


def fills_equity(tag, orders):
    out = []
    for o in orders:
        if not is_filled("equity", o):
            continue
        side = "buy" if o.get("side") == "buy" else "sell"
        ex = o.get("executions") or []
        if not ex:
            ex = [{"timestamp": o.get("last_transaction_at") or o["created_at"],
                   "quantity": o["cumulative_quantity"], "price": o.get("average_price")}]
        fee = F(o.get("fees"))
        tq = sum(F(x.get("quantity")) for x in ex) or 1.0
        for x in ex:
            q = F(x.get("quantity"))
            out.append({"ts": iso(x.get("timestamp") or o["created_at"]), "src": "rh", "acct": tag,
                        "cls": "equity", "key": o.get("instrument_id") or o.get("symbol"),
                        "sym": o.get("symbol") or "?", "side": side, "qty": q,
                        "px": F(x.get("price") or o.get("average_price")), "ref": o["id"],
                        "eff": None, "fee": fee * q / tq})
    return out


def fills_option(tag, orders):
    out = []
    for o in orders:
        if not is_filled("option", o):
            continue
        legs = o.get("legs") or []
        nx = sum(len(l.get("executions") or []) for l in legs) or 1
        fee = F(o.get("regulatory_fees")) + F(o.get("contract_fees")) + F(o.get("fees"))
        for leg in legs:
            oid = leg.get("option_id") or (leg.get("option") or "").rstrip("/").split("/")[-1]
            mult = F(leg.get("trade_value_multiplier")) or F(o.get("trade_value_multiplier")) or OPTION_MULTIPLIER
            for x in leg.get("executions") or []:
                out.append({"ts": iso(x["timestamp"]), "src": "rh", "acct": tag, "cls": "option",
                            "key": oid, "sym": o.get("chain_symbol") or "?",
                            "label": option_label(o, leg), "exp": (leg.get("expiration_date") or "")[:10] or None,
                            "side": leg.get("side"), "qty": F(x.get("quantity")) * mult,
                            "px": F(x.get("price")), "ref": o["id"],
                            "eff": leg.get("position_effect"), "fee": fee / nx})
    return out


def add_expiry_closes(fills, now, extra_exp=None):
    """Contracts still net-open after expiry get a zero-price close at 16:00 ET on
    the expiration date (RH counts these as closing trades)."""
    exp, net, meta = dict(extra_exp or {}), defaultdict(float), {}
    for f in fills:
        if f["cls"] != "option":
            continue
        if f.get("exp"):
            exp[f["key"]] = f["exp"]
        net[f["key"]] += f["qty"] if f["side"] == "buy" else -f["qty"]
        meta[f["key"]] = f
    out = list(fills)
    for k in sorted(net):
        q = net[k]
        d = exp.get(k)
        if not d or abs(q) < 1e-9:
            continue
        t = et_wall_to_utc(d, 16)
        if t < to_utc(now):
            m = meta[k]
            out.append({"ts": iso(t), "src": "rh", "acct": m["acct"], "cls": "option", "key": k,
                        "sym": m["sym"], "label": m.get("label"), "exp": d,
                        "side": "sell" if q > 0 else "buy", "qty": abs(q), "px": 0.0,
                        "ref": "EXP:" + k, "eff": "close", "fee": 0.0, "expiry": True})
    return out


def fills_crypto(tag, orders):
    out = []
    for o in orders:
        if not is_filled("crypto", o):
            continue
        q = F(o.get("cumulative_quantity"))
        px = F(o.get("average_price")) or F(o.get("price"))
        out.append({"ts": iso(o.get("last_transaction_at") or o.get("updated_at") or o["created_at"]),
                    "src": "rh", "acct": tag, "cls": "crypto", "key": o.get("currency_pair_id"),
                    "sym": crypto_symbol(o) or ("pair:" + str(o.get("currency_pair_id"))[:8]),
                    "side": o.get("side"), "qty": q, "px": px, "ref": o["id"], "eff": None,
                    "fee": F(o.get("fee") or o.get("fees"))})
    return out


def fills_for(cls, tag, orders):
    return {"equity": fills_equity, "option": fills_option, "crypto": fills_crypto}[cls](tag, orders)


def sort_fills(fills):
    return sorted(fills, key=lambda f: (f["ts"], f["ref"], f["key"] or "", f["side"] or "", f["qty"], f["px"]))


def closing_counts(cls, orders, now, daily=False, tag="x"):
    """RH-style closing-trade counts per ET month/day: equity sell + sell_short
    orders, crypto filled sells, option closing orders + expiries of open contracts."""
    fills = fills_for(cls, tag, orders)
    if cls == "option":
        fills = add_expiry_closes(fills, now)
    out = defaultdict(set)
    for f in fills:
        if f["eff"] == "close" or (f["eff"] is None and f["side"] == "sell"):
            out[(et_day if daily else et_month)(f["ts"])].add(f["ref"])
    return {b: len(s) for b, s in out.items()}


def bucket_day(start_time):
    s = str(start_time)
    if len(s) <= 10:
        return s[:10]
    if s.endswith("Z") or "+" in s[10:] or s[10:].count("-"):
        return et_day(s)
    return s[:10]


def pnl_buckets(points, monthly=True):
    """data_points -> {YYYY-MM or YYYY-MM-DD: (number_of_trades, realized_gain)}."""
    out = {}
    for p in points:
        d = bucket_day(p.get("start_time") or p.get("begins_at") or p.get("date"))
        k = d[:7] if monthly else d
        n0, g0 = out.get(k, (0, 0.0))
        out[k] = (n0 + int(F(p.get("number_of_trades"))), g0 + F(p.get("realized_gain")))
    return out
