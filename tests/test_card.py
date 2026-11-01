"""Share cards (v0.2.13: three 1080x1920 story cards), casual, no dollar amounts; bot copy.json is
checked against the script output.  All data is generated."""
import json
import os

from lsl import card, page, persona
from tests.test_sources import Base, add_hl, add_rh
from tests.test_hl import ADDR, OTHER


class Card(Base):
    def setUp(self):
        super().setUp()
        add_rh(self.d)
        add_hl(self.d, (ADDR, OTHER))

    def load(self, lang="en"):
        res, sm, pg = self.compute(lang=lang)
        with open(os.path.join(res["out"], "series.json")) as f:
            se = json.load(f)
        return res, sm, se, pg

    def test_sections_and_no_dollars(self):
        for lang in ("en", "zh"):
            res, sm, se, pg = self.load(lang)
            T, Lb = card.L(lang), persona.labels(lang)
            s = card.svg(card.data(sm, se), lang)
            self.assertNotIn("$", s)
            for h in ("h_persona", "h_grade", "h_soul"):
                self.assertIn(card.esc(T[h][1]), s, h)
            self.assertIn(card.esc(T["footer"]), s)
            for g in "SABCDF":   # the ladder: full labels, or letter chips at the compact tiers
                self.assertTrue(card.esc(Lb["grades"][g]) in s or (">%s</text>" % g) in s, g)
            self.assertIn(card.esc(Lb["grades"][sm["persona"]["grade"]]), s)
            self.assertIn(card.esc(Lb["archetypes"][sm["persona"]["archetype"]]), s)
            cs = card.svgs(card.data(sm, se), lang)
            self.assertEqual([i for i, _s in cs], list(card.CARD_IDS))
            for n, (cid, one) in enumerate(cs):
                self.assertEqual(card.size_of(one), (1080, 1920), cid)
                self.assertEqual(card.layout_problems(one), [], (lang, cid))
                self.assertIn(">%d/3</text>" % (n + 1), one)
            # page variants are keyed by period only
            self.assertEqual(sorted(pg["card"]["variants"]), sorted(card.available(sm)))
            self.assertEqual(pg["card"]["copy_source"], "template")

    def test_template_copy_is_deterministic_and_valid(self):
        res, sm, se, pg = self.load()
        for lang in ("en", "zh"):
            for key in ["all"] + [r["key"] for r in sm["accounts"]["rows"]]:
                d = card.data(sm, se, "all", key)
                c = card.template_copy(d, lang)
                self.assertEqual(c, card.template_copy(d, lang))
                self.assertEqual(len(c["rules"]), 3)
                clean, probs = card.check_copy({k: c[k] for k in ("persona", "verdict", "rules", "question")}, sm, lang)
                self.assertEqual(probs, [], (lang, key))

    def test_copy_validation(self):
        res, sm, se, pg = self.load()
        wr = "%.1f" % (sm["profile"]["all"]["win_rate"] * 100)
        good = {"persona": "You trade a lot. Win rate %s%%, and the 50-day SMA is your friend." % wr,
                "verdict": "Solid entries, sloppy exits.",
                "rules": ["Size down. Smaller bets hurt less.", "Journal it. Write one line per trade.",
                          "Use stops. Every perp gets one."],
                "question": "Am I following the trend or fighting it?"}
        clean, probs = card.check_copy(good, sm, "en")
        self.assertEqual(probs, [])
        s = card.svg(card.data(sm, se), "en", copy=clean)
        self.assertIn("sloppy exits", s)
        self.assertNotIn("$", s)
        bad = [dict(good, verdict="You lost $1,234 this year."),          # dollar sign
               dict(good, verdict="You made 98765 trades."),             # number not in the output
               dict(good, persona="word " * 200),                        # too long
               dict(good, rules=good["rules"][:2]),                      # not exactly 3 rules
               dict(good, mood="spicy"),                                 # unknown key
               dict(good, question="")]
        for b in bad:
            clean, probs = card.check_copy(b, sm, "en")
            self.assertIsNone(clean, b)
            self.assertTrue(probs, b)
        # a line that fits the length limit but wraps past the slot is rejected too
        wide = {"verdict": " ".join(["WWWWW"] * 28)}
        self.assertLessEqual(len(wide["verdict"]), card.COPY_LIMITS["en"]["verdict"])
        self.assertTrue(any("wraps" in p for p in card.check_copy(wide, sm, "en")[1]))

    def test_cli_copy_paths(self):
        res, sm, se, pg = self.load("zh")
        out = res["out"]
        p = os.path.join(self.d, "copy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"verdict": "进场不错，出场太慢。"}, f, ensure_ascii=False)
        r = card.run(out, lang="zh", copy_path=p)
        self.assertEqual((r["status"], r["copy"]), ("done", "copy.json"))
        with open(r["cards_set"][-1]["svg"], encoding="utf-8") as f:
            self.assertIn("出场太慢", f.read())
        r2 = page.run(out, "zh", True, p)
        self.assertEqual(r2["card_copy"], "copy.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"verdict": "亏了 $500"}, f, ensure_ascii=False)
        self.assertIn("error", card.run(out, lang="zh", copy_path=p))
        self.assertIn("error", page.run(out, "zh", True, p))
        r3 = card.run(out, lang="zh", percent_only=True)
        self.assertEqual(r3["status"], "done")
