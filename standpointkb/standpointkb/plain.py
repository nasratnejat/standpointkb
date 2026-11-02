"""
Citizen view: turn standpoint reasoning into a plain-language checklist.

The citizen fills in a short profile; the profile becomes case facts about the
constant `you`; every *topic* (an obligation predicate) is asked in the citizen's
city standpoint and in federal law, and the result is rendered in plain words.
Nothing here reasons on its own — it only phrases what the reasoner derived.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .engine import UNIVERSAL, Literal, Reasoner, parse_literal

ME = "you"

# predicate → (positive phrase, negative phrase)   — "you …"
PHRASES: Dict[str, tuple] = {
    "freelancer": ("you work as a freelancer", "you do not work as a freelancer"),
    "software_dev": ("you develop software", "you do not develop software"),
    "creative": ("you do creative or artistic work", "your work is not artistic"),
    "sells_goods": ("you sell goods", "you do not sell goods"),
    "cafe": ("you run a café or restaurant", "you do not run a café"),
    "serves_alcohol": ("you serve alcohol", "you do not serve alcohol"),
    "outdoor_seating": ("you have tables outside on public ground", "you have no outdoor seating"),
    "moved": ("you recently moved", "you did not move"),
    "freier_beruf": ("your work counts as a 'free profession' (freier Beruf)",
                     "your work is not a 'free profession'"),
    "gewerbe": ("your work counts as a trade (Gewerbe)", "your work is not a trade (Gewerbe)"),
    "self_employed": ("you are self-employed", "you are not self-employed"),
    "kleinunternehmer": ("you count as a small business (Kleinunternehmer)",
                         "you do not count as a small business"),
    "ksk_eligible": ("you can join the artists' social insurance (KSK)", "you cannot join the KSK"),
    "must_register_finanzamt": ("you must register with the tax office", "you need not register with the tax office"),
    "must_register_gewerbe": ("you must register a trade (Gewerbeanmeldung)",
                              "you do not need to register a trade"),
    "must_charge_vat": ("you must charge VAT on your invoices", "you do not charge VAT"),
    "must_pay_gewerbesteuer": ("you must pay trade tax (Gewerbesteuer)", "you do not pay trade tax"),
    "must_join_ihk": ("you become a member of the IHK", "you are not an IHK member"),
    "must_register_residence": ("you must register your new address", "you need not register an address"),
    "needs_hygiene_instruction": ("you and your staff need a hygiene instruction", "no hygiene instruction is needed"),
    "needs_gaststaettenerlaubnis": ("you need a restaurant licence (Gaststättenerlaubnis)",
                                    "you do not need a restaurant licence"),
    "needs_sondernutzung": ("you need a permit for using public space (Sondernutzung)",
                            "you need no permit for public space"),
}

# the checklist, in display order:  (predicate, question, what-to-do hint when YES)
TOPICS = [
    ("must_register_finanzamt", "Register with the tax office (Finanzamt)?",
     "Fill in the 'Fragebogen zur steuerlichen Erfassung' on ELSTER within 4 weeks of starting."),
    ("must_register_gewerbe", "Register a trade (Gewerbeanmeldung)?",
     "Go to the Gewerbeamt of your city before you start."),
    ("must_charge_vat", "Charge VAT (Umsatzsteuer) on invoices?",
     "Add 19 % VAT to invoices and file VAT returns."),
    ("must_pay_gewerbesteuer", "Pay trade tax (Gewerbesteuer)?",
     "The city sends a tax notice based on your profit."),
    ("must_join_ihk", "Become an IHK member?",
     "Membership is automatic; small businesses often pay no fee."),
    ("ksk_eligible", "Join the artists' social insurance (KSK)?",
     "Apply at the Künstlersozialkasse — it pays part of your insurance."),
    ("must_register_residence", "Register your address (Anmeldung)?",
     "Go to the Bürgeramt within 2 weeks of moving."),
    ("needs_hygiene_instruction", "Get a hygiene instruction (§43 IfSG)?",
     "Book the instruction at the health office before staff handle food."),
    ("needs_gaststaettenerlaubnis", "Get a restaurant licence?",
     "Apply at the city's Ordnungsamt before serving alcohol."),
    ("needs_sondernutzung", "Get a permit for outdoor seating?",
     "Apply for a Sondernutzungserlaubnis before putting tables outside."),
]

WORK_TYPES = {
    "software": ["freelancer", "software_dev"],
    "creative": ["freelancer", "creative"],
    "goods": ["freelancer", "sells_goods"],
    "cafe": ["cafe"],
    "other": ["freelancer"],
}


def profile_to_facts(p: dict) -> List[Literal]:
    facts = [Literal(pred, (ME,)) for pred in WORK_TYPES.get(p.get("work", "other"), ["freelancer"])]
    for flag in ("serves_alcohol", "outdoor_seating", "moved"):
        if p.get(flag):
            facts.append(Literal(flag, (ME,)))
    for num in ("revenue", "profit"):
        v = p.get(num)
        if v not in (None, ""):
            facts.append(Literal(num, (ME, int(float(v)))))
    return facts


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:]


def phrase(lit: str) -> str:
    neg = lit.startswith("¬")
    m = re.match(r"(\w+)\((.*)\)", lit.lstrip("¬"))
    if not m:
        return lit
    pred, args = m.group(1), [a.strip() for a in m.group(2).split(",")]
    if pred in ("revenue", "profit"):
        word = "yearly revenue" if pred == "revenue" else "yearly profit"
        return f"your {word} is {int(args[1]):,} €".replace(",", ".")
    if pred in PHRASES:
        return PHRASES[pred][1 if neg else 0]
    return ("not " if neg else "") + pred.replace("_", " ")


def steps(tree: Optional[dict]) -> List[dict]:
    """Proof tree → ordered plain-language reasoning steps (premises first)."""
    out: List[dict] = []
    seen = set()

    def rec(n: dict) -> None:
        for c in n.get("children", []):
            rec(c)
        if n["literal"] in seen:
            return
        seen.add(n["literal"])
        k = n["kind"]
        if k == "fact":
            out.append({"kind": "fact", "text": "You told us: " + phrase(n["literal"]) + "."})
        elif k == "check":
            m = re.search(r"\[(.*)\]", n["literal"])
            if m:
                a, op, b = m.group(1).split()
                word = {"<=": "at most", "<": "below", ">": "above", ">=": "at least"}.get(op, op)
                out.append({"kind": "check", "text": f"{int(float(a)):,} € is {word} the limit of {int(float(b)):,} €.".replace(",", ".")})
        elif k == "absent":
            out.append({"kind": "absent", "text": "No exception applies: it is not the case that " + phrase(n["literal"][4:]) + "."})
        elif k == "rule":
            out.append({"kind": "rule", "text": "So " + phrase(n["literal"]) + ".",
                        "source": n.get("source", ""), "standpoint": n["standpoint"], "rule_id": n["rule_id"]})

    if tree:
        rec(tree)
    return out


def top_rule_standpoint(tree: Optional[dict]) -> str:
    return tree.get("standpoint", "") if tree and tree.get("kind") == "rule" else ""


def checklist(kb, profile: dict, city: str) -> dict:
    facts = profile_to_facts(profile)
    r = Reasoner(kb)
    label = lambda s: re.sub(r"\s*\((city|federal|state)\)$", "", kb.standpoints[s]["label"]) if s in kb.standpoints else s
    practice = [s for s in kb.standpoints if s.startswith(city + "_practice")]
    items = []
    for pred, question, todo in TOPICS:
        goal = Literal(pred, (ME,))
        res = {x["standpoint"]: x for x in r.query(facts, goal, ["bund", city] + practice)["results"]}
        c, b = res[city], res["bund"]
        v = c["verdict"]
        item = {"topic": pred, "goal": str(goal), "question": question, "todo": todo,
                "city_label": label(city)}
        if v == "CONFLICT":
            pos_sp, neg_sp = top_rule_standpoint(c["proof"]), top_rule_standpoint(c["counter_proof"])
            item.update(status="unclear",
                        headline="Unclear — the rules contradict each other",
                        detail=f"{label(pos_sp)} says yes, but {label(neg_sp)} says no. "
                               f"Ask the office in {label(city)} to confirm before you start.",
                        why_yes=steps(c["proof"]), why_no=steps(c["counter_proof"]))
        elif v == "YES":
            item.update(status="yes", headline="Yes", detail=_cap(phrase(str(goal))) + ".",
                        why=steps(c["proof"]))
        elif v == "NO":
            item.update(status="no", headline="No", detail=_cap(phrase("¬" + str(goal))) + ".",
                        why=steps(c["counter_proof"]))
        else:
            item.update(status="na", headline="Does not apply", detail="No rule requires this for you.", why=[])

        # differences between official rules and local office practice
        notes = []
        for s in practice:
            pv = res[s]["verdict"]
            if pv in ("YES", "NO") and pv != v:
                notes.append(f"Heads-up: in practice the {label(s)} says "
                             f"{'yes' if pv == 'YES' else 'no'} — "
                             f"{phrase(str(goal) if pv == 'YES' else '¬' + str(goal))}. "
                             f"Official rules and local practice differ.")
        if v in ("YES", "NO") and b["verdict"] in ("YES", "NO") and b["verdict"] != v:
            notes.append(f"Federal law alone would say {'yes' if b['verdict'] == 'YES' else 'no'}; "
                         f"{label(city)} adds its own rule.")
        item["notes"] = notes
        items.append(item)

    order = {"unclear": 0, "yes": 1, "no": 2, "na": 3}
    items.sort(key=lambda i: order[i["status"]])
    cities = [s for s in kb.sharpest() if "_practice" not in s]
    return {"city": city, "city_label": label(city), "items": items,
            "facts": ", ".join(str(f) for f in facts), "cities": cities}
