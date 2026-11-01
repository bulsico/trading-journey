"""v0.2.13 story cards: three 1080x1920 cards per scope/period (stats, fun facts, roast), every element
inside the frame and above the footer, no overlapping text, deterministic bytes.
All data and copy are synthetic."""
import json
import os
import re
import shutil
import tempfile

from lsl import card
from tests.test_sources import Base, add_hl, add_rh
from tests.test_hl import ADDR, OTHER

ZH_TEXT = ("这是一段虚构的测试文案，用来把每个文案槽位都填到校验器允许的最长长度。它不描述任何真实账户，只负责检查换行、"
           "标点避头尾和卡片边界。「示例标签」这样的引号也要正确换行，逗号、句号和顿号不能出现在行首；"
           "如果文字太长，卡片应该缩小一级字号或省略细节，而不是跑出画面。再补一句凑足长度：测试测试，一二三四五六七八九十。")
EN_TEXT = ("You're no sniper. You walk in with a drum mag and spray, every ticker gets a turn, and somehow the "
           "keyboard survives. Entries are fine; exits are a coin toss. Cap the losers, let the winners breathe, "
           "and stop treating the liquidation price as your stop. Discipline beats volume every single time, "
           "and the trend is your friend until it bends.")


def longest(text, lang, slot, limit, sm):
    """The longest prefix of `text` (<= limit chars) that the copy validator accepts for `slot`."""
    allowed = card.allowed_numbers(sm)
    w, size = card._SLOT[slot]
    n = min(limit, len(text))
    if lang != "zh" and n < len(text):
        n = text[:n + 1].rfind(" ")
    while n > 0:
        s = text[:n].rstrip(" ,，")
        if not card.check_text(s, allowed, lang, slot, w, size):
            return s
        n = n - 1 if lang == "zh" else max(0, text[:n].rstrip().rfind(" "))
    raise AssertionError("no valid prefix")


def max_copy(sm, lang):
    lim = card.COPY_LIMITS[lang]
    t = ZH_TEXT if lang == "zh" else EN_TEXT
    rules = [("先缩小仓位。" if lang == "zh" else "Size down first. ") + t, ("每单都要止损。" if lang == "zh" else
             "Every perp gets a stop. ") + t, ("写交易日志。" if lang == "zh" else "Journal every trade. ") + t]
    c = {"persona": longest(t, lang, "persona", lim["persona"], sm),
         "verdict": longest(t, lang, "verdict", lim["verdict"], sm),
         "rules": [longest(r, lang, "rule", lim["rule"], sm) for r in rules],
         "question": longest(t, lang, "question", lim["question"], sm)}
    clean, probs = card.check_copy(c, sm, lang)
    assert not probs, probs
    return clean


class StoryCards(Base):
    def setUp(self):
        super().setUp()
        add_rh(self.d)
        add_hl(self.d, (ADDR, OTHER))

    def load(self, lang="en"):
        res, sm, pg = self.compute(lang=lang)
        with open(os.path.join(res["out"], "series.json")) as f:
            se = json.load(f)
        return res, sm, se, pg

    def assert_set(self, cs, ctx):
        ids = [i for i, _s in cs]
        self.assertIn(ids, [list(card.CARD_IDS), [card.CARD_IDS[0], card.CARD_IDS[2]]], ctx)
        for n, (cid, s) in enumerate(cs):
            self.assertEqual(card.size_of(s), (1080, 1920), (ctx, cid))
            self.assertEqual(card.layout_problems(s), [], (ctx, cid))
            self.assertIn(">%d/%d</text>" % (n + 1, len(cs)), s, (ctx, cid))
            self.assertNotIn("$", s, (ctx, cid))

    def test_every_card_in_bounds(self):
        """Every scope, period and language, with template copy and with copy.json at its limits."""
        for lang in ("en", "zh"):
            res, sm, se, pg = self.load(lang)
            big = max_copy(sm, lang)
            self.assertGreater(len(big["persona"]), card.COPY_LIMITS[lang]["persona"] * 0.6)
            for key in ["all"] + sorted(sm["scopes"]):
                for per in card.available(sm, key):
                    d = card.data(sm, se, per, key)
                    for cp in (None, big):
                        cs = card.svgs(d, lang, copy=cp)
                        self.assert_set(cs, (lang, key, per, bool(cp)))
                        self.assertEqual(cs, card.svgs(card.data(sm, se, per, key), lang, copy=cp))  # deterministic
                    if cp:
                        self.assertIn(card.esc(big["question"][:12]), cs[-1][1])

    def test_long_chinese_copy_wraps_inside(self):
        res, sm, se, pg = self.load("zh")
        big = max_copy(sm, "zh")
        cs = card.svgs(card.data(sm, se), "zh", copy=big)
        roast = cs[-1][1]
        # the persona is wrapped over several lines, none of which crosses the margin (layout_problems)
        lines = re.findall(r'<text x="64"[^>]*>([^<]+)</text>', roast)
        self.assertTrue(any(ZH_TEXT[:6] in ln for ln in lines))
        self.assertEqual(card.layout_problems(roast), [])
        # closing punctuation never starts a line
        for ln in lines:
            self.assertNotIn(ln[:1], "，。、；：！？）」』》", ln)

    def test_no_facts_two_cards(self):
        res, sm, se, pg = self.load()
        d = card.data(sm, se)
        d["fun"] = {}
        cs = card.svgs(d, "en")
        self.assertEqual([i for i, _s in cs], [card.CARD_IDS[0], card.CARD_IDS[2]])
        self.assert_set(cs, "no facts")
        self.assertNotIn(card.esc(card.L("en")["h_fun"][1]), "".join(s for _i, s in cs))

    def test_facts_drop_instead_of_shrinking(self):
        res, sm, se, pg = self.load()
        d = card.data(sm, se)
        cells = card.fun_cells(d, "en")
        kept = card.fact_keys(d, "en")
        self.assertEqual([r[0] for r in kept], [c for c in cells if c in [r[0] for r in kept]])  # order kept
        facts = card.svgs(d, "en")[1][1]
        for r in kept:
            self.assertIn(card.esc(r[0][2]), facts)
            self.assertGreaterEqual(r[3], 32)  # value size floor
        # a scope with far more rows than fit drops facts (never below the size floor), still in bounds
        many = cells * 3
        orig = card.fun_cells
        try:
            card.fun_cells = lambda d_, lang_: many
            lay = card.fact_keys(d, "en")
            self.assertLess(len(lay), len(many))
            self.assertEqual(card.layout_problems(card.svgs(d, "en")[1][1]), [])
        finally:
            card.fun_cells = orig

    def test_run_writes_set_and_removes_zips(self):
        res, sm, se, pg = self.load("zh")
        out = res["out"]
        for n in ("share-card.svg", "share-card.png", "share-card.zip", "share-cards.zip", "share-card-1-stats.zip"):
            with open(os.path.join(out, n), "w") as f:   # stale files from older versions are removed
                f.write("old")
        r = card.run(out, lang="zh", png=bool(card.rasterizer()))
        self.assertEqual(r["status"], "done", r)
        self.assertEqual([c["id"] for c in r["cards_set"]], list(card.CARD_IDS))
        self.assertEqual(r["size"], [1080, 1920])
        for ext in (".svg", ".png", ".zip"):
            self.assertFalse(os.path.exists(os.path.join(out, "share-card" + ext)), ext)
        for c in r["cards_set"]:
            self.assertTrue(os.path.exists(c["svg"]))
        self.assertEqual([f for f in os.listdir(out) if f.endswith(".zip")], [])
        if not card.rasterizer():
            self.skipTest("no rasterizer: PNG path not exercised")
        for c in r["cards_set"]:
            self.assertNotIn("zip", c)
            self.assertTrue(os.path.exists(c["png"]))
        self.assertNotIn("zip", r)

    def test_page_block_sets(self):
        res, sm, se, pg = self.load()
        C = pg["card"]
        self.assertEqual((C["width"], C["height"]), (1080, 1920))
        for per, ss in C["variants"].items():
            self.assertEqual(len(ss), len(C["card_ids"][per]))
            for s in ss:
                self.assertEqual(card.layout_problems(s), [])
        for a in C["accounts"]:
            for per, ss in a["variants"].items():
                self.assertIn(len(ss), (2, 3))
