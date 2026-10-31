"""lsl command line. All output is JSON on stdout.

DATA is a stable, persistent data dir (default ~/trading-journey-data, or $LSL_DATA_DIR):
DATA/state (sync state + parsed records), DATA/raw (saved MCP results), DATA/out.
It is kept between runs so later runs sync incrementally; `--run` is an alias.

  lsl rh plan    [--data-dir DATA] [--namespace NS] [--max N] [--now ISO]  next exact MCP calls
  lsl rh ingest  [--data-dir DATA]                          parse saved results, advance
  lsl rh status  [--data-dir DATA]                          progress + completeness
  lsl rh refresh [--data-dir DATA] [--namespace NS] [--now ISO]  start an incremental round
  lsl hl add     ADDRESS [--label NAME] [--data-dir DATA]   add a Hyperliquid address (0..n)
  lsl hl remove  ADDRESS|LABEL [--data-dir DATA]            drop one and delete its stored data
  lsl hl list    [--data-dir DATA]                          configured addresses + sync state
  lsl hl fetch   [--data-dir DATA] [--address 0x...] [--now ISO]  sync every address (incremental)
  lsl hl tokens  [--unit] [--match NAME]                    public spotMeta token index/tokenId lookup
  lsl sources    [--data-dir DATA]                          which sources have data (rh, hl addresses)
  lsl compute    [--data-dir DATA] [--scope SCOPE] [--out DIR] [--lang zh|en]
                 SCOPE: all (default) | rh | rh:<account> | hl | hl:<address>; outputs go to
                 DATA/out/<scope-slug>/ unless --out is given
  lsl page       [--out DIR | --data-dir DATA --scope SCOPE] [--lang en|zh] [--compact]   rebuild page.json
  lsl card       [--out DIR | --data-dir DATA --scope SCOPE] [--period all|90d|30d|7d]
                 [--percent-only] [--lang en|zh] [--png] [--per-account]
                                                     share card SVG (+PNG); --per-account adds
                                                     cards/<account-slug>.svg per account
  lsl purge      [--data-dir DATA] [--source rh|hl|all] [--yes]  wipe stored account data
  lsl selftest
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

from .util import UTC, dumps, to_utc


def _now(a):
    return to_utc(a.now) if getattr(a, "now", None) else dt.datetime.now(UTC)


def _scope_out(a):
    if a.out:
        return a.out
    from . import compute
    return compute.scope_out(a.run, a.scope)


def default_data_dir():
    return os.path.expanduser(os.environ.get("LSL_DATA_DIR") or "~/trading-journey-data")


def _data(sp):
    sp.add_argument("--data-dir", "--run", dest="run", default=None,
                    help="persistent data dir (default ~/trading-journey-data or $LSL_DATA_DIR)")


def purge(data_dir, source="all", yes=False):
    """Delete stored account data.  rh: state/rh_state.json + RH records + raw;
    hl: state/hl; all: state, raw and out.  Dry run unless yes."""
    d = os.path.abspath(data_dir)
    st = os.path.join(d, "state")
    if source == "all":
        targets = [os.path.join(d, x) for x in ("state", "raw", "out")]
    elif source == "hl":
        targets = [os.path.join(st, "hl")]
    else:
        targets = [os.path.join(d, "raw")]
        if os.path.isdir(st):
            targets += [os.path.join(st, x) for x in sorted(os.listdir(st)) if x != "hl"]
        targets.append(os.path.join(d, "out"))
    targets = [t for t in targets if os.path.exists(t)]
    if yes:
        for t in targets:
            shutil.rmtree(t) if os.path.isdir(t) else os.remove(t)
    return {"status": "purged" if yes else "dry_run", "data_dir": d, "source": source,
            "paths": [os.path.relpath(t, d) for t in targets],
            "hint": None if yes else "re-run with --yes to delete"}


def main(argv=None):
    p = argparse.ArgumentParser(prog="lsl")
    sub = p.add_subparsers(dest="cmd", required=True)
    rh = sub.add_parser("rh")
    rh.add_argument("action", choices=["plan", "ingest", "status", "refresh"])
    _data(rh)
    rh.add_argument("--max", type=int, default=8)
    rh.add_argument("--namespace", help="the Robinhood connector namespace found with GetDynamicTools")
    rh.add_argument("--now")
    rh.add_argument("--option-universe", choices=["on", "off"],
                    help="optional option-contract cross-check (default off)")
    hl = sub.add_parser("hl")
    hl.add_argument("action", choices=["add", "remove", "list", "fetch", "tokens"])
    hl.add_argument("target", nargs="?", help="add/remove: the address (remove also takes a label)")
    _data(hl)
    hl.add_argument("--address", action="append", default=[],
                    help="fetch: add this address first (repeatable), then sync all")
    hl.add_argument("--label", help="add: optional short name shown instead of 0x1234\u20265678")
    hl.add_argument("--unit", action="store_true", help="tokens: Unit-deployed tokens + HYPE")
    hl.add_argument("--match", action="append", default=[], help="tokens: exact token name")
    hl.add_argument("--now")
    c = sub.add_parser("compute")
    _data(c)
    c.add_argument("--out")
    c.add_argument("--scope", default="all", help="all | rh | rh:<account> | hl | hl:<address>")
    c.add_argument("--lang", default="zh", choices=["zh", "en"])
    pg = sub.add_parser("page")
    _data(pg)
    pg.add_argument("--out", help="dir holding the compute outputs (default DATA/out/<scope-slug>)")
    pg.add_argument("--scope", default="all")
    pg.add_argument("--lang", default="en", choices=["zh", "en"])
    pg.add_argument("--compact", action="store_true",
                    help="small payload for pasting into one Page write (fixed trims, see lsl/page.py compact)")
    cd = sub.add_parser("card")
    _data(cd)
    cd.add_argument("--out", help="dir holding the compute outputs (default DATA/out/<scope-slug>)")
    cd.add_argument("--scope", default="all")
    cd.add_argument("--lang", default="en", choices=["zh", "en"])
    cd.add_argument("--period", default="all", choices=["all", "90d", "30d", "7d"])
    cd.add_argument("--percent-only", action="store_true", help="hide dollar amounts")
    cd.add_argument("--png", action="store_true", help="also rasterize to PNG if a converter is installed")
    cd.add_argument("--per-account", action="store_true",
                    help="also one card per account (several accounts) into <out>/cards/<account-slug>.svg")
    so = sub.add_parser("sources")
    _data(so)
    pu = sub.add_parser("purge")
    _data(pu)
    pu.add_argument("--source", choices=["rh", "hl", "all"], default="all")
    pu.add_argument("--yes", action="store_true")
    sub.add_parser("selftest")
    a = p.parse_args(argv)
    if hasattr(a, "run") and a.run is None:
        a.run = default_data_dir()

    if a.cmd == "rh":
        from . import rhsync
        run = rhsync.Run(a.run)
        if a.action == "plan":
            ou = None if a.option_universe is None else a.option_universe == "on"
            res = rhsync.plan(run, a.max, now=_now(a), option_universe=ou, namespace=a.namespace)
        elif run.st is None:
            res = {"error": "no state; run `lsl rh plan` first"}
        elif a.action == "ingest":
            res = rhsync.ingest(run)
        elif a.action == "status":
            res = rhsync.status(run)
        else:
            if a.namespace:
                rhsync.set_namespace(run, a.namespace)
            if not run.st.get("namespace"):
                res = {"error": rhsync.NO_NAMESPACE}
            else:
                rhsync.start_incremental(run, _now(a))
                res = rhsync.plan(run, a.max)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "purge":
        sys.stdout.write(dumps(purge(a.run, a.source, a.yes)))
        return 0
    if a.cmd == "hl":
        from . import hl as hlmod
        if a.action == "tokens":
            res = hlmod.tokens(unit=a.unit, names=a.match)
        elif a.action == "add":
            res = hlmod.add(a.run, a.target or (a.address[0] if a.address else None), a.label)
        elif a.action == "remove":
            res = hlmod.remove(a.run, a.target) if a.target else {"error": "which address? `lsl hl remove 0x...`"}
        elif a.action == "list":
            res = hlmod.listing(a.run)
        else:
            res = None
            for addr in a.address + ([a.target] if a.target else []):
                r = hlmod.add(a.run, addr)
                if "error" in r:
                    res = r
                    break
            res = res or hlmod.fetch_all(a.run, _now(a))
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "compute":
        from . import compute
        res = compute.run(a.run, a.out, a.lang, a.scope)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "page":
        from . import page
        res = page.run(_scope_out(a), a.lang, a.compact)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "card":
        from . import card
        res = card.run(_scope_out(a), period=a.period, percent_only=a.percent_only, png=a.png, lang=a.lang,
                       per_account_cards=a.per_account)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "sources":
        from . import compute
        sys.stdout.write(dumps(compute.sources(a.run)))
        return 0
    if a.cmd == "selftest":
        import os
        import unittest
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suite = unittest.defaultTestLoader.discover(os.path.join(here, "tests"), top_level_dir=here)
        r = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if r.wasSuccessful() else 1
    return 2
