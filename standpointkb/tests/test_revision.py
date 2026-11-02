import unittest

from standpointkb.engine import Reasoner, parse_literal
from standpointkb.seed import build


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.rev = build()
        self.dresden_rule = next(r.id for r in self.rev.kb.rules.values() if r.standpoint == "dresden")

    def test_recommended_repair_is_general_exception(self):
        s = self.rev.suggest("nasrat", "dresden", "must_register_gewerbe(nasrat)")
        top = s["candidates"][0]
        self.assertTrue(top["recommended"])
        self.assertEqual(top["op"], {"type": "weaken", "rule_id": self.dresden_rule,
                                     "condition": "not freier_beruf(X)"})
        self.assertEqual(top["also_fixes"], [["jonas", "dresden", "must_register_gewerbe(jonas)"]])

    def test_all_admissible_candidates_actually_resolve(self):
        s = self.rev.suggest("nasrat", "dresden", "must_register_gewerbe(nasrat)")
        for c in s["candidates"]:
            if c["admissible"]:
                self.assertTrue(c["resolves"])
                self.assertEqual(c["new_conflicts"], [])

    def test_weaken_keeps_unclassified_case(self):
        self.rev.apply({"type": "weaken", "rule_id": self.dresden_rule, "condition": "not freier_beruf(X)"})
        self.assertEqual(self.rev.overview()["n_conflicts"], 0)
        r = Reasoner(self.rev.kb)
        mia = r.query(self.rev.cases["mia"], parse_literal("must_register_gewerbe(mia)"))
        self.assertEqual(mia["summary"]["dresden"], "YES")   # local rule still applies to mia
        self.assertEqual(mia["summary"]["bund"], "UNKNOWN")

    def test_detach_makes_disagreement_explicit(self):
        self.rev.apply({"type": "detach", "rule_id": self.dresden_rule, "new_standpoint": "dresden_practice"})
        r = Reasoner(self.rev.kb)
        q = r.query(self.rev.cases["nasrat"], parse_literal("must_register_gewerbe(nasrat)"))
        self.assertEqual(q["summary"]["dresden_practice"], "YES")
        self.assertEqual(q["summary"]["dresden"], "NO")
        self.assertTrue(q["disagreement"])
        self.assertEqual(self.rev.overview()["n_conflicts"], 0)

    def test_narrow_requires_sharper_standpoint(self):
        bund_rule = next(r.id for r in self.rev.kb.rules.values() if r.standpoint == "bund")
        with self.assertRaises(ValueError):
            self.rev.apply({"type": "narrow", "rule_id": self.dresden_rule, "to": "bund"})
        self.rev.apply({"type": "narrow", "rule_id": bund_rule, "to": "sachsen"})
        self.assertEqual(self.rev.kb.rules[bund_rule].standpoint, "sachsen")

    def test_undo_restores_state(self):
        before = self.rev.kb.to_dict()
        self.rev.apply({"type": "remove", "rule_id": self.dresden_rule})
        self.assertNotEqual(before, self.rev.kb.to_dict())
        self.rev.undo()
        self.assertEqual(before, self.rev.kb.to_dict())

    def test_no_vacuous_weakening(self):
        # direct contradiction with identical conditions: weakening can only kill the rule
        self.rev.apply({"type": "add_rule", "standpoint": "leipzig",
                        "text": "cafe(X) ∧ serves_alcohol(X) → ¬needs_gaststaettenerlaubnis(X)"})
        s = self.rev.suggest("cafe_elbe", "leipzig", "needs_gaststaettenerlaubnis(cafe_elbe)")
        types = {c["op"]["type"] for c in s["candidates"]}
        self.assertNotIn("weaken", types)
        self.assertIn("narrow", types)

    def test_failed_op_leaves_kb_unchanged(self):
        before = self.rev.kb.to_dict()
        with self.assertRaises(ValueError):
            self.rev.apply({"type": "weaken", "rule_id": self.dresden_rule, "condition": "not foo(Y)"})
        self.assertEqual(before, self.rev.kb.to_dict())
        self.assertEqual(self.rev.history, [])


if __name__ == "__main__":
    unittest.main()
