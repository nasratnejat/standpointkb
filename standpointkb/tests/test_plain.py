import unittest

from standpointkb import plain
from standpointkb.seed import build


def by_topic(out):
    return {i["topic"]: i for i in out["items"]}


class CitizenViewTests(unittest.TestCase):
    def test_developer_in_dresden_sees_unclear_then_clear(self):
        rev = build()
        prof = {"work": "software", "revenue": 18000, "profit": 15000, "moved": True}
        items = by_topic(plain.checklist(rev.kb, prof, "dresden"))
        self.assertEqual(items["must_register_gewerbe"]["status"], "unclear")
        self.assertIn("Dresden says yes", items["must_register_gewerbe"]["detail"])
        self.assertEqual(items["must_register_finanzamt"]["status"], "yes")
        self.assertEqual(items["must_charge_vat"]["status"], "no")
        # expert applies the recommended repair → citizen gets a definite answer
        rid = next(r.id for r in rev.kb.rules.values() if r.standpoint == "dresden")
        rev.apply({"type": "weaken", "rule_id": rid, "condition": "not freier_beruf(X)"})
        items = by_topic(plain.checklist(rev.kb, prof, "dresden"))
        self.assertEqual(items["must_register_gewerbe"]["status"], "no")

    def test_cafe_in_leipzig(self):
        rev = build()
        prof = {"work": "cafe", "revenue": 60000, "profit": 30000,
                "serves_alcohol": True, "outdoor_seating": True}
        items = by_topic(plain.checklist(rev.kb, prof, "leipzig"))
        for t in ("must_register_gewerbe", "must_register_finanzamt", "must_charge_vat",
                  "must_pay_gewerbesteuer", "needs_gaststaettenerlaubnis", "needs_sondernutzung"):
            self.assertEqual(items[t]["status"], "yes", t)
        self.assertEqual(by_topic(plain.checklist(rev.kb, prof, "dresden"))["needs_sondernutzung"]["status"], "na")

    def test_detached_practice_shows_heads_up(self):
        rev = build()
        rid = next(r.id for r in rev.kb.rules.values() if r.standpoint == "dresden")
        rev.apply({"type": "detach", "rule_id": rid, "new_standpoint": "dresden_practice",
                   "label": "Dresden office (local practice)"})
        out = by_topic(plain.checklist(rev.kb, {"work": "software"}, "dresden"))
        g = out["must_register_gewerbe"]
        self.assertEqual(g["status"], "no")
        self.assertTrue(any("local practice" in n for n in g["notes"]))

    def test_steps_are_plain_language(self):
        rev = build()
        out = by_topic(plain.checklist(rev.kb, {"work": "software", "revenue": 18000}, "leipzig"))
        texts = [s["text"] for s in out["must_charge_vat"]["why"]]
        self.assertIn("18.000 € is at most the limit of 25.000 €.", texts)
        self.assertTrue(all("(X)" not in t and "∧" not in t for t in texts))


if __name__ == "__main__":
    unittest.main()
