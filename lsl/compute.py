"""Deterministic compute core: raw RH/HL state -> summary.json, series.json,
open_positions.json, data_quality.json.

Every number a report may quote is produced here.  Inputs are the files the
fetchers wrote under RUN/state; the same inputs always give byte-identical
outputs (sorted keys, fsum, fixed rounding, no wall-clock reads).

Gains policy
  Robinhood: from each account's coverage start (first month get_realized_pnl
  reports closes) the per-symbol get_pnl_trade_history realized_gain is the
  source of truth.  FIFO closes from orders are matched to those rows (same
  symbol, same quantity, nearest time within 3 days, expiry rows preferring
  expiry closes); a matched close takes RH's gain and keeps the FIFO holding
  time.  Unmatched RH rows become trades on their own.  Before coverage, FIFO
  from orders is used and flagged approximate.
  Hyperliquid: closedPnl per fill is the source of truth; FIFO is used only
  for holding times and to exclude spot sells of airdropped (spotGenesis)
  tokens.
"""
import datetime as dt
import math
import os
from collections import defaultdict

from . import accounts as accounts_mod, assets, fifo, funfacts, page, persona, rhmodel, trend as trend_mod
from .util import F, UTC, et_day, et_wall_to_utc, iso, r2, r4, read_json, to_utc, write_json

SCHEMA = 1
CLASSES = ("equity", "option", "crypto")
STABLES = {"USDC", "USDE", "USDH", "USDT", "USDT0"}
MATCH_WINDOW = dt.timedelta(days=3)
LOOSE_WINDOW = dt.timedelta(seconds=60)
PNL_EPS = 0.001
TOP_N = 10
HOLD_BUCKETS = (("lt_1h", 3600), ("1h_1d", 86400), ("1d_7d", 7 * 86400),
                ("7d_30d", 30 * 86400), ("30d_180d", 180 * 86400), ("gt_180d", None))


def fs(xs):
    return math.fsum(xs)


def utc_day(t):
    return t.astimezone(UTC).strftime("%Y-%m-%d")


# ======================================================================= loading
def load_rh(run_dir):
    from . import rhsync
    run = rhsync.Run(run_dir)
    st = run.st
    if not st:
        return None
    S = st["streams"]
    accts = []
    for a in st["accounts"]:
        tag = a["tag"]
        mine = {k: v for k, v in S.items() if k.startswith(tag + ".")}
        streams, failed = defaultdict(lambda: defaultdict(int)), defaultdict(int)
        for sid, s in mine.items():
            streams[s["type"]][s["status"]] += 1
            for w in s.get("failed") or []:
                failed["%s:%s" % (s["type"], w[2] if len(w) > 2 else "?")] += 1
        hist = {}
        for sym, i in sorted(st["flags"].get("hist_index:%s" % tag, {}).items()):
            s = S.get("%s.hist.%d" % (tag, i))
            hist[sym] = {"rows": run.load_data("%s.hist.%d" % (tag, i), []),
                         "status": s["status"] if s else "missing"}
        optuni = {}
        for sid in sorted(mine):
            if mine[sid]["type"] == "optuni":
                optuni.update(run.load_data(sid, {}))
        num = str(a.get("account_number") or "")
        accts.append({
            "tag": tag, "kind": a.get("kind"), "mask": ("\u2026" + num[-4:]) if len(num) >= 4 else None,
            "coverage": st["flags"].get("coverage:%s" % tag),
            "orders": {c: sorted(rhsync._orders_of(run, tag, c), key=lambda o: o["id"]) for c in CLASSES},
            "positions": {c: run.load_data("%s.pos.%s" % (tag, c), []) for c in CLASSES},
            "pnl": {c: run.load_data("%s.pnl.%s" % (tag, c), None) for c in CLASSES},
            "hist": hist,
            "streams": {k: dict(v) for k, v in streams.items()},
            "failed": dict(failed),
            "optuni": optuni,
        })
    return {"sync_end": st["sync_end"], "accounts": accts}


def load_hl(run_dir):
    """[] or one dict per synced Hyperliquid address (see hl.load)."""
    from . import hl
    return hl.load(run_dir)


def sources(run_dir):
    """What `compute` would use: RH yes/no, the synced HL addresses (short form only), report scopes."""
    from . import hl, scope as scope_mod
    rh = load_rh(run_dir)
    cfg = hl.load_config(run_dir) or []
    hs = load_hl(run_dir)
    synced = {h["fingerprint"] for h in hs}
    lab = hl.labels(cfg)
    return {"rh": bool(rh), "hl_addresses": len(cfg), "hl_synced": len(hs),
            "addresses": [{"label": lab[e["address"]], "short": hl.short(e["address"]),
                           "synced": hl.fingerprint(e["address"]) in synced} for e in cfg],
            "legacy_hl": bool(hs and not cfg),
            "scopes": scope_mod.available(rh, hs),
            "any": bool(rh) or bool(hs)}


# ======================================================================= Robinhood
def _fifo_events(tag, fills):
    """FIFO per instrument -> close events aggregated per (class, key, order)."""
    groups = defaultdict(list)
    for f in fills:
        groups[(f["cls"], f["key"] or f["sym"])].append(f)
    events, open_net = [], {}
    for (cls, key) in sorted(groups, key=lambda k: (k[0], str(k[1]))):
        seq = [dict(f, ts=to_utc(f["ts"]), eff=f["eff"] if cls == "option" else None)
               for f in groups[(cls, key)]]
        closes, lots = fifo.match(seq)
        open_net[(cls, key)] = (fifo.net_open(lots), groups[(cls, key)][-1])
        agg = {}
        for c in closes:
            f = c["fill"]
            k = (c["ref"], bool(c.get("unmatched")))
            e = agg.get(k)
            if e is None:
                e = agg[k] = {"src": "rh", "acct": tag, "cls": cls, "key": key, "sym": f["sym"],
                              "label": f.get("label"), "ref": c["ref"], "ts": c["ts"],
                              "expiry": bool(f.get("expiry")), "side": c["side"],
                              "unmatched": bool(c.get("unmatched")),
                              "_q": [], "_g": [], "_h": []}
            e["ts"] = max(e["ts"], c["ts"])
            e["_q"].append(c["qty"])
            if not c.get("unmatched"):
                e["_g"].append(c["gross"])
                e["_h"].append(c["hold_s"] * c["qty"])
        for k in sorted(agg, key=lambda k: (str(k[0]), k[1])):
            e = agg.pop(k)
            q = fs(e.pop("_q"))
            g, h = e.pop("_g"), e.pop("_h")
            e.update(qty=q, gross=fs(g) if g else None, hold_s=(fs(h) / q) if h and q else None)
            events.append(e)
    events.sort(key=lambda e: (e["ts"], e["cls"], str(e["key"]), str(e["ref"])))
    return events, open_net


def _row_class(row, sym, evs_by_sym):
    if not row.get("side"):
        return "option"
    have = sorted({e["cls"] for e in evs_by_sym.get(sym, [])})
    if len(have) == 1:
        return have[0]
    return "equity" if "equity" in have or not have else have[0]


def _qty_ok(ev, rq, remaining):
    mult = rhmodel.OPTION_MULTIPLIER if ev["cls"] == "option" else 1.0
    need = rq * mult
    return remaining + 1e-6 * max(1.0, need) >= need, abs(remaining - need) <= 1e-6 * max(1.0, need)


def rh_account(a, sync_end):
    tag = a["tag"]
    fills = []
    for cls in CLASSES:
        fills += rhmodel.fills_for(cls, tag, a["orders"].get(cls, []))
    fills = rhmodel.sort_fills(rhmodel.add_expiry_closes(fills, sync_end))
    events, open_net = _fifo_events(tag, fills)
    cov = a.get("coverage")
    cov_start = et_wall_to_utc(cov + "-01", 0) if cov else None
    by_sym = defaultdict(list)
    for e in events:
        by_sym[e["sym"]].append(e)
    q = defaultdict(int)
    trades, matched_rows = [], {}
    rem = {id(e): e["qty"] for e in events}
    gains = defaultdict(list)
    oldest_row = {}
    for sym in sorted(a.get("hist", {})):
        rows = sorted(a["hist"][sym]["rows"],
                      key=lambda r: (r.get("timestamp", ""), r.get("side", ""), r.get("quantity", ""),
                                     r.get("realized_gain", "")))
        if rows:
            oldest_row[sym] = to_utc(rows[0]["timestamp"])
        for r in rows:
            q["rh_rows"] += 1
            t = to_utc(r["timestamp"])
            rq = F(r.get("quantity"))
            exp_row = not r.get("side")
            best = None
            for e in by_sym.get(sym, []):
                if abs(e["ts"] - t) > MATCH_WINDOW:
                    continue
                fits, exact = _qty_ok(e, rq, rem[id(e)])
                d = abs((e["ts"] - t).total_seconds())
                if not fits and d > LOOSE_WINDOW.total_seconds():
                    continue
                # RH books some expirations as real orders, so kinds may differ
                rank = (0 if exact else 1 if fits else 2, int(e["expiry"] != exp_row), d, str(e["ref"]))
                if best is None or rank < best[0]:
                    best = (rank, e)
            if best:
                e = best[1]
                mult = rhmodel.OPTION_MULTIPLIER if e["cls"] == "option" else 1.0
                rem[id(e)] = max(0.0, rem[id(e)] - rq * mult)
                gains[id(e)].append(F(r.get("realized_gain")))
                matched_rows[id(e)] = e
                q["rh_rows_matched"] += 1
                if best[0][0] == 2:
                    q["rh_rows_loose_matched"] += 1
            else:
                q["rh_rows_unmatched"] += 1
                trades.append({"src": "rh", "acct": tag, "cls": _row_class(r, sym, by_sym), "sym": sym,
                               "label": None, "ts": t, "qty": rq, "pnl": F(r.get("realized_gain")),
                               "gain_source": "rh", "approx": False, "hold_s": None,
                               "side": "short" if r.get("side") == "buy" else "long",
                               "expiry": exp_row, "liquidation": False})
    for e in events:
        base = {"src": "rh", "acct": tag, "cls": e["cls"], "sym": e["sym"], "label": e["label"],
                "ts": e["ts"], "qty": e["qty"], "hold_s": e["hold_s"], "side": e["side"],
                "expiry": e["expiry"], "liquidation": False}
        if id(e) in gains:
            trades.append(dict(base, pnl=fs(gains[id(e)]), gain_source="rh", approx=False))
            q["fifo_closes_rh_sourced"] += 1
            continue
        if e["unmatched"]:
            q["closes_without_cost_basis"] += 1
            continue
        if cov_start is None or e["ts"] < cov_start:
            trades.append(dict(base, pnl=e["gross"], gain_source="fifo", approx=True))
            q["fifo_closes_before_coverage"] += 1
            continue
        h = a.get("hist", {}).get(e["sym"])
        if h is None:
            trades.append(dict(base, pnl=e["gross"], gain_source="fifo", approx=True))
            q["fifo_closes_no_history_stream"] += 1
        elif h["status"] != "done" and (e["sym"] not in oldest_row or e["ts"] < oldest_row[e["sym"]]):
            trades.append(dict(base, pnl=e["gross"], gain_source="fifo", approx=True))
            q["fifo_closes_kept_partial_history"] += 1
        else:
            q["fifo_closes_dropped_in_coverage"] += 1
    acts = [{"src": "rh", "acct": tag, "cls": f["cls"], "sym": f["sym"], "ts": to_utc(f["ts"]),
             "fee": f["fee"], "liquidation": False, "notional": abs(f["qty"] * f["px"])}
            for f in fills if not f.get("expiry")]
    for t in trades:
        t["day"] = et_day(t["ts"])
    for f in acts:
        f["day"] = et_day(f["ts"])
    dq = {"tag": tag, "kind": a.get("kind"), "coverage_start": cov,
          "orders": {c: len(a["orders"].get(c, [])) for c in CLASSES},
          "fills": {c: sum(1 for f in acts if f["cls"] == c) for c in CLASSES},
          "expiry_closes": sum(1 for f in fills if f.get("expiry")),
          # statuses seen per stream type (window counts depend on full vs incremental sync shape)
          "streams": {t: sorted(v) for t, v in sorted((a.get("streams") or {}).items())},
          "possible_tie_gaps": sum(v for k, v in a.get("failed", {}).items() if k.endswith(":TIE_OVERFLOW")),
          "failed_windows": sum(v for k, v in a.get("failed", {}).items()
                                if not k.endswith(":TIE_OVERFLOW") and not k.startswith("optuni:")),
          "history": {"symbols": len(a.get("hist", {})),
                      "complete": sum(1 for h in a.get("hist", {}).values() if h["status"] == "done"),
                      "rows": sum(len(h["rows"]) for h in a.get("hist", {}).values())},
          "gains": dict(sorted(q.items())),
          "pnl_reconciliation": _rh_pnl_recon(a, sync_end, trades),
          "positions_reconciliation": _rh_pos_recon(a, open_net),
          "option_universe": _optuni_check(a, open_net)}
    return trades, acts, dq, _rh_open_positions(a)


def _rh_pnl_recon(a, sync_end, trades):
    cov = a.get("coverage")
    out = {"months_compared": 0, "count_match": 0, "count_under": 0, "count_over": 0,
           "gain_match": 0, "classes": 0}
    for cls in CLASSES:
        p = a["pnl"].get(cls)
        if not p:
            continue
        out["classes"] += 1
        rh = rhmodel.pnl_buckets(p.get("data_points") or [], True)
        ours_n = rhmodel.closing_counts(cls, a["orders"].get(cls, []), sync_end, daily=False)
        ours_g = defaultdict(list)
        for t in trades:
            if t["cls"] == cls and t["gain_source"] == "rh":
                ours_g[t["day"][:7]].append(t["pnl"])
        for m in sorted(rh):
            if cov and m < cov:
                continue
            n, g = rh[m]
            if not n and not g:
                continue
            out["months_compared"] += 1
            on = ours_n.get(m, 0)
            out["count_match"] += int(on == n)
            out["count_under"] += int(on < n)     # RH saw closes we have no order for
            out["count_over"] += int(on > n)      # e.g. expiries RH books differently
            mine = fs(ours_g.get(m, []))
            out["gain_match"] += int(abs(mine - g) <= max(1.0, 0.005 * abs(g)))
    out["count_pass"] = out["count_under"] == 0
    out["gain_pass"] = out["gain_match"] == out["months_compared"]
    return out


def _rh_pos_recon(a, open_net):
    fifo_eq, fifo_opt, fifo_cr = defaultdict(float), {}, defaultdict(float)
    for (cls, key), (net, f) in open_net.items():
        if cls == "equity":
            fifo_eq[f["sym"]] += net
        elif cls == "option":
            fifo_opt[key] = net / rhmodel.OPTION_MULTIPLIER
        else:
            fifo_cr[f["sym"]] += net
    res = {"compared": 0, "match": 0, "mismatch": 0, "fifo_open_without_position": 0}

    def cmp(mine, theirs):
        res["compared"] += 1
        ok = abs(mine - theirs) <= 1e-6 * max(1.0, abs(theirs))
        res["match" if ok else "mismatch"] += 1

    seen_eq, seen_opt, seen_cr = set(), set(), set()
    for p in a["positions"].get("equity") or []:
        s = p.get("symbol")
        seen_eq.add(s)
        cmp(fifo_eq.get(s, 0.0), F(p.get("quantity")))
    for p in a["positions"].get("option") or []:
        k = p.get("option_id")
        seen_opt.add(k)
        sign = -1 if p.get("type") == "short" else 1
        cmp(fifo_opt.get(k, 0.0), sign * F(p.get("quantity")))
    for p in a["positions"].get("crypto") or []:
        s = rhmodel.crypto_symbol(p) or p.get("code")
        seen_cr.add(s)
        cmp(fifo_cr.get(s, 0.0), F(p.get("quantity") or p.get("quantity_available")))
    for d, seen in ((fifo_eq, seen_eq), (fifo_opt, seen_opt), (fifo_cr, seen_cr)):
        res["fifo_open_without_position"] += sum(1 for k, v in d.items() if abs(v) > 1e-6 and k not in seen)
    res["pass"] = res["mismatch"] == 0 and res["fifo_open_without_position"] == 0
    return res


def _optuni_check(a, open_net):
    """Optional cross-check against get_option_positions(nonzero=false)."""
    st = (a.get("streams") or {}).get("optuni", {})
    recs = a.get("optuni") or {}
    out = {"enabled": bool(st), "streams": dict(sorted(st.items())), "contracts": len(recs),
           "compared": 0, "match": 0, "mismatch": 0, "not_in_universe": 0}
    if not recs:
        return out
    uni = defaultdict(float)
    for r in recs.values():
        sign = -1 if r.get("type") == "short" else 1
        uni[r.get("option_id")] += sign * F(r.get("quantity"))
    for (cls, key), (net, f) in sorted(open_net.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        if cls != "option":
            continue
        if key not in uni:
            out["not_in_universe"] += 1
            continue
        out["compared"] += 1
        ok = abs(net / rhmodel.OPTION_MULTIPLIER - uni[key]) <= 1e-6
        out["match" if ok else "mismatch"] += 1
    complete = set(st) <= {"done", "split"}
    out["coverage"] = "complete" if complete else "partial"
    return out


def _rh_open_positions(a):
    out = []
    for p in a["positions"].get("equity") or []:
        if F(p.get("quantity")):
            out.append({"src": "rh", "acct": a["tag"], "cls": "equity", "sym": p.get("symbol"),
                        "side": "long" if F(p.get("quantity")) > 0 else "short",
                        "qty": r4(abs(F(p.get("quantity")))), "avg_price": r4(F(p.get("average_buy_price")))})
    for p in a["positions"].get("option") or []:
        if F(p.get("quantity")):
            mult = F(p.get("trade_value_multiplier")) or rhmodel.OPTION_MULTIPLIER
            out.append({"src": "rh", "acct": a["tag"], "cls": "option", "sym": p.get("chain_symbol"),
                        "side": p.get("type"), "qty": r4(F(p.get("quantity"))),
                        "expiration": p.get("expiration_date"),
                        "avg_price": r4(F(p.get("average_price")) / mult)})
    for p in a["positions"].get("crypto") or []:
        qy = F(p.get("quantity") or p.get("quantity_available"))
        if qy:
            out.append({"src": "rh", "acct": a["tag"], "cls": "crypto",
                        "sym": rhmodel.crypto_symbol(p) or p.get("code"), "side": "long",
                        "qty": r4(qy), "avg_price": None})
    return out


# ======================================================================= Hyperliquid
def hl_process(h, whitelist=None, acct="hl"):
    names = h.get("spot_names") or {}
    wl = assets.SpotWhitelist(h.get("spot_meta"), whitelist or assets.load_whitelist())
    raw = sorted(h.get("fills") or [], key=lambda f: (int(f.get("time", 0)), str(f.get("tid"))))
    q = defaultdict(int)
    norm = []
    junk_orders, junk_reasons = set(), defaultdict(int)
    for f in raw:
        coin = str(f.get("coin"))
        spot = coin.startswith("@") or ("/" in coin and ":" not in coin)
        sym = names.get(coin, coin)
        if f.get("dir") == "Spot Dust Conversion":
            q["dust_fills_excluded"] += 1
            continue
        canon = None
        if spot:
            canon, why = wl.resolve(coin)
            if canon is None:
                # only HYPE + Unit-bridged tokens count; everything else is junk
                q["spot_fills_excluded_not_whitelisted"] += 1
                junk_reasons[why] += 1
                if abs(F(f.get("closedPnl"))) > PNL_EPS:
                    junk_orders.add((coin, str(f.get("oid"))))
                continue
        tok = str(f.get("feeToken") or "USDC")
        px, sz, fee = F(f.get("px")), F(f.get("sz")), F(f.get("fee"))
        if tok.upper() in STABLES:
            fee_usd = fee
        else:
            fee_usd = fee * px
            q["fees_converted_at_fill_price"] += 1
        liq = f.get("liquidation") if isinstance(f.get("liquidation"), dict) else None
        own_liq = bool(liq and liq.get("is_self", True))
        if liq and not own_liq:
            q["liquidation_fills_as_counterparty"] += 1
        norm.append({"f": f, "coin": coin, "spot": spot, "sym": sym, "canon": canon,
                     "inst": sym.split("/")[0] if spot else coin,
                     "ts": dt.datetime.fromtimestamp(int(f["time"]) / 1000.0, UTC),
                     "side": "buy" if f.get("side") == "B" else "sell", "qty": sz, "px": px,
                     "fee": fee_usd, "pnl": F(f.get("closedPnl")), "liq": own_liq,
                     "adl": f.get("dir") == "Auto-Deleveraging"})
    # FIFO per instrument (spot: airdropped spotGenesis lots enter at zero cost)
    groups = defaultdict(list)
    for n in norm:
        groups[("spot" if n["spot"] else "perp", n["inst"])].append(n)
    genesis = defaultdict(list)
    for u in h.get("ledger") or []:
        d = u.get("delta") or {}
        if d.get("type") == "spotGenesis":
            genesis[str(d.get("token"))].append(
                {"ts": dt.datetime.fromtimestamp(int(u["time"]) / 1000.0, UTC), "side": "buy",
                 "qty": F(d.get("amount")), "px": 0.0, "fee": 0.0, "airdrop": True, "ref": "GENESIS"})
    q["airdrop_genesis_events"] = sum(len(v) for v in genesis.values())
    close_by_fill = {}
    for (kind, inst) in sorted(groups):
        seq = []
        for i, n in enumerate(groups[(kind, inst)]):
            seq.append({"ts": n["ts"], "side": n["side"], "qty": n["qty"], "px": n["px"],
                        "fee": 0.0, "ref": id(n), "shorts": kind == "perp"})
        if kind == "spot":
            seq = sorted(genesis.get(inst, []) + seq, key=lambda x: (x["ts"], x["ref"] != "GENESIS"))
        closes, _ = fifo.match(seq)
        for c in closes:
            if c.get("unmatched"):
                continue
            close_by_fill.setdefault(c["ref"], []).append(c)
    # closed trades: fills with nonzero closedPnl, aggregated per (coin, order id)
    agg, acts = {}, []
    for n in norm:
        cs = close_by_fill.get(id(n), [])
        air = fs(c["airdrop_qty"] for c in cs)
        frac = min(1.0, air / n["qty"]) if n["qty"] else 0.0
        if frac >= 0.999:
            q["airdrop_sell_fills_excluded"] += 1
            continue
        if frac > 0:
            q["airdrop_sell_fills_partial"] += 1
        day = utc_day(n["ts"])
        acts.append({"src": "hl", "acct": acct, "cls": "spot" if n["spot"] else "perp", "sym": n["sym"],
                     "canon": n["canon"], "ts": n["ts"], "day": day, "fee": n["fee"], "liquidation": n["liq"],
                     "notional": abs(n["qty"] * n["px"])})
        if abs(n["pnl"]) <= PNL_EPS:
            continue
        k = (n["coin"], str(n["f"].get("oid")))
        e = agg.get(k)
        if e is None:
            e = agg[k] = {"src": "hl", "acct": acct, "cls": "spot" if n["spot"] else "perp",
                          "sym": n["sym"], "canon": n["canon"], "label": None, "ts": n["ts"], "_p": [], "_q": [], "_h": [],
                          "_hq": [], "liquidation": False, "adl": False, "expiry": False,
                          "side": "long" if n["side"] == "sell" else "short",
                          "gain_source": "hl", "approx": False}
        e["ts"] = max(e["ts"], n["ts"])
        e["_p"].append(n["pnl"] * (1 - frac))
        e["_q"].append(n["qty"])
        for c in cs:
            if not c["airdrop_qty"]:
                e["_h"].append(c["hold_s"] * c["qty"])
                e["_hq"].append(c["qty"])
        e["liquidation"] = e["liquidation"] or n["liq"]
        e["adl"] = e["adl"] or n["adl"]
    trades = []
    for k in sorted(agg):
        e = agg[k]
        hq = fs(e.pop("_hq"))
        hs = e.pop("_h")
        e.update(pnl=fs(e.pop("_p")), qty=fs(e.pop("_q")), hold_s=(fs(hs) / hq) if hq else None,
                 day=utc_day(e["ts"]))
        trades.append(e)
    fund = h.get("funding") or []
    funding = fs(F((u.get("delta") or {}).get("usdc")) for u in fund)
    ledger_types = defaultdict(int)
    for u in h.get("ledger") or []:
        ledger_types[str((u.get("delta") or {}).get("type"))] += 1
    q["spot_trades_excluded_not_whitelisted"] = len(junk_orders)
    info = h.get("info") or {}
    perps, spots, spot_junk = _hl_open_positions(h, wl, acct)
    dq = {"spot_whitelist": {"have_spot_meta": wl.have_meta,
                             "spot_fills": sum(1 for f in raw if str(f.get("coin", "")).startswith("@")
                                               or ("/" in str(f.get("coin", "")) and ":" not in str(f.get("coin", "")))), "token_id_mismatch": len(wl.id_mismatch),
                             "excluded_fill_reasons": dict(sorted(junk_reasons.items())),
                             "excluded_spot_balances": spot_junk},
          "fills_raw": len(raw), "fills_used": len(acts), "closed_trades": len(trades),
          "liquidation_fills_self": sum(1 for n in norm if n["liq"]),
          "adl_fills": sum(1 for n in norm if n["adl"]),
          "funding_events": len(fund), "ledger_events": dict(sorted(ledger_types.items())),
          "fetch": _fetch_public(info),
          "counts": dict(sorted(q.items()))}
    return trades, acts, funding, dq, (perps, spots)


def _fetch_public(info):
    """Completeness facts only; per-sync call shapes (pages, new rows, sync count)
    differ between a full and an incremental fetch and are left out."""
    out = {}
    for k, v in sorted(info.items()):
        if k in ("address_fingerprint", "syncs"):
            continue
        if isinstance(v, dict):
            v = {a: b for a, b in v.items() if a not in ("pages", "new")}
        out[k] = v
    return out


def _hl_open_positions(h, wl, acct="hl"):
    perps, spots = [], []
    chs = h.get("clearinghouse") or {}
    if "assetPositions" in chs:            # single-dex layout
        chs = {"main": chs}
    for dex in sorted(chs):
        for ap in (chs[dex] or {}).get("assetPositions") or []:
            p = ap.get("position") or {}
            szi = F(p.get("szi"))
            if not szi:
                continue
            val = F(p.get("positionValue"))
            mark = val / abs(szi) if szi else None
            liq = F(p.get("liquidationPx")) if p.get("liquidationPx") not in (None, "") else None
            lev = p.get("leverage") or {}
            perps.append({"src": "hl", "acct": acct, "dex": dex, "coin": p.get("coin"),
                          "side": "long" if szi > 0 else "short", "size": r4(abs(szi)),
                          "entry_px": r4(F(p.get("entryPx"))), "mark_px": r4(mark),
                          "position_value": r2(val), "unrealized_pnl": r2(F(p.get("unrealizedPnl"))),
                          "roe": r4(F(p.get("returnOnEquity"))) if p.get("returnOnEquity") is not None else None,
                          "leverage": F(lev.get("value")) if isinstance(lev, dict) else F(lev),
                          "leverage_type": lev.get("type") if isinstance(lev, dict) else None,
                          "liquidation_px": r4(liq) if liq else None,
                          "liq_distance_pct": r4(abs(mark - liq) / mark) if (liq and mark) else None})
    junk = 0
    for b in (h.get("spot_clearinghouse") or {}).get("balances") or []:
        tot = F(b.get("total"))
        if not tot:
            continue
        tok = b.get("token")
        if tok in wl.quote or str(b.get("coin")).upper() in STABLES:
            continue                       # cash, not a position
        if not wl.token_ok(tok):
            junk += 1
            continue
        spots.append({"src": "hl", "acct": acct, "coin": b.get("coin"), "underlying": wl.underlying_of_token(tok),
                      "total": r4(tot), "entry_notional": r2(F(b.get("entryNtl")))})
    return perps, spots, junk


# ======================================================================= statistics
def _hold_bucket(s):
    for name, lim in HOLD_BUCKETS:
        if lim is None or s < lim:
            return name
    return HOLD_BUCKETS[-1][0]


def stats(trades, acts, funding=0.0):
    """Report numbers for one scope (all / rh / hl / an account)."""
    closed = [t for t in trades if abs(t["pnl"]) > PNL_EPS]
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] <= 0]
    days = sorted({f["day"] for f in acts} | {t["day"] for t in trades})
    realized = fs(t["pnl"] for t in trades)
    fees = fs(f["fee"] for f in acts)
    n_fills = len(acts)
    if days:
        span = max(1, (dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days)
    else:
        span = 0
    per_day = defaultdict(int)
    for f in acts:
        per_day[f["day"]] += 1
    peak_day = sorted(per_day.items(), key=lambda kv: (-kv[1], kv[0]))[0] if per_day else ("", 0)
    by_sym = defaultdict(int)
    for f in acts:
        by_sym[f["sym"]] += 1
    top = max(by_sym.values()) if by_sym else 0
    month_fills = defaultdict(int)
    for f in acts:
        month_fills[f["day"][:7]] += 1
    holds = sorted(t["hold_s"] for t in closed if t.get("hold_s") is not None)
    avg_win = fs(wins) / len(wins) if wins else 0.0
    avg_loss = fs(losses) / len(losses) if losses else 0.0
    liq_fills = sum(1 for f in acts if f.get("liquidation"))
    return {
        "period": {"from": days[0] if days else None, "to": days[-1] if days else None, "days": span},
        "fills": n_fills,
        "closed_trades": len(closed),
        "unique_assets": len(by_sym),
        "wins": len(wins), "losses": len(losses),
        "win_rate": r4(len(wins) / len(closed)) if closed else 0.0,
        "avg_win": r2(avg_win), "avg_loss": r2(avg_loss),
        "profit_factor_ratio": r2(avg_win / abs(avg_loss)) if avg_loss else None,
        "realized_pnl": r2(realized), "fees": r2(fees), "net_pnl": r2(realized - fees),
        "funding": r2(funding), "net_pnl_incl_funding": r2(realized - fees + funding),
        "liquidations": liq_fills,
        "liquidated_trades": sum(1 for t in closed if t.get("liquidation")),
        "avg_fills_per_day": r4(n_fills / span) if span else 0.0,
        "peak_fills_in_one_day": peak_day[1], "peak_fills_date": peak_day[0] or None,
        "concentration": r4(top / n_fills) if n_fills else 0.0,
        "holding": {"trades_with_hold_time": len(holds),
                    "median_days": r4(holds[len(holds) // 2] / 86400) if holds else None,
                    "avg_days": r4(fs(holds) / len(holds) / 86400) if holds else None},
        "gain_sources": _gain_sources(trades),
        "_persona_in": {"fills": n_fills, "closed": len(closed),
                        "fpd": n_fills / span if span else 0.0,
                        "win_rate": len(wins) / len(closed) if closed else 0.0,
                        "avg_win": avg_win, "avg_loss": avg_loss, "unique_assets": len(by_sym),
                        "concentration": top / n_fills if n_fills else 1.0,
                        "liq_rate": liq_fills / n_fills if n_fills else 0.0,
                        "monthly_fill_counts": [month_fills[m] for m in sorted(month_fills)],
                        "net": realized - fees},
    }


def _gain_sources(trades):
    out = defaultdict(lambda: {"trades": 0, "pnl": []})
    for t in trades:
        k = t["gain_source"]
        out[k]["trades"] += 1
        out[k]["pnl"].append(t["pnl"])
    tot = len(trades)
    return {k: {"trades": v["trades"], "share": r4(v["trades"] / tot) if tot else 0.0,
                "realized_pnl": r2(fs(v["pnl"]))} for k, v in sorted(out.items())}


def _trade_row(t):
    return {"src": t["src"], "acct": t["acct"], "cls": t["cls"], "sym": t["sym"], "label": t.get("label"),
            "date": t["day"], "pnl": r2(t["pnl"]), "side": t.get("side"),
            "gain_source": t["gain_source"], "approx": t["approx"],
            "hold_days": r4(t["hold_s"] / 86400) if t.get("hold_s") is not None else None,
            "liquidation": bool(t.get("liquidation")), "expiry": bool(t.get("expiry"))}


def _trade_key(t):
    return (t["day"], t["src"], t["acct"], t["cls"], str(t["sym"]), str(t.get("label")), t["pnl"])


def best_worst(trades, n=TOP_N):
    closed = sorted((t for t in trades if abs(t["pnl"]) > PNL_EPS), key=lambda t: (t["pnl"], _trade_key(t)))
    worst = [_trade_row(t) for t in closed[:n] if t["pnl"] < 0]
    best = [_trade_row(t) for t in reversed(closed[-n:]) if t["pnl"] > 0]
    return best, worst


def by_asset(trades, acts):
    keys = sorted({(f["src"], f["cls"], str(f["sym"])) for f in acts} |
                  {(t["src"], t["cls"], str(t["sym"])) for t in trades})
    out = []
    T, A = defaultdict(list), defaultdict(list)
    for t in trades:
        T[(t["src"], t["cls"], str(t["sym"]))].append(t)
    for f in acts:
        A[(f["src"], f["cls"], str(f["sym"]))].append(f)
    for k in keys:
        ts, fl = T.get(k, []), A.get(k, [])
        closed = [t["pnl"] for t in ts if abs(t["pnl"]) > PNL_EPS]
        w = [p for p in closed if p > 0]
        l = [p for p in closed if p <= 0]
        realized, fees = fs(t["pnl"] for t in ts), fs(f["fee"] for f in fl)
        out.append({"src": k[0], "cls": k[1], "asset": k[2], "fills": len(fl), "closed_trades": len(closed),
                    "wins": len(w), "losses": len(l),
                    "win_rate": r4(len(w) / len(closed)) if closed else 0.0,
                    "avg_win": r2(fs(w) / len(w)) if w else 0.0, "avg_loss": r2(fs(l) / len(l)) if l else 0.0,
                    "biggest_win": r2(max(w)) if w else 0.0, "biggest_loss": r2(min(l)) if l else 0.0,
                    "realized_pnl": r2(realized), "fees": r2(fees), "net_pnl": r2(realized - fees),
                    "approx_trades": sum(1 for t in ts if t["approx"])})
    out.sort(key=lambda r: (r["net_pnl"], r["src"], r["cls"], r["asset"]))
    return out


def by_month(trades, acts):
    P, Fe, N, W, C = (defaultdict(list), defaultdict(list), defaultdict(int), defaultdict(int),
                      defaultdict(int))
    for t in trades:
        m = t["day"][:7]
        P[m].append(t["pnl"])
        if abs(t["pnl"]) > PNL_EPS:
            C[m] += 1
            W[m] += int(t["pnl"] > 0)
    for f in acts:
        Fe[f["day"][:7]].append(f["fee"])
        N[f["day"][:7]] += 1
    out, cum = [], []
    for m in sorted(set(P) | set(Fe)):
        net = fs(P[m]) - fs(Fe[m])
        cum.append(net)
        out.append({"month": m, "realized_pnl": r2(fs(P[m])), "fees": r2(fs(Fe[m])), "net_pnl": r2(net),
                    "fills": N[m], "closed_trades": C[m], "wins": W[m],
                    "win_rate": r4(W[m] / C[m]) if C[m] else 0.0, "cumulative": r2(fs(cum))})
    return out


def daily(trades, acts):
    P, Fe, N, C = defaultdict(list), defaultdict(list), defaultdict(int), defaultdict(int)
    for t in trades:
        P[t["day"]].append(t["pnl"])
        C[t["day"]] += int(abs(t["pnl"]) > PNL_EPS)
    for f in acts:
        Fe[f["day"]].append(f["fee"])
        N[f["day"]] += 1
    out, cum = [], []
    for d in sorted(set(P) | set(Fe)):
        net = fs(P[d]) - fs(Fe[d])
        cum.append(net)
        out.append({"day": d, "net_pnl": r2(net), "cumulative": r2(fs(cum)), "fills": N[d],
                    "closed_trades": C[d]})
    return out


def holding_buckets(trades):
    B = defaultdict(list)
    for t in trades:
        if abs(t["pnl"]) > PNL_EPS and t.get("hold_s") is not None:
            B[_hold_bucket(t["hold_s"])].append(t["pnl"])
    out = []
    for name, _ in HOLD_BUCKETS:
        ps = B.get(name, [])
        w = sum(1 for p in ps if p > 0)
        out.append({"bucket": name, "trades": len(ps), "wins": w,
                    "win_rate": r4(w / len(ps)) if ps else 0.0, "realized_pnl": r2(fs(ps))})
    return out


def weekday_activity(acts):
    c = [0] * 7
    for f in acts:
        c[dt.date.fromisoformat(f["day"]).weekday()] += 1
    return [{"weekday": i, "fills": c[i]} for i in range(7)]


def _group_brief(g):
    return {k: g[k] for k in ("group", "symbol", "kind", "venue_count", "closed_trades", "win_rate",
                              "net_pnl")} | {"venues": [v["venue"] for v in g["venues"]]}


def _public(st):
    return {k: v for k, v in st.items() if not k.startswith("_")}


PERSONA_CODES = ("archetype", "modifiers", "ratings", "grade", "trend_basis")


def _scope_public(key, st, trend, lang):
    """A scope's public stats plus its own persona codes (labels come from the card/page lang).
    The scope's SMA trend comes from trend.by_account when the scope is one account."""
    tr = ((trend or {}).get("by_account") or {}).get(key)
    if tr is not None:
        tr = dict(tr, status=trend.get("status"))
    p = persona.persona(st["_persona_in"], lang, trend=tr)
    return dict(_public(st), persona={k: p[k] for k in PERSONA_CODES})


# ======================================================================= driver
asset_mod = assets


def trades_and_as_of(rh, hl):
    """Closed-trade records and the data as_of (the same ones compute_all uses), for candles."""
    trades, as_of = [], None
    if rh:
        as_of = rh["sync_end"]
        for a in rh["accounts"]:
            trades += rh_account(a, rh["sync_end"])[0]
    if isinstance(hl, dict):
        hl = [dict(hl, tag="hl", label="hl", fingerprint="")]
    for h in hl or []:
        trades += hl_process(h, acct=h.get("tag", "hl"))[0]
        ms_ = (h.get("info") or {}).get("as_of_ms")
        if ms_:
            hl_asof = iso(dt.datetime.fromtimestamp(ms_ / 1000.0, UTC))
            as_of = max(as_of, hl_asof) if as_of else hl_asof
    trades.sort(key=_trade_key)
    return trades, as_of


def compute_all(rh, hl, lang="zh", candles=None):
    from . import scope as scope_mod
    trades, acts, scopes, funding = [], [], {}, 0.0
    accts = []
    dq = {"schema": SCHEMA, "rh": None, "hl": None, "checks": []}
    open_rh, perps, spots = [], [], []
    as_of = None
    if rh:
        as_of = rh["sync_end"]
        dq["rh"] = {"accounts": []}
        for a in rh["accounts"]:
            t, f, adq, op = rh_account(a, rh["sync_end"])
            trades += t
            acts += f
            open_rh += op
            dq["rh"]["accounts"].append(adq)
            if t or f:
                scopes["rh." + a["tag"]] = (t, f, 0.0)
                labels = {l: scope_mod.rh_label(a, l) for l in ("en", "zh")}
                accts.append({"key": "rh." + a["tag"], "id": "rh:" + a["tag"], "venue": "rh",
                              "labels": labels, "label": labels.get(lang, labels["en"]),
                              "trades": t, "acts": f, "funding": 0.0})
        rt = [x for x in trades if x["src"] == "rh"]
        scopes["rh"] = (rt, [x for x in acts if x["src"] == "rh"], 0.0)
    if isinstance(hl, dict):
        hl = [dict(hl, tag="hl", label="hl", fingerprint="")]
    hl = hl or []
    multi = len(hl) > 1
    if hl:
        ht, hf, hdqs = [], [], []
        for h in hl:
            tag = h.get("tag", "hl") if multi else "hl"
            t, f, fund, hdq, (p, sp) = hl_process(h, acct=tag)
            ht += t
            hf += f
            perps += p
            spots += sp
            funding += fund
            hdqs.append(dict(hdq, tag=tag, label=h.get("label", tag)))
            if multi:
                scopes["hl." + tag] = (t, f, fund)
            if t or f:
                # nickname / short 0xabcd…1234; a legacy single-address layout has no address on file
                lab = (h.get("label") or h["short"]) if h.get("short") else "Hyperliquid"
                accts.append({"key": ("hl." + tag) if multi else "hl",
                              "id": ("hl:" + (h.get("short") or h.get("fingerprint") or tag)) if h.get("short") or multi
                              else "hl", "venue": "hl", "labels": {"en": lab, "zh": lab}, "label": lab,
                              "trades": t, "acts": f, "funding": fund})
            ms_ = (h.get("info") or {}).get("as_of_ms")
            if ms_:
                hl_asof = iso(dt.datetime.fromtimestamp(ms_ / 1000.0, UTC))
                as_of = max(as_of, hl_asof) if as_of else hl_asof
        trades += ht
        acts += hf
        scopes["hl"] = (ht, hf, funding)
        if multi:
            dq["hl"] = dict(_hl_dq_total(hdqs), addresses=hdqs)
        else:
            dq["hl"] = {k: v for k, v in hdqs[0].items() if k not in ("tag", "label")}
        if multi:
            perps.sort(key=lambda p: (p["acct"], p["dex"], str(p["coin"])))
    trades.sort(key=_trade_key)
    acts.sort(key=lambda f: (f["ts"], f["src"], f["acct"], f["cls"], str(f["sym"]), f["fee"]))
    overall = stats(trades, acts, funding)
    trend, trend_rows = trend_mod.build(trades, candles or {}, accts)
    pers = persona.persona(overall["_persona_in"], lang, trend=trend)
    best, worst = best_worst(trades)
    assets = by_asset(trades, acts)
    groups, gq = asset_mod.groups(trades, acts)
    dq["asset_grouping"] = gq
    months = by_month(trades, acts)
    profs = profiles(scopes, trades, acts, funding, as_of)
    for a in accts:
        a["slug"] = scope_mod.slug(a["id"])
    summary = {
        "schema": SCHEMA, "as_of": as_of, "lang": lang,
        "profile": profs,
        "sources": {"rh": bool(rh), "hl": bool(hl)},
        "hl_addresses": [{"tag": d["tag"], "label": d["label"]} for d in (dq["hl"] or {}).get("addresses", [])]
        if multi else [],
        "overall": _public(overall),
        "scopes": {k: _scope_public(k, stats(*v), trend, lang) for k, v in sorted(scopes.items())},
        "persona": pers,
        "trend": trend,
        "best_trades": best[:5], "worst_trades": worst[:5],
        "by_asset_worst": assets[:5], "by_asset_best": list(reversed(assets[-5:])),
        "asset_groups": {"count": len(groups), "multi_venue": gq["multi_venue_groups"],
                         "ambiguous": gq["ambiguous_count"],
                         "best": [_group_brief(g) for g in reversed(groups[-5:])],
                         "worst": [_group_brief(g) for g in groups[:5]]},
        "by_month_best": sorted(months, key=lambda m: (-m["net_pnl"], m["month"]))[:5],
        "by_month_worst": sorted(months, key=lambda m: (m["net_pnl"], m["month"]))[:5],
        "recent_months": months[-12:],
        "open_positions": {"rh": len(open_rh), "hl_perp": len(perps), "hl_spot": len(spots)},
        "approximate": {"trades": sum(1 for t in trades if t["approx"]),
                        "share": r4(sum(1 for t in trades if t["approx"]) / len(trades)) if trades else 0.0},
    }
    series = {
        "schema": SCHEMA, "as_of": as_of,
        "cumulative": {k: daily(v[0], v[1]) for k, v in sorted(dict(scopes, all=(trades, acts, 0)).items())},
        "monthly": {k: by_month(v[0], v[1]) for k, v in sorted(dict(scopes, all=(trades, acts, 0)).items())},
        "per_asset": assets,
        "asset_groups": groups,
        "activity": {"weekday": weekday_activity(acts)},
        "holding_times": holding_buckets(trades),
        "best_trades": best, "worst_trades": worst,
        "trend_entries": trend_rows,
    }
    hist_by = {(r["src"], r["asset"]): r for r in assets}
    for p in perps:
        r = hist_by.get(("hl", p["coin"]))
        p["asset_history"] = ({"closed_trades": r["closed_trades"], "win_rate": r["win_rate"],
                               "net_pnl": r["net_pnl"]} if r else None)
    for p in open_rh:
        r = hist_by.get(("rh", p["sym"]))
        p["asset_history"] = ({"closed_trades": r["closed_trades"], "win_rate": r["win_rate"],
                               "net_pnl": r["net_pnl"]} if r else None)
    open_pos = {"schema": SCHEMA, "as_of": as_of,
                "rh": sorted(open_rh, key=lambda p: (p["acct"], p["cls"], str(p["sym"]), str(p.get("expiration")))),
                "hl_perp": perps, "hl_spot": sorted(spots, key=lambda s: (str(s["coin"]), s["acct"]))}
    summary["fun"] = funfacts.build(dict(scopes, all=(trades, acts, funding)), profs)
    summary["accounts"] = accounts_mod.build(accts, overall, profs, open_pos)
    for row in summary["accounts"]["rows"]:
        row["trend"] = (trend.get("by_account") or {}).get(row["key"])
    dq["checks"] = _checks(dq)
    dq["pass"] = all(c["pass"] for c in dq["checks"] if c["required"])
    return {"summary.json": summary, "series.json": series, "open_positions.json": open_pos,
            "data_quality.json": dq}


def profiles(scopes, trades, acts, funding, as_of):
    """Trader profile for all, rh, hl and every account (rh.<tag>, hl.<tag> with several addresses)."""
    from . import profile as P
    day = as_of[:10] if as_of else None
    out = {"all": P.profile(trades, acts, funding, day)}
    for k in sorted(scopes):
        out[k] = P.profile(*scopes[k], day)
    return out


def _sum_tree(xs):
    """Add up per-address quality dicts: numbers sum, dicts merge key-wise."""
    out = {}
    for x in xs:
        for k, v in x.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[k] = out.get(k, 0) + v
            elif isinstance(v, dict):
                out.setdefault(k, []).append(v)
    return {k: (_sum_tree(v) if isinstance(v, list) else v) for k, v in sorted(out.items())}


def _hl_dq_total(hdqs):
    """Totals across addresses in the single-address dq shape (counts only; no fetch block)."""
    keys = ("fills_raw", "fills_used", "closed_trades", "liquidation_fills_self", "adl_fills",
            "funding_events", "ledger_events", "counts")
    tot = _sum_tree([{k: d[k] for k in keys} for d in hdqs])
    for k in keys:
        tot.setdefault(k, {} if k in ("ledger_events", "counts") else 0)
    w = _sum_tree([d["spot_whitelist"] for d in hdqs])
    w["have_spot_meta"] = all(d["spot_whitelist"]["have_spot_meta"] for d in hdqs)
    w.setdefault("excluded_fill_reasons", {})
    tot["spot_whitelist"] = w
    return tot


def _checks(dq):
    out = []

    def add(name, ok, required, detail):
        out.append({"name": name, "pass": bool(ok), "required": required, "detail": detail})

    if dq["rh"]:
        for a in dq["rh"]["accounts"]:
            tag = a["tag"]
            bad = {k: v for k, v in a["streams"].items()
                   if set(v) - {"done", "na", "skipped", "split", "partial"}}
            add("rh.%s.fetch_complete" % tag, not bad and a["failed_windows"] == 0, True,
                {"possible_tie_gaps": a["possible_tie_gaps"], "failed_windows": a["failed_windows"]})
            r = a["pnl_reconciliation"]
            add("rh.%s.no_missing_closes_vs_rh" % tag, r["count_pass"], True,
                {"months": r["months_compared"], "exact": r["count_match"],
                 "under": r["count_under"], "over": r["count_over"]})
            add("rh.%s.realized_gain_match_rh" % tag, r["gain_pass"], False,
                {"months": r["months_compared"], "match": r["gain_match"]})
            g = a["gains"]
            rows = g.get("rh_rows", 0)
            add("rh.%s.history_rows_matched" % tag,
                rows == 0 or g.get("rh_rows_matched", 0) / rows >= 0.95, False,
                {"rows": rows, "matched": g.get("rh_rows_matched", 0)})
            p = a["positions_reconciliation"]
            add("rh.%s.fifo_matches_positions" % tag, p["pass"], False,
                {k: p[k] for k in ("compared", "match", "mismatch", "fifo_open_without_position")})
    hls = []
    if dq["hl"]:
        hls = [("hl.%s" % a["tag"], a) for a in dq["hl"]["addresses"]] if "addresses" in dq["hl"] \
            else [("hl", dq["hl"])]
    for pre, h in hls:
        f = h["fetch"].get("fills", {})
        add(pre + ".fills_complete", not f.get("possibly_capped") and not f.get("hit_page_limit")
            and not f.get("possible_tie_gaps"), True,
            {k: f.get(k) for k in ("count", "possible_tie_gaps", "possibly_capped")})
        add(pre + ".spot_names_resolved", h["fetch"].get("unresolved_spot_coins", 0) == 0, True,
            {"unresolved": h["fetch"].get("unresolved_spot_coins", 0)})
        w = h["spot_whitelist"]
        add(pre + ".spot_whitelist_ids_match", (w["have_spot_meta"] or not w["spot_fills"])
            and w["token_id_mismatch"] == 0, True,
            {"have_spot_meta": w["have_spot_meta"], "token_id_mismatch": w["token_id_mismatch"]})
    g = dq.get("asset_grouping")
    if g:
        add("assets.all_grouped", g["ambiguous_count"] == 0, False,
            {"groups": g["groups"], "multi_venue": g["multi_venue_groups"], "ambiguous": g["ambiguous_count"]})
    return out


def scope_out(run_dir, scope="all"):
    """Default output dir for a scope (DATA/out/<slug>), resolving aliases like a full address."""
    from . import scope as scope_mod
    res = scope_mod.resolve(scope, load_rh(run_dir), load_hl(run_dir))
    sl = res[2]["slug"] if isinstance(res, tuple) else scope_mod.slug(scope)
    return os.path.join(run_dir, "out", sl)


def run(run_dir, out_dir=None, lang="zh", scope="all"):
    from . import scope as scope_mod
    rh = load_rh(run_dir)
    hl = load_hl(run_dir)
    if not rh and not hl:
        return {"error": "no sources: add a Hyperliquid address (`lsl hl add 0x...` then `lsl hl fetch`) "
                         "and/or sync Robinhood (`lsl rh plan --namespace ...`) first",
                "data_dir": os.path.abspath(run_dir), "sources": sources(run_dir)}
    res = scope_mod.resolve(scope, rh, hl, lang)
    if isinstance(res, dict):
        return res
    rh, hl, meta = res
    out_dir = out_dir or os.path.join(run_dir, "out", meta["slug"])
    from . import candles as candles_mod
    docs = compute_all(rh, hl, lang, candles=candles_mod.load_all(run_dir))
    docs["summary.json"]["scope"] = meta
    docs["page.json"] = page.build(docs["summary.json"], docs["series.json"],
                                   docs["open_positions.json"], docs["data_quality.json"], lang)
    for name in sorted(docs):
        write_json(os.path.join(out_dir, name), docs[name])
    dq = docs["data_quality.json"]
    return {"status": "done", "scope": meta["id"], "scope_label": meta["label"],
            "out": os.path.abspath(out_dir), "files": sorted(docs),
            "data_quality_pass": dq["pass"],
            "checks": [{"name": c["name"], "pass": c["pass"]} for c in dq["checks"]]}
