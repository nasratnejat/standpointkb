"""
Example knowledge base: starting self-employed work in Germany.

Standpoint hierarchy (sharpening, ≼):

        *  (universal)
        │
       bund            federal law (simplified)
        │
      sachsen          Free State of Saxony
       ╱  ╲
  dresden  leipzig     municipal practice

IMPORTANT: this is a research demo, not legal advice. Federal rules are simplified
paraphrases of real provisions; the municipal rules are ILLUSTRATIVE (constructed
to show realistic kinds of local divergence) and are marked as such.
"""

from .engine import KnowledgeBase, parse_literal
from .revision import RevisionEngine

STANDPOINTS = [
    ("bund", ["*"], "Bund (federal)"),
    ("sachsen", ["bund"], "Sachsen (state)"),
    ("dresden", ["sachsen"], "Dresden (city)"),
    ("leipzig", ["sachsen"], "Leipzig (city)"),
]

RULES = [
    # --- universal: plain definitions -------------------------------------------
    ("*", "freier_beruf(X) → self_employed(X)", "", "definition shared by every standpoint"),
    ("*", "gewerbe(X) → self_employed(X)", "", "definition shared by every standpoint"),
    ("*", "freelancer(X) → self_employed(X)", "", "definition shared by every standpoint"),

    # --- federal ------------------------------------------------------------------
    ("bund", "self_employed(X) ∧ revenue(X, R) ∧ R <= 25000 → kleinunternehmer(X)",
     "§19 UStG (from 2025: prior-year revenue ≤ 25,000 €)", "simplified: ignores the 100,000 € current-year cap"),
    ("bund", "kleinunternehmer(X) → ¬must_charge_vat(X)", "§19 UStG", ""),
    ("bund", "self_employed(X) ∧ revenue(X, R) ∧ R > 25000 → must_charge_vat(X)", "§19 UStG", ""),
    ("bund", "self_employed(X) → must_register_finanzamt(X)",
     "§138 AO (Fragebogen zur steuerlichen Erfassung)", ""),
    ("bund", "software_dev(X) → freier_beruf(X)", "§18 EStG",
     "simplified: in practice decided case-by-case ('ingenieurähnlicher Beruf')"),
    ("bund", "creative(X) → freier_beruf(X)", "§18 EStG (künstlerische Tätigkeit)", ""),
    ("bund", "creative(X) → ksk_eligible(X)", "KSVG (Künstlersozialkasse)", ""),
    ("bund", "sells_goods(X) → gewerbe(X)", "§1 HGB / §14 GewO", ""),
    ("bund", "cafe(X) → gewerbe(X)", "§14 GewO", ""),
    ("bund", "freier_beruf(X) → ¬gewerbe(X)", "§6 GewO, §18 EStG", "free professions are not a Gewerbe"),
    ("bund", "gewerbe(X) → must_register_gewerbe(X)", "§14 GewO (Gewerbeanmeldung)", ""),
    ("bund", "freier_beruf(X) → ¬must_register_gewerbe(X)", "§14 GewO", "no Gewerbeanmeldung for freie Berufe"),
    ("bund", "gewerbe(X) ∧ profit(X, P) ∧ P > 24500 → must_pay_gewerbesteuer(X)",
     "§11 GewStG (Freibetrag 24,500 €)", ""),
    ("bund", "gewerbe(X) ∧ profit(X, P) ∧ P <= 24500 → ¬must_pay_gewerbesteuer(X)",
     "§11 GewStG (Freibetrag 24,500 €)", "simplified: Freibetrag applies to sole traders"),
    ("bund", "freier_beruf(X) → ¬must_pay_gewerbesteuer(X)", "§2 GewStG", ""),
    ("bund", "gewerbe(X) → must_join_ihk(X)", "§2 IHKG", ""),
    ("bund", "moved(X) → must_register_residence(X)", "§17 BMG (within 2 weeks)", ""),
    ("bund", "cafe(X) → needs_hygiene_instruction(X)", "§43 IfSG", ""),

    # --- Sachsen -----------------------------------------------------------------
    ("sachsen", "cafe(X) ∧ serves_alcohol(X) → needs_gaststaettenerlaubnis(X)",
     "SächsGastG (simplified — verify)", "several other Länder abolished this permit"),

    # --- Dresden (ILLUSTRATIVE local practice) ------------------------------------
    ("dresden", "freelancer(X) → must_register_gewerbe(X)",
     "ILLUSTRATIVE: local office practice 'register first, classify later'",
     "constructed example of a local rule that over-generalises federal law"),

    # --- Leipzig (ILLUSTRATIVE) ---------------------------------------------------
    ("leipzig", "cafe(X) ∧ outdoor_seating(X) → needs_sondernutzung(X)",
     "ILLUSTRATIVE: municipal Sondernutzungssatzung", ""),
]

CASES = {
    "nasrat": "freelancer(nasrat), software_dev(nasrat), revenue(nasrat, 18000), profit(nasrat, 15000), moved(nasrat)",
    "jonas": "freelancer(jonas), creative(jonas), revenue(jonas, 32000), profit(jonas, 26000)",
    "lena": "freelancer(lena), sells_goods(lena), revenue(lena, 41000), profit(lena, 30000)",
    "mia": "freelancer(mia), revenue(mia, 12000), profit(mia, 9000)",
    "cafe_elbe": "cafe(cafe_elbe), serves_alcohol(cafe_elbe), outdoor_seating(cafe_elbe), profit(cafe_elbe, 52000)",
}

CASE_LABELS = {
    "nasrat": "Software developer, freelance, 18k € revenue, just moved",
    "jonas": "Illustrator, freelance, 32k € revenue",
    "lena": "Sells handmade goods online, 41k € revenue",
    "mia": "Freelancer, activity not yet classified, 12k €",
    "cafe_elbe": "Café with alcohol licence and outdoor seating",
}


def parse_facts(text: str):
    from .engine import split_conj
    return [parse_literal(p) for p in split_conj(text.replace(";", ","))]


def build() -> RevisionEngine:
    kb = KnowledgeBase()
    for name, parents, label in STANDPOINTS:
        kb.add_standpoint(name, parents, label)
    for sp, text, source, note in RULES:
        kb.add_rule(text, standpoint=sp, source=source, note=note)
    return RevisionEngine(kb, {k: parse_facts(v) for k, v in CASES.items()})
