"""lsl command line. All output is JSON on stdout.

DATA is a stable, persistent data dir. Resolution, first match: `--data-dir`,
`$LSL_DATA_DIR`, `~/trading-journey-data` if that directory already exists,
otherwise `~/.trading-journey`. It holds state/ (sync cursors and parsed
records), raw/ (saved MCP results), out/ (computed files) and prefs.json
(report language). It is kept between runs so later runs sync incrementally;
`--run` is an alias.

  lsl rh plan    [--data-dir DATA] [--namespace NS] [--max N] [--now ISO]  next exact MCP calls
                 [--candles on|off]  also plan Robinhood stock-candle calls (default off)
  lsl rh ingest  [--data-dir DATA]                          parse saved results, advance
  lsl rh status  [--data-dir DATA]                          progress + completeness
  lsl rh refresh [--data-dir DATA] [--namespace NS] [--now ISO]  start an incremental round
  lsl hl add     ADDRESS [--label NAME] [--data-dir DATA]   add a Hyperliquid address (0..n)
  lsl hl remove  ADDRESS|LABEL [--data-dir DATA]            drop one and delete its stored data
  lsl hl list    [--data-dir DATA]                          configured addresses + sync state
  lsl hl fetch   [--data-dir DATA] [--address 0x...] [--now ISO]  sync every address (incremental)
  lsl hl tokens  [--unit] [--match NAME]                    public spotMeta token index/tokenId lookup
  lsl candles fetch  [--data-dir DATA] [--stocks yahoo|rh]
                                        daily candles for the SMA trend tags: Hyperliquid
                                        candleSnapshot for crypto/HL markets (throttled, resumable);
                                        stocks: yahoo (default) = keyless box-side fetch, 1 call per
                                        symbol; rh = the `lsl rh plan --candles on` connector loop
  lsl candles status [--data-dir DATA]   which candle series are complete / missing
  lsl sources    [--data-dir DATA]                          which sources have data (rh, hl addresses)
  lsl compute    [--data-dir DATA] [--scope SCOPE] [--out DIR] [--lang zh|en]
                 SCOPE: all (default) | rh | rh:<account> | hl | hl:<address>; outputs go to
                 DATA/out/<scope-slug>/ unless --out is given
  lsl page       [--out DIR | --data-dir DATA --scope SCOPE] [--lang en|zh] [--compact] [--copy FILE]
                                                     rebuild page.json
  lsl html       [--out DIR | --data-dir DATA --scope SCOPE] [--lang en|zh] [--copy FILE]
                                                     self-contained report.html (cards inlined, PNG paths listed)
  lsl card       [--out DIR | --data-dir DATA --scope SCOPE] [--period all|90d|30d|7d]
                 [--lang en|zh] [--png] [--per-account] [--copy FILE]
                                                     three 1080x1920 story cards (SVG, +2x PNG),
                                                     no dollar amounts;
                                                     --per-account adds cards/<account-slug>-*.svg per
                                                     account; --copy uses the bot's checked copy.json
                                                     (--percent-only: accepted, no-op)
  lsl purge      [--data-dir DATA] [--source rh|hl|all] [--yes]  wipe stored account data
                                                     (prefs.json at the data-dir root is kept)
  lsl prefs      [show|set --lang en|zh|system] [--data-dir DATA]   report language
  lsl data-dir   [--data-dir DATA]                                  resolved data dir
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


LEGACY_DATA_DIR = "~/trading-journey-data"
LOCAL_DATA_DIR = "~/.trading-journey"


def data_dir_info(environ=None, isdir=None):
    """Where account data goes when `--data-dir` is omitted.

    `$LSL_DATA_DIR` wins. Else `~/trading-journey-data` if it already exists
    (the directory Grok Bot has always used). Else `~/.trading-journey`.
    """
    env = os.environ if environ is None else environ
    exists = os.path.isdir if isdir is None else isdir
    raw = env.get("LSL_DATA_DIR")
    if raw:
        return {"data_dir": os.path.expanduser(raw), "source": "env"}
    legacy = os.path.expanduser(LEGACY_DATA_DIR)
    if exists(legacy):
        return {"data_dir": legacy, "source": "legacy"}
    return {"data_dir": os.path.expanduser(LOCAL_DATA_DIR), "source": "default"}


def default_data_dir(environ=None, isdir=None):
    return data_dir_info(environ, isdir)["data_dir"]


def _data(sp):
    sp.add_argument("--data-dir", "--run", dest="run", default=None,
                    help="persistent data dir ($LSL_DATA_DIR, else ~/trading-journey-data "
                         "if it exists, else ~/.trading-journey)")


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
    rh.add_argument("--namespace", help="Robinhood MCP namespace (the name your host gave the server)")
    rh.add_argument("--now")
    rh.add_argument("--option-universe", choices=["on", "off"],
                    help="optional option-contract cross-check (default off)")
    rh.add_argument("--candles", choices=["on", "off"], default="off",
                    help="also plan stock daily-candle calls (Robinhood get_equity_historicals) once the "
                         "account sync is done; ~1 call per 50 trading days per symbol (default off: "
                         "`lsl candles fetch --stocks yahoo` covers stocks keylessly)")
    ca = sub.add_parser("candles")
    ca.add_argument("action", choices=["fetch", "status"])
    ca.add_argument("--stocks", choices=["rh", "yahoo"], default="yahoo",
                    help="stock candles: yahoo (default, keyless, 1 call/symbol) or rh (the "
                         "`lsl rh plan --candles on` connector loop)")
    _data(ca)
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
    pg.add_argument("--copy", help="bot-written share-card copy.json (persona/verdict/rules/question); see lsl/card.py")
    ht = sub.add_parser("html")
    _data(ht)
    ht.add_argument("--out", help="dir holding the compute outputs (default DATA/out/<scope-slug>)")
    ht.add_argument("--scope", default="all")
    ht.add_argument("--lang", choices=["zh", "en"],
                    help="report language (default: the lang already in page.json, else en)")
    ht.add_argument("--copy", help="card-copy.json to quote in the report (default: OUT/card-copy.json if it exists)")
    cd = sub.add_parser("card")
    _data(cd)
    cd.add_argument("--out", help="dir holding the compute outputs (default DATA/out/<scope-slug>)")
    cd.add_argument("--scope", default="all")
    cd.add_argument("--lang", default="en", choices=["zh", "en"])
    cd.add_argument("--period", default="all", choices=["all", "90d", "30d", "7d"])
    cd.add_argument("--percent-only", action="store_true", help="no-op (the card never shows dollar amounts)")
    cd.add_argument("--copy", help="bot-written copy.json (persona/verdict/rules/question); validated, see lsl/card.py")
    cd.add_argument("--png", action="store_true", help="also rasterize to 2x PNGs if a converter is installed")
    cd.add_argument("--per-account", action="store_true",
                    help="also one card set per account (several accounts) into <out>/cards/<account-slug>-*.svg")
    so = sub.add_parser("sources")
    _data(so)
    pu = sub.add_parser("purge")
    _data(pu)
    pu.add_argument("--source", choices=["rh", "hl", "all"], default="all")
    pu.add_argument("--yes", action="store_true")
    pr = sub.add_parser("prefs")
    pr.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    pr.add_argument("--lang", choices=["en", "zh", "system"],
                    help="set: en, zh, or system (resolved from LANG / LC_ALL on each read)")
    _data(pr)
    dd = sub.add_parser("data-dir")
    _data(dd)
    sub.add_parser("selftest")
    a = p.parse_args(argv)
    explicit_run = getattr(a, "run", None)
    if hasattr(a, "run") and a.run is None:
        a.run = default_data_dir()

    if a.cmd == "rh":
        from . import rhsync
        run = rhsync.Run(a.run)
        if a.action == "plan":
            ou = None if a.option_universe is None else a.option_universe == "on"
            res = rhsync.plan(run, a.max, now=_now(a), option_universe=ou, namespace=a.namespace,
                              candles=a.candles == "on")
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
                res = rhsync.plan(run, a.max, candles=a.candles == "on")
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "candles":
        from . import candles
        if a.action == "fetch":
            res = candles.fetch_hl(a.run)
            if "error" not in res and a.stocks == "yahoo":
                res = {"hl": res, "stocks_yahoo": candles.fetch_stocks_yahoo(a.run)}
            elif "error" not in res:
                st = candles.status(a.run)
                res["stocks"] = dict(st["rh"], next="run the `lsl rh plan --candles on` / `lsl rh ingest` loop until done"
                                     if st["rh"]["windows_left"] else None)
        else:
            res = candles.status(a.run)
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
        res = page.run(_scope_out(a), a.lang, a.compact, a.copy)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "html":
        from . import htmlreport
        res = htmlreport.run(_scope_out(a), a.lang, a.copy)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "prefs":
        from . import prefs
        if a.action == "set":
            res = prefs.set_choice(a.run, a.lang) if a.lang else {
                "error": "pass --lang en|zh|system", "path": prefs.path(a.run)}
        else:
            res = prefs.show(a.run)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "data-dir":
        if explicit_run:
            info = {"data_dir": os.path.abspath(os.path.expanduser(explicit_run)), "source": "flag"}
        else:
            info = data_dir_info()
        import lsl
        info["version"] = lsl.VERSION
        sys.stdout.write(dumps(info))
        return 0
    if a.cmd == "card":
        from . import card
        res = card.run(_scope_out(a), period=a.period, percent_only=a.percent_only, png=a.png, lang=a.lang,
                       per_account_cards=a.per_account, copy_path=a.copy)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "sources":
        from . import compute
        sys.stdout.write(dumps(compute.sources(a.run)))
        return 0
    if a.cmd == "selftest":
        import unittest
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suite = unittest.defaultTestLoader.discover(os.path.join(here, "tests"), top_level_dir=here)
        r = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if r.wasSuccessful() else 1
    return 2
