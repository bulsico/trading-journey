"""Page template guards.  The Page runs in a sandboxed frame whose kit has no download, blob or
clipboard API, so the template must not ship buttons or code that try to save files; the card is
shown as an image and the PNG is attached in chat (bot/SKILL.md step 5)."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.t = read("bot", "page-template.tsx")
        self.labels = {lang: json.loads(read("lsl", "labels", lang + ".json")) for lang in ("en", "zh")}

    def test_no_download_machinery(self):
        code = "\n".join(l for l in self.t.split("\n") if not l.lstrip().startswith("//"))
        for bad in ("<Button", "a.download", ".download =", "toDataURL", "createElement", "Blob", "createObjectURL",
                    "clipboard", "download_png", "download_svg", "download_blocked", "savePng"):
            self.assertFalse(bad in code, bad)

    def test_card_is_an_image_with_chat_note(self):
        self.assertEqual(self.t.count("__PAGE_JSON__"), 1)
        self.assertIn('<Image src={svgUrl(svg)}', self.t)
        self.assertIn("{U.card_save}", self.t)
        self.assertIn('"data:image/svg+xml;charset=utf-8,"', self.t)

    def test_labels_both_languages(self):
        en, zh = self.labels["en"], self.labels["zh"]
        for sec in ("ui", "accounts"):
            self.assertEqual(sorted(en[sec]), sorted(zh[sec]), sec)
            for k in ("download_png", "download_svg", "download_blocked"):
                self.assertNotIn(k, en[sec])
        self.assertIn("chat", en["ui"]["card_save"])
        self.assertNotIn("zip", en["ui"]["card_save"] + zh["ui"]["card_save"])
        self.assertIn("聊天", zh["ui"]["card_save"])
        self.assertIn("chat", en["accounts"]["card_in_picker"])
        self.assertIn("聊天", zh["accounts"]["card_in_picker"])

    def test_document_language(self):
        # the kit's <Page> has no lang prop; the template sets <html lang> from D.html_lang
        self.assertIn("document.documentElement.lang = D.html_lang", self.t)
        self.assertIn("useEffect", self.t.split("\n")[0])

    def test_report_templates_use_real_fields(self):
        # placeholders name real snake_case fields (summary.json / page.json), not old camelCase ones
        for lang in ("en", "zh"):
            t = read("bot", "report-template.%s.md" % lang)
            camel = [w for b in re.findall(r"\{([^{}]*)\}", t) for w in re.findall(r"\b[a-z]+[A-Z]\w*", b) if w != "uPnL"]
            self.assertEqual(camel, [], lang)
            self.assertIn("persona.ratings[]", t)

    def test_template_keys_exist(self):
        # every U.x / AL.x / L.x the template reads exists in both languages
        refs = {"ui": set(re.findall(r"\bU\.(\w+)", self.t)),
                "accounts": set(re.findall(r"\b(?:AL|L)\.(\w+)", self.t))}
        for lang, lab in self.labels.items():
            for sec, keys in refs.items():
                missing = sorted(k for k in keys if k not in lab[sec])
                self.assertEqual(missing, [], "%s %s" % (lang, sec))


if __name__ == "__main__":
    unittest.main()
