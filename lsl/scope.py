"""Report scope: which slice of the synced data a report covers.

  all              everything (default)
  rh               every Robinhood account
  rh:<account>     one Robinhood account: its tag (a0, a1, ...), the last 4 digits of its
                   number, or its kind (individual, ira_roth, ...) when that kind is unique
  hl               every Hyperliquid address
  hl:<address>     one Hyperliquid address: full address, label, fingerprint or short form

Raw data is shared and fetched once; a scope only filters what compute sees.  Outputs go
to DATA/out/<slug>/ so several scopes coexist.  Labels never contain a full account number
or a full address: Robinhood accounts show as "Robinhood <kind> ...1234", addresses as 0xabcd...1234.
"""
import re

from . import hl as hlmod

KINDS = {"individual": ("Individual", "个人账户"), "ira_traditional": ("Traditional IRA", "传统IRA"),
         "ira_roth": ("Roth IRA", "Roth IRA"), "joint": ("Joint", "联名账户"), "unknown": ("Account", "账户")}


def _kind(k, lang):
    en, zh = KINDS.get(k or "unknown", (str(k or "Account").replace("_", " ").title(), None))
    return zh if lang == "zh" and zh else en


def rh_label(a, lang="en"):
    return "Robinhood %s%s" % (_kind(a.get("kind"), lang), (" " + a["mask"]) if a.get("mask") else "")


def hl_label(h, ident=None):
    if h.get("short"):
        return h["short"]
    a = hlmod.normalize(ident or "")
    if a and hlmod.ADDRESS_RE.match(a):
        return hlmod.short(a)
    return h.get("label") or "Hyperliquid"


def slug(scope_id):
    return re.sub(r"[^a-z0-9]+", "-", scope_id.lower()).strip("-") or "all"


def _match_rh(accts, ident):
    ident = ident.strip().lower()
    for a in accts:
        if ident == a["tag"]:
            return a
    digits = re.sub(r"\D", "", ident)
    if len(digits) == 4:
        hits = [a for a in accts if (a.get("mask") or "").endswith(digits)]
        if len(hits) == 1:
            return hits[0]
    hits = [a for a in accts if (a.get("kind") or "").lower() == ident]
    return hits[0] if len(hits) == 1 else None


def _match_hl(hs, ident):
    ident = ident.strip()
    a = hlmod.normalize(ident)
    fp = hlmod.fingerprint(a) if a and hlmod.ADDRESS_RE.match(a) else None
    for h in hs:
        if (fp and h["fingerprint"] == fp) or ident in (h["fingerprint"], h.get("label"), h.get("short"), h.get("tag")):
            return h
    return None


def available(rh, hs, lang="en"):
    """Every scope that has data: [{"id", "slug", "label"}] (ids are stable, never full numbers)."""
    out = [{"id": "all", "label": "All accounts" if lang != "zh" else "全部账户"}]
    if rh:
        out.append({"id": "rh", "label": "Robinhood"})
        if len(rh["accounts"]) > 1:
            out += [{"id": "rh:" + a["tag"], "label": rh_label(a, lang)} for a in rh["accounts"]]
    if hs:
        out.append({"id": "hl", "label": "Hyperliquid"})
        if len(hs) > 1:
            out += [{"id": "hl:" + (h.get("short") or h["fingerprint"]), "label": hl_label(h)} for h in hs]
    if rh and not hs:
        out = [s for s in out if s["id"] != "rh"]
    if hs and not rh:
        out = [s for s in out if s["id"] != "hl"]
    for s in out:
        s["slug"] = slug(s["id"])
    return out


def resolve(spec, rh, hs, lang="en"):
    """-> (rh', hs', meta) filtered to the scope, or an {"error"} dict."""
    spec = (spec or "all").strip()
    kind, _, ident = spec.partition(":")
    kind = kind.lower()
    avail = [s["id"] for s in available(rh, hs, lang)]
    if kind == "all" and not ident:
        return rh, hs, _meta("all", "all", rh, hs, lang)
    if kind == "rh":
        if not rh:
            return {"error": "scope %r: Robinhood is not synced" % spec, "scopes": avail}
        if not ident:
            return rh, [], _meta("rh", "rh", rh, [], lang)
        a = _match_rh(rh["accounts"], ident)
        if not a:
            return {"error": "scope %r: no Robinhood account matches (use a tag, last 4 digits or a unique kind)"
                             % spec, "scopes": avail}
        r = dict(rh, accounts=[a])
        return r, [], _meta("rh:" + a["tag"], "rh_account", r, [], lang, label=lambda l: rh_label(a, l))
    if kind == "hl":
        if not hs:
            return {"error": "scope %r: no Hyperliquid address is synced" % spec, "scopes": avail}
        if not ident:
            return None, hs, _meta("hl", "hl", None, hs, lang)
        h = _match_hl(hs, ident)
        if not h:
            return {"error": "scope %r: no synced Hyperliquid address matches" % spec, "scopes": avail}
        lab = hl_label(h, ident)
        sid = "hl:" + (h.get("short") or (lab if lab.startswith("0x") else h["fingerprint"]))
        return None, [h], _meta(sid, "hl_address", None, [h], lang, label=lambda l: lab)
    return {"error": "unknown scope %r (all | rh | rh:<account> | hl | hl:<address>)" % spec, "scopes": avail}


def _meta(sid, kind, rh, hs, lang, label=None):
    def lab(l):
        if label is not None:
            return label(l)
        if kind == "all":
            return "All accounts" if l != "zh" else "全部账户"
        if kind == "rh":
            return "Robinhood"
        return hl_label(hs[0]) if len(hs) == 1 and hs[0].get("short") else "Hyperliquid"
    labels = {l: lab(l) for l in ("en", "zh")}
    return {"id": sid, "slug": slug(sid), "kind": kind, "label": labels.get(lang, labels["en"]), "labels": labels,
            "sources": {"rh": bool(rh), "hl": bool(hs)},
            "rh_accounts": [rh_label(a, lang) for a in (rh or {}).get("accounts", [])],
            "rh_account_labels": {a["tag"]: rh_label(a, lang) for a in (rh or {}).get("accounts", []) if a.get("tag")},
            "hl_addresses": [hl_label(h) for h in hs or []]}
