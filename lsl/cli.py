"""lsl command line. All output is JSON on stdout.

DATA is a stable, persistent data dir (default ~/trading-journey-data, or $LSL_DATA_DIR):
DATA/state (sync state + parsed records), DATA/raw (saved MCP results), DATA/out.
It is kept between runs so later runs sync incrementally; `--run` is an alias.

  lsl rh plan    [--data-dir DATA] [--max N] [--now ISO]   next exact MCP calls
  lsl rh ingest  [--data-dir DATA]                          parse saved results, advance
  lsl rh status  [--data-dir DATA]                          progress + completeness
  lsl rh refresh [--data-dir DATA] [--now ISO]              start an incremental round
  lsl hl fetch   [--data-dir DATA] --address 0x... [--now ISO]  full first time, then incremental
  lsl hl tokens  [--unit] [--match NAME]                    public spotMeta token index/tokenId lookup
  lsl compute    [--data-dir DATA] [--out DIR] [--lang zh|en]
  lsl page       --out DIR [--lang en|zh]                   page.json from existing outputs
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
    rh.add_argument("--now")
    rh.add_argument("--option-universe", choices=["on", "off"],
                    help="optional option-contract cross-check (default off)")
    hl = sub.add_parser("hl")
    hl.add_argument("action", choices=["fetch", "tokens"])
    _data(hl)
    hl.add_argument("--address")
    hl.add_argument("--unit", action="store_true", help="tokens: Unit-deployed tokens + HYPE")
    hl.add_argument("--match", action="append", default=[], help="tokens: exact token name")
    hl.add_argument("--now")
    c = sub.add_parser("compute")
    _data(c)
    c.add_argument("--out")
    c.add_argument("--lang", default="zh", choices=["zh", "en"])
    pg = sub.add_parser("page")
    pg.add_argument("--out", required=True)
    pg.add_argument("--lang", default="en", choices=["zh", "en"])
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
            res = rhsync.plan(run, a.max, now=_now(a), option_universe=ou)
        elif run.st is None:
            res = {"error": "no state; run `lsl rh plan` first"}
        elif a.action == "ingest":
            res = rhsync.ingest(run)
        elif a.action == "status":
            res = rhsync.status(run)
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
        elif not a.address:
            res = {"error": "--address is required for hl fetch"}
        else:
            res = hlmod.fetch(a.run, a.address, _now(a))
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "compute":
        from . import compute
        res = compute.run(a.run, a.out, a.lang)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "page":
        from . import page
        res = page.run(a.out, a.lang)
        sys.stdout.write(dumps(res))
        return 1 if "error" in res else 0
    if a.cmd == "selftest":
        import os
        import unittest
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suite = unittest.defaultTestLoader.discover(os.path.join(here, "tests"), top_level_dir=here)
        r = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if r.wasSuccessful() else 1
    return 2
