"""
Interactive, structured revision of a standpoint-labelled rule base.

Repair operators
----------------
  remove(r)            classical contraction: drop rule r entirely
  weaken(r, not L)     add an exception condition to r's body (r now applies in fewer cases)
  narrow(r, t)         move r from standpoint s to a strictly sharper standpoint t ≼ s
                       (r keeps holding where it was meant to, but stops being imposed on
                        sibling standpoints) — not expressible in classical AGM revision
  detach(r, s')        move r into a new standpoint s' that does NOT sharpen the standpoint
                       r conflicted with: both views are kept, the disagreement is made
                       explicit instead of being treated as an error

Candidate generation
--------------------
Given a conflict p / ¬p in standpoint s for some case, the minimal conflict sets
(justifications) are computed by the reasoner. Every rule occurring in a
justification yields remove/narrow/detach candidates; weaken candidates take
literals from the *opposing* derivation and lift them to the rule's variables
(e.g. add `not freier_beruf(X)` to the rule that concluded must_register(X)).

Ranking (minimal change)
------------------------
Each candidate is simulated on a copy of the knowledge base against *every*
stored case and every standpoint. A candidate is admissible if it removes the
target conflict and introduces no new conflict. Admissible candidates are ranked by
  1. number of *other* conflicts it also resolves (more is better — a repair should
     address the conflict class, not overfit to the one case at hand)
  2. number of lost consequences   (fewer is better — AGM minimal change)
  3. operator preference           weaken < narrow < detach < remove
"""

from __future__ import annotations

import copy
import time
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .engine import (
    UNIVERSAL,
    KnowledgeBase,
    Literal,
    Model,
    Reasoner,
    Rule,
    is_var,
    parse_body_item,
    parse_literal,
)

OP_PREF = {"weaken": 0, "narrow": 1, "detach": 2, "remove": 3}

Consequence = Tuple[str, str, str]  # (case, standpoint, literal)


class RevisionEngine:
    def __init__(self, kb: KnowledgeBase, cases: Optional[Dict[str, List[Literal]]] = None) -> None:
        self.kb = kb
        self.cases: Dict[str, List[Literal]] = dict(cases or {})
        self.history: List[dict] = []          # applied operations
        self._undo: List[KnowledgeBase] = []    # snapshots before each op
        self.version = 1

    # ------------------------------------------------------------------ #
    # applying operations
    # ------------------------------------------------------------------ #
    def apply(self, op: dict, reason: str = "") -> dict:
        snapshot = copy.deepcopy(self.kb)
        try:
            desc = _apply(self.kb, op)
        except Exception:
            self.kb.__dict__.update(snapshot.__dict__)
            raise
        self._undo.append(snapshot)
        self.version += 1
        entry = {"version": self.version, "op": op, "description": desc, "reason": reason,
                 "time": time.strftime("%H:%M:%S")}
        self.history.append(entry)
        return entry

    def undo(self) -> Optional[dict]:
        if not self._undo:
            return None
        snap = self._undo.pop()
        self.kb.__dict__.update(snap.__dict__)
        last = self.history.pop()
        self.version += 1
        return last

    # ------------------------------------------------------------------ #
    # global analysis
    # ------------------------------------------------------------------ #
    def consequences(self, kb: Optional[KnowledgeBase] = None) -> Tuple[Set[Consequence], List[Tuple[str, str, str]]]:
        """All derived literals and all conflicts, over every case × every standpoint."""
        kb = kb or self.kb
        r = Reasoner(kb)
        cons: Set[Consequence] = set()
        conflicts: List[Tuple[str, str, str]] = []
        for cname, facts in self.cases.items():
            for s in kb.standpoints:
                if s == UNIVERSAL:
                    continue
                m = r.model(s, facts)
                for l in m.derived:
                    if l not in m.facts:
                        cons.add((cname, s, str(l)))
                for p, _ in m.conflicts():
                    conflicts.append((cname, s, str(p)))
        return cons, conflicts

    def overview(self) -> dict:
        _, conflicts = self.consequences()
        r = Reasoner(self.kb)
        out = []
        for c, s, l in sorted(conflicts):
            m = r.model(s, self.cases[c])
            p = parse_literal(l)
            js = r.justifications(m, p, p.negate())
            out.append({"case": c, "standpoint": s, "literal": l,
                        "rules": sorted({x for j in js for x in j if not x.startswith("F:")})})
        return {"conflicts": out, "n_conflicts": len(out)}

    # ------------------------------------------------------------------ #
    # repair suggestions
    # ------------------------------------------------------------------ #
    def suggest(self, case: str, standpoint: str, literal: str, limit: int = 12) -> dict:
        facts = self.cases[case]
        reasoner = Reasoner(self.kb)
        m = reasoner.model(standpoint, facts)
        p = parse_literal(literal)
        if p.neg:
            p = p.negate()
        q = p.negate()
        if not (m.holds(p) and m.holds(q)):
            return {"conflict": None, "candidates": [], "justifications": []}

        justs = reasoner.justifications(m, p, q)
        rule_ids = sorted({x for j in justs for x in j if not x.startswith("F:")})
        sup_p = {x for s in reasoner.supports(m, p) for x in s}
        sup_q = {x for s in reasoner.supports(m, q) for x in s}

        cands: List[dict] = []
        seen: Set[str] = set()

        def add(op: dict, text: str) -> None:
            k = repr(sorted(op.items()))
            if k not in seen:
                seen.add(k)
                cands.append({"op": op, "text": text})

        for rid in rule_ids:
            r = self.kb.rules[rid]
            add({"type": "remove", "rule_id": rid}, f"Remove {rid} ({r.standpoint}) entirely")

            # narrow to strictly sharper standpoints
            for t in self.kb.standpoints:
                if t not in (r.standpoint, UNIVERSAL) and self.kb.sharper_than(t, r.standpoint):
                    add({"type": "narrow", "rule_id": rid, "to": t},
                        f"Narrow {rid} from {r.standpoint} to {t} (stop imposing it on other standpoints)")

            # detach into an independent standpoint (only meaningful for rules that
            # conflict with something inherited from a broader standpoint)
            other = sup_q if rid in sup_p else sup_p
            other_sps = {self.kb.rules[x].standpoint for x in other if not x.startswith("F:")}
            if r.standpoint != UNIVERSAL and any(o != r.standpoint and self.kb.sharper_than(r.standpoint, o) for o in other_sps):
                new_sp = f"{r.standpoint}_practice"
                add({"type": "detach", "rule_id": rid, "new_standpoint": new_sp,
                     "label": f"{self.kb.standpoints[r.standpoint]['label']} (local practice)"},
                    f"Keep both: move {rid} into a separate standpoint '{new_sp}' that does not inherit "
                    f"the conflicting rule — the disagreement becomes explicit")

            # weaken: exceptions lifted from the opposing derivation
            mine = p if rid in sup_p else q
            theirs = q if mine == p else p
            theta = _theta_for(m, rid, mine)
            if theta is None:
                continue
            inv = {v: k for k, v in theta.items()}
            # only literals specific to the opposing side: an exception on a condition the
            # rule itself relies on would make the rule vacuous (= removal in disguise)
            own = _closure(m, mine)
            for L in sorted(_closure(m, theirs) - own, key=str):
                if L in (p, q):
                    continue
                lifted = _lift(L, inv)
                if lifted is None:
                    continue
                cond = f"not {lifted}"
                if any(str(b) == cond for b in r.body):
                    continue
                add({"type": "weaken", "rule_id": rid, "condition": cond},
                    f"Weaken {rid}: add exception `{cond}`")

        # simulate every candidate against all cases
        base_cons, base_conf = self.consequences()
        base_conf_set = set(base_conf)
        scored = []
        for c in cands:
            kb2 = copy.deepcopy(self.kb)
            try:
                _apply(kb2, c["op"])
                cons2, conf2 = self.consequences(kb2)
            except Exception as e:  # e.g. unstratifiable weakening
                c.update(admissible=False, error=str(e))
                scored.append(c)
                continue
            conf2_set = set(conf2)
            resolved = (case, standpoint, str(p)) not in conf2_set
            new_conf = sorted(conf2_set - base_conf_set)
            fixed_other = sorted((base_conf_set - conf2_set) - {(case, standpoint, str(p))})
            lost = sorted(base_cons - cons2)
            # literal conflicting in target is expected to lose one side — don't double count
            c.update(
                admissible=resolved and not new_conf,
                resolves=resolved,
                new_conflicts=[list(x) for x in new_conf],
                also_fixes=[list(x) for x in fixed_other],
                lost=[list(x) for x in lost],
                n_lost=len(lost),
                gained=len(cons2 - base_cons),
            )
            scored.append(c)

        scored.sort(key=lambda c: (
            not c.get("admissible", False),
            -len(c.get("also_fixes", [])),   # generality: fix the whole conflict class
            c.get("n_lost", 10 ** 6),        # minimal change
            OP_PREF[c["op"]["type"]],
        ))
        for i, c in enumerate(scored):
            c["rank"] = i + 1
        if scored and scored[0].get("admissible"):
            scored[0]["recommended"] = True

        return {
            "conflict": {"case": case, "standpoint": standpoint, "literal": str(p)},
            "justifications": [sorted(j) for j in justs],
            "rules": [self.kb.rules[r].to_dict() for r in rule_ids],
            "proof_pos": reasoner.proof_tree(m, p),
            "proof_neg": reasoner.proof_tree(m, q),
            "candidates": scored[:limit],
            "n_candidates": len(scored),
        }


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _apply(kb: KnowledgeBase, op: dict) -> str:
    t = op["type"]
    if t == "add_rule":
        r = kb.add_rule(op["text"], standpoint=op.get("standpoint", UNIVERSAL),
                        source=op.get("source", ""), note=op.get("note", ""))
        _check_stratified(kb)
        return f"added {r.id} [{r.standpoint}]: {r}"
    if t == "add_standpoint":
        kb.add_standpoint(op["name"], op.get("parents", [UNIVERSAL]), op.get("label", ""))
        return f"added standpoint {op['name']} ≼ {', '.join(op.get('parents', [UNIVERSAL]))}"

    r = kb.rules.get(op.get("rule_id", ""))
    if r is None:
        raise ValueError(f"unknown rule {op.get('rule_id')!r}")

    if t == "remove":
        kb.remove_rule(r.id)
        return f"removed {r.id} [{r.standpoint}]: {r}"
    if t == "weaken":
        item = parse_body_item(op["condition"])
        r.body.append(item)
        if not r.is_safe():
            r.body.pop()
            raise ValueError("condition introduces unbound variables")
        _check_stratified(kb)
        return f"weakened {r.id} with `{item}`"
    if t == "narrow":
        to = op["to"]
        if not (to != r.standpoint and kb.sharper_than(to, r.standpoint)):
            raise ValueError(f"{to} is not strictly sharper than {r.standpoint}")
        old = r.standpoint
        r.standpoint = to
        return f"narrowed {r.id} from {old} to {to}"
    if t == "detach":
        name = op["new_standpoint"]
        if name not in kb.standpoints:
            kb.add_standpoint(name, op.get("parents", [UNIVERSAL]), op.get("label", name))
        old = r.standpoint
        r.standpoint = name
        return f"detached {r.id} from {old} into independent standpoint {name}"
    if t == "move":
        if op["to"] not in kb.standpoints:
            raise ValueError(f"unknown standpoint {op['to']!r}")
        old = r.standpoint
        r.standpoint = op["to"]
        return f"moved {r.id} from {old} to {op['to']}"
    raise ValueError(f"unknown operation {t!r}")


def _check_stratified(kb: KnowledgeBase) -> None:
    from .engine import stratify
    for s in kb.standpoints:
        stratify(kb.rules_for(s))


def _theta_for(m: Model, rule_id: str, lit: Literal) -> Optional[dict]:
    """Variable binding of rule_id somewhere in the derivation of lit."""
    seen: Set[Literal] = set()
    todo = [lit]
    while todo:
        l = todo.pop()
        if l in seen:
            continue
        seen.add(l)
        for d in m.derivations.get(l, []):
            if d.rule_id == rule_id:
                return d.theta
            todo.extend(d.premises)
    return None


def _closure(m: Model, lit: Literal) -> Set[Literal]:
    """All literals used in some derivation of lit (including lit itself)."""
    seen: Set[Literal] = set()
    todo = [lit]
    while todo:
        l = todo.pop()
        if l in seen:
            continue
        seen.add(l)
        for d in m.derivations.get(l, []):
            todo.extend(d.premises)
    return seen


def _lift(L: Literal, inv: Dict) -> Optional[Literal]:
    """Replace constants by the rule's variables; None if some constant has no variable."""
    args = []
    for a in L.args:
        if a in inv:
            args.append(inv[a])
        elif isinstance(a, (int, float)):
            return None
        else:
            return None
    if not args:
        return None
    return Literal(L.pred, tuple(args), L.neg)
