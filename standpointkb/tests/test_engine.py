import unittest

from standpointkb.engine import KnowledgeBase, Reasoner, parse_literal, parse_rule, stratify
from standpointkb.seed import build


def lit(s):
    return parse_literal(s)


class ParserTests(unittest.TestCase):
    def test_arrow_and_prolog_syntax_agree(self):
        a = parse_rule("p(X) ∧ q(X, Y) ∧ Y > 3 → r(X)")
        b = parse_rule("r(X) :- p(X), q(X, Y), Y > 3")
        self.assertEqual(str(a), str(b))

    def test_strong_negation_vs_naf(self):
        r = parse_rule("p(X) ∧ not q(X) → ¬s(X)")
        self.assertTrue(r.head.neg)
        self.assertEqual(str(r.body[1]), "not q(X)")

    def test_unsafe_rule_rejected(self):
        with self.assertRaises(ValueError):
            parse_rule("p(X) → q(Y)")
        with self.assertRaises(ValueError):
            parse_rule("p(X) ∧ not q(Y) → r(X)")


class StandpointTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase()
        self.kb.add_standpoint("bund")
        self.kb.add_standpoint("sachsen", ["bund"])
        self.kb.add_standpoint("dresden", ["sachsen"])
        self.kb.add_standpoint("leipzig", ["sachsen"])

    def test_sharpening_is_reflexive_transitive(self):
        self.assertTrue(self.kb.sharper_than("dresden", "bund"))
        self.assertTrue(self.kb.sharper_than("dresden", "dresden"))
        self.assertFalse(self.kb.sharper_than("bund", "dresden"))
        self.assertFalse(self.kb.sharper_than("dresden", "leipzig"))

    def test_cycle_rejected(self):
        with self.assertRaises(ValueError):
            self.kb.standpoints["bund"]["parents"] = ["dresden"]
            self.kb.add_standpoint("x", ["bund"])

    def test_inheritance_down_not_up(self):
        self.kb.add_rule("a(X) → b(X)", "bund")
        self.kb.add_rule("a(X) → c(X)", "dresden")
        r = Reasoner(self.kb)
        f = [lit("a(k)")]
        self.assertTrue(r.model("dresden", f).holds(lit("b(k)")))   # inherited from bund
        self.assertTrue(r.model("dresden", f).holds(lit("c(k)")))
        self.assertFalse(r.model("bund", f).holds(lit("c(k)")))     # not imposed upward
        self.assertFalse(r.model("leipzig", f).holds(lit("c(k)")))  # not imposed on siblings

    def test_disagreement_is_not_inconsistency(self):
        self.kb.add_rule("a(X) → b(X)", "dresden")
        self.kb.add_rule("a(X) → ¬b(X)", "leipzig")
        r = Reasoner(self.kb)
        q = r.query([lit("a(k)")], lit("b(k)"), ["dresden", "leipzig"])
        self.assertEqual(q["summary"], {"dresden": "YES", "leipzig": "NO"})
        self.assertTrue(q["disagreement"])
        self.assertTrue(all(x["consistent"] for x in q["results"]))

    def test_box_and_diamond(self):
        self.kb.add_rule("a(X) → b(X)", "bund")
        r = Reasoner(self.kb)
        res = {x["standpoint"]: x for x in r.query([lit("a(k)")], lit("c(k)"), ["bund"])["results"]}
        self.assertFalse(res["bund"]["necessarily"])  # □ c not derivable
        self.assertTrue(res["bund"]["possibly"])      # ◇ c: ¬c not derivable either


class ReasoningTests(unittest.TestCase):
    def test_comparisons(self):
        kb = KnowledgeBase()
        kb.add_rule("income(X, I) ∧ I <= 100 → low(X)")
        m = Reasoner(kb).model("*", [lit("income(a, 50)"), lit("income(b, 500)")])
        self.assertTrue(m.holds(lit("low(a)")))
        self.assertFalse(m.holds(lit("low(b)")))

    def test_naf_and_stratification(self):
        kb = KnowledgeBase()
        kb.add_rule("p(X) ∧ not q(X) → r(X)")
        kb.add_rule("s(X) → q(X)")
        m = Reasoner(kb).model("*", [lit("p(a)"), lit("p(b)"), lit("s(b)")])
        self.assertTrue(m.holds(lit("r(a)")))
        self.assertFalse(m.holds(lit("r(b)")))  # q(b) derived in a lower stratum first

    def test_unstratified_program_rejected(self):
        kb = KnowledgeBase()
        kb.add_rule("d(X) ∧ not q(X) → p(X)")
        kb.add_rule("d(X) ∧ not p(X) → q(X)")
        with self.assertRaises(ValueError):
            stratify(list(kb.rules.values()))

    def test_recursion_terminates(self):
        kb = KnowledgeBase()
        kb.add_rule("edge(X, Y) → path(X, Y)")
        kb.add_rule("path(X, Y) ∧ edge(Y, Z) → path(X, Z)")
        facts = [lit(f"edge(n{i}, n{i+1})") for i in range(6)] + [lit("edge(n6, n0)")]
        m = Reasoner(kb).model("*", facts)
        self.assertTrue(m.holds(lit("path(n3, n2)")))
        self.assertEqual(sum(1 for l in m.derived if l.pred == "path"), 49)

    def test_minimal_justification(self):
        kb = KnowledgeBase()
        kb.add_rule("a(X) → b(X)", id="R1")
        kb.add_rule("b(X) → c(X)", id="R2")
        kb.add_rule("a(X) → ¬c(X)", id="R3")
        kb.add_rule("a(X) → d(X)", id="R4")   # irrelevant
        r = Reasoner(kb)
        m = r.model("*", [lit("a(k)")])
        js = r.justifications(m, lit("c(k)"), lit("¬c(k)"))
        self.assertEqual([sorted(j) for j in js], [["F:a(k)", "R1", "R2", "R3"]])


class SeedTests(unittest.TestCase):
    def test_seed_has_expected_conflicts(self):
        rev = build()
        ov = rev.overview()
        found = {(c["case"], c["standpoint"]) for c in ov["conflicts"]}
        self.assertEqual(found, {("nasrat", "dresden"), ("jonas", "dresden")})

    def test_federal_answers(self):
        rev = build()
        r = Reasoner(rev.kb)
        f = rev.cases["nasrat"]
        q = r.query(f, lit("must_register_gewerbe(nasrat)"))
        self.assertEqual(q["summary"]["bund"], "NO")
        self.assertEqual(q["summary"]["dresden"], "CONFLICT")
        self.assertEqual(r.query(f, lit("must_charge_vat(nasrat)"))["summary"]["bund"], "NO")
        lena = rev.cases["lena"]
        self.assertEqual(r.query(lena, lit("must_pay_gewerbesteuer(lena)"))["summary"]["bund"], "YES")


if __name__ == "__main__":
    unittest.main()
