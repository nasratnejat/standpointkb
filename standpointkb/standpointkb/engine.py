"""
Core reasoning engine.

Logic: Datalog with *explicit* (strong) negation and arithmetic comparisons,
extended with standpoint labels.

Standpoint semantics (simplified from Gómez Álvarez & Rudolph, "Standpoint Logic"):
  * every rule carries a standpoint label s;
  * standpoints form a partial order under the *sharpening* relation  s ≼ s'
    ("s is sharper than s'", e.g. dresden ≼ sachsen ≼ bund);
  * the universal standpoint "*" is broader than every other standpoint;
  * a sharper standpoint inherits all rules of the broader standpoints above it,
    i.e.  rules(s) = { r | label(r) ∈ up(s) },  up(s) = {s} ∪ {s' | s ≼* s'};
  * □_s φ  ("according to s, necessarily φ") holds iff φ is derivable from rules(s);
  * ◇_s φ  ("according to s, possibly φ") holds iff ¬φ is *not* derivable from rules(s).

A standpoint s is *inconsistent* for a case iff both p and ¬p are derivable in s.
Because standpoints are kept separate, p holding in s1 and ¬p in s2 is NOT a
contradiction — it is a disagreement between standpoints, which the engine reports.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple, Union

UNIVERSAL = "*"

# --------------------------------------------------------------------------- #
# Terms and literals
# --------------------------------------------------------------------------- #

Term = Union[str, int, float]


def is_var(t: Term) -> bool:
    return isinstance(t, str) and t[:1].isupper()


@dataclass(frozen=True)
class Literal:
    pred: str
    args: Tuple[Term, ...]
    neg: bool = False

    def __str__(self) -> str:
        a = ", ".join(str(x) for x in self.args)
        return f"{'¬' if self.neg else ''}{self.pred}({a})"

    def negate(self) -> "Literal":
        return Literal(self.pred, self.args, not self.neg)

    def ground(self) -> bool:
        return not any(is_var(a) for a in self.args)

    def subst(self, theta: Dict[str, Term]) -> "Literal":
        return Literal(self.pred, tuple(theta.get(a, a) if is_var(a) else a for a in self.args), self.neg)

    def vars(self) -> Set[str]:
        return {a for a in self.args if is_var(a)}


@dataclass(frozen=True)
class Comparison:
    left: Term
    op: str
    right: Term

    OPS = {
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "=": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"

    def vars(self) -> Set[str]:
        return {a for a in (self.left, self.right) if is_var(a)}

    def holds(self, theta: Dict[str, Term]) -> bool:
        l = theta.get(self.left, self.left) if is_var(self.left) else self.left
        r = theta.get(self.right, self.right) if is_var(self.right) else self.right
        if is_var(l) or is_var(r):
            return False
        try:
            return Comparison.OPS[self.op](l, r)
        except TypeError:
            return False


@dataclass(frozen=True)
class Naf:
    """Negation as failure:  `not L`  holds iff L is not derivable (closed-world on L).

    Distinct from strong negation ¬L, which must be positively derived.
    Programs must be stratified (no recursion through `not`)."""
    lit: Literal

    def __str__(self) -> str:
        return f"not {self.lit}"

    def vars(self) -> Set[str]:
        return self.lit.vars()

    def subst(self, theta: Dict[str, Term]) -> "Naf":
        return Naf(self.lit.subst(theta))


BodyItem = Union[Literal, Comparison, Naf]


def key(l: Literal) -> Tuple[str, bool]:
    return (l.pred, l.neg)


@dataclass
class Rule:
    id: str
    head: Literal
    body: List[BodyItem]
    standpoint: str = UNIVERSAL
    source: str = ""      # provenance: law / website / authority
    note: str = ""        # free-text comment

    def __str__(self) -> str:
        if not self.body:
            return f"{self.head}"
        return " ∧ ".join(str(b) for b in self.body) + f" → {self.head}"

    def is_safe(self) -> bool:
        """Datalog safety: every head variable occurs in a positive-or-negative body literal."""
        body_vars: Set[str] = set()
        for b in self.body:
            if isinstance(b, Literal):
                body_vars |= b.vars()
        for b in self.body:
            if isinstance(b, (Comparison, Naf)) and not b.vars() <= body_vars:
                return False
        return self.head.vars() <= body_vars

    def positive_body(self) -> List[Literal]:
        return [b for b in self.body if isinstance(b, Literal)]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": str(self),
            "head": str(self.head),
            "body": [str(b) for b in self.body],
            "standpoint": self.standpoint,
            "source": self.source,
            "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

_LIT_RE = re.compile(r"^\s*(¬|!|~|-)?\s*([a-z_][\w]*)\s*\((.*)\)\s*$")
_NAF_RE = re.compile(r"^\s*not\s+(.*)$")
_CMP_RE = re.compile(r"^\s*([\w.]+)\s*(<=|>=|!=|<|>|=)\s*([\w.]+)\s*$")


def _parse_term(tok: str) -> Term:
    tok = tok.strip()
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    if re.fullmatch(r"-?\d+\.\d+", tok):
        return float(tok)
    if not re.fullmatch(r"[A-Za-z_][\w]*", tok):
        raise ValueError(f"bad term: {tok!r}")
    return tok


def parse_literal(s: str) -> Literal:
    m = _LIT_RE.match(s)
    if not m:
        raise ValueError(f"cannot parse literal: {s!r}")
    neg, pred, args = m.group(1), m.group(2), m.group(3)
    arglist = tuple(_parse_term(a) for a in args.split(",")) if args.strip() else ()
    return Literal(pred, arglist, bool(neg))


def parse_body_item(s: str) -> BodyItem:
    m = _CMP_RE.match(s)
    if m and "(" not in s:
        return Comparison(_parse_term(m.group(1)), m.group(2), _parse_term(m.group(3)))
    n = _NAF_RE.match(s)
    if n:
        return Naf(parse_literal(n.group(1)))
    return parse_literal(s)


def split_conj(s: str) -> List[str]:
    parts = re.split(r"\s*(?:∧|&&|&|\band\b|,(?![^()]*\)))\s*", s)
    return [p for p in parts if p.strip()]


def parse_rule(text: str, id: str = "", standpoint: str = UNIVERSAL, source: str = "", note: str = "") -> Rule:
    """Parse 'b1 ∧ b2 → h'  (also accepts '->', ':-' in Prolog order 'h :- b1, b2')."""
    text = text.strip()
    if ":-" in text:
        h, b = text.split(":-", 1)
    elif "→" in text or "->" in text or "=>" in text:
        b, h = re.split(r"→|->|=>", text, maxsplit=1)
    else:
        b, h = "", text
    if _NAF_RE.match(h):
        raise ValueError("rule head cannot use `not` (use strong negation ¬ instead)")
    head = parse_literal(h)
    body = [parse_body_item(p) for p in split_conj(b)] if b.strip() else []
    r = Rule(id=id, head=head, body=body, standpoint=standpoint, source=source, note=note)
    if not r.is_safe():
        raise ValueError(f"unsafe rule (head/comparison variable not bound in body): {text}")
    return r


# --------------------------------------------------------------------------- #
# Knowledge base
# --------------------------------------------------------------------------- #


class KnowledgeBase:
    def __init__(self) -> None:
        self.rules: Dict[str, Rule] = {}
        self.standpoints: Dict[str, dict] = {UNIVERSAL: {"name": UNIVERSAL, "label": "universal (*)", "parents": []}}
        self._counter = 0

    # ---- standpoints ------------------------------------------------------
    def add_standpoint(self, name: str, parents: Iterable[str] = (UNIVERSAL,), label: str = "") -> None:
        parents = [p for p in parents if p != name]
        for p in parents:
            if p not in self.standpoints:
                raise ValueError(f"unknown parent standpoint {p!r}")
        if not parents:
            parents = [UNIVERSAL]
        self.standpoints[name] = {"name": name, "label": label or name, "parents": list(parents)}
        if self._has_cycle():
            del self.standpoints[name]
            raise ValueError("sharpening relation must be acyclic")

    def _has_cycle(self) -> bool:
        seen: Set[str] = set()

        def visit(n: str, stack: Set[str]) -> bool:
            if n in stack:
                return True
            if n in seen:
                return False
            seen.add(n)
            for p in self.standpoints[n]["parents"]:
                if visit(p, stack | {n}):
                    return True
            return False

        return any(visit(n, set()) for n in self.standpoints)

    def up(self, s: str) -> Set[str]:
        """All standpoints broader-or-equal to s (reflexive-transitive closure of ≼) plus *."""
        if s not in self.standpoints:
            raise ValueError(f"unknown standpoint {s!r}")
        out: Set[str] = set()
        todo = [s]
        while todo:
            n = todo.pop()
            if n in out:
                continue
            out.add(n)
            todo.extend(self.standpoints[n]["parents"])
        out.add(UNIVERSAL)
        return out

    def sharper_than(self, a: str, b: str) -> bool:
        """a ≼ b  (a is sharper than or equal to b)."""
        return b in self.up(a)

    def sharpest(self) -> List[str]:
        """Standpoints that nothing sharpens (leaves) — the most specific viewpoints."""
        parents = {p for sp in self.standpoints.values() for p in sp["parents"]}
        return [s for s in self.standpoints if s not in parents and s != UNIVERSAL]

    # ---- rules ------------------------------------------------------------
    def new_id(self) -> str:
        self._counter += 1
        return f"R{self._counter}"

    def add_rule(self, text: str, standpoint: str = UNIVERSAL, source: str = "", note: str = "", id: str = "") -> Rule:
        if standpoint not in self.standpoints:
            raise ValueError(f"unknown standpoint {standpoint!r}")
        rid = id or self.new_id()
        if rid in self.rules:
            raise ValueError(f"duplicate rule id {rid}")
        r = parse_rule(text, id=rid, standpoint=standpoint, source=source, note=note)
        self.rules[rid] = r
        return r

    def remove_rule(self, rid: str) -> Rule:
        return self.rules.pop(rid)

    def rules_for(self, s: str) -> List[Rule]:
        scope = self.up(s)
        return [r for r in self.rules.values() if r.standpoint in scope]

    def to_dict(self) -> dict:
        return {
            "standpoints": list(self.standpoints.values()),
            "rules": [r.to_dict() for r in self.rules.values()],
        }


# --------------------------------------------------------------------------- #
# Reasoner: forward chaining with derivation bookkeeping
# --------------------------------------------------------------------------- #


@dataclass
class Derivation:
    rule_id: Optional[str]            # None => case fact
    theta: Dict[str, Term]
    premises: Tuple[Literal, ...]     # ground body literals used


@dataclass
class Model:
    """Least model of rules(s) ∪ facts, plus derivation records for explanations."""
    standpoint: str
    facts: Tuple[Literal, ...]
    derived: Set[Literal] = field(default_factory=set)
    derivations: Dict[Literal, List[Derivation]] = field(default_factory=dict)

    def holds(self, lit: Literal) -> bool:
        return lit in self.derived

    def conflicts(self) -> List[Tuple[Literal, Literal]]:
        out = []
        for l in self.derived:
            if not l.neg and l.negate() in self.derived:
                out.append((l, l.negate()))
        return sorted(out, key=lambda p: str(p[0]))

    def consistent(self) -> bool:
        return not self.conflicts()


class Reasoner:
    MAX_SUPPORTS = 40  # cap on support sets per literal (guards exponential blow-up)

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    # ---- model computation ------------------------------------------------
    def model(self, standpoint: str, facts: Iterable[Literal]) -> Model:
        facts = tuple(facts)
        m = Model(standpoint=standpoint, facts=facts)
        for f in facts:
            if not f.ground():
                raise ValueError(f"facts must be ground: {f}")
            m.derived.add(f)
            m.derivations.setdefault(f, []).append(Derivation(None, {}, ()))
        rules = self.kb.rules_for(standpoint)
        for stratum in stratify(rules):
            changed = True
            while changed:
                changed = False
                for r in stratum:
                    for theta in self._match(r.body, m.derived):
                        h = r.head.subst(theta)
                        prem = tuple(b.subst(theta) for b in r.positive_body())
                        d = Derivation(r.id, theta, prem)
                        ds = m.derivations.setdefault(h, [])
                        if not any(x.rule_id == d.rule_id and x.premises == d.premises for x in ds):
                            ds.append(d)
                        if h not in m.derived:
                            m.derived.add(h)
                            changed = True
        return m

    def _match(self, body: List[BodyItem], facts: Set[Literal]):
        lits = [b for b in body if isinstance(b, Literal)]
        nafs = [b for b in body if isinstance(b, Naf)]
        cmps = [b for b in body if isinstance(b, Comparison)]
        # index by predicate/sign for speed
        index: Dict[Tuple[str, bool, int], List[Literal]] = {}
        for f in facts:
            index.setdefault((f.pred, f.neg, len(f.args)), []).append(f)

        def rec(i: int, theta: Dict[str, Term]):
            if i == len(lits):
                if all(c.holds(theta) for c in cmps) and all(n.lit.subst(theta) not in facts for n in nafs):
                    yield dict(theta)
                return
            pat = lits[i]
            for f in index.get((pat.pred, pat.neg, len(pat.args)), []):
                t = dict(theta)
                ok = True
                for a, v in zip(pat.args, f.args):
                    if is_var(a):
                        if a in t and t[a] != v:
                            ok = False
                            break
                        t[a] = v
                    elif a != v:
                        ok = False
                        break
                if ok:
                    yield from rec(i + 1, t)

        yield from rec(0, {})

    # ---- explanations -----------------------------------------------------
    def proof_tree(self, m: Model, lit: Literal, _seen: Optional[FrozenSet[Literal]] = None) -> Optional[dict]:
        """First (shortest-found) derivation tree of lit in model m."""
        if lit not in m.derived:
            return None
        seen = _seen or frozenset()
        for d in m.derivations.get(lit, []):
            if d.rule_id is None:
                return {"literal": str(lit), "kind": "fact", "children": []}
            if any(p in seen for p in d.premises):
                continue
            kids = []
            ok = True
            for p in d.premises:
                sub = self.proof_tree(m, p, seen | {lit})
                if sub is None:
                    ok = False
                    break
                kids.append(sub)
            if ok:
                r = self.kb.rules[d.rule_id]
                for b in r.body:
                    if isinstance(b, Naf):
                        kids.append({"literal": str(b.subst(d.theta)), "kind": "absent", "children": []})
                    elif isinstance(b, Comparison):
                        kids.append({"literal": f"{b}  [{_show_theta(b, d.theta)}]", "kind": "check", "children": []})
                return {
                    "literal": str(lit),
                    "kind": "rule",
                    "rule_id": r.id,
                    "rule": str(r),
                    "standpoint": r.standpoint,
                    "source": r.source,
                    "children": kids,
                }
        return None

    def supports(self, m: Model, lit: Literal, _seen: Optional[FrozenSet[Literal]] = None) -> List[FrozenSet[str]]:
        """All (bounded) minimal sets of rule ids + fact ids ('F:<literal>') that derive lit."""
        seen = _seen or frozenset()
        out: Set[FrozenSet[str]] = set()
        for d in m.derivations.get(lit, []):
            if d.rule_id is None:
                out.add(frozenset({f"F:{lit}"}))
                continue
            if any(p in seen for p in d.premises):
                continue
            prem_supports = [self.supports(m, p, seen | {lit}) for p in d.premises]
            if any(not ps for ps in prem_supports):
                continue
            for combo in itertools.islice(itertools.product(*prem_supports), self.MAX_SUPPORTS):
                s = frozenset({d.rule_id}).union(*combo)
                out.add(s)
                if len(out) >= self.MAX_SUPPORTS:
                    break
        return _minimize(out)

    def justifications(self, m: Model, p: Literal, q: Literal) -> List[FrozenSet[str]]:
        """Minimal conflict sets (MinAs) jointly deriving p and q (normally q = ¬p)."""
        sp, sq = self.supports(m, p), self.supports(m, q)
        return _minimize({a | b for a in sp for b in sq})

    # ---- querying across standpoints -------------------------------------
    def query(self, facts: Iterable[Literal], goal: Literal, standpoints: Optional[Iterable[str]] = None) -> dict:
        facts = tuple(facts)
        sps = list(standpoints) if standpoints else [s for s in self.kb.standpoints if s != UNIVERSAL]
        results = []
        for s in sps:
            m = self.model(s, facts)
            pos, neg = m.holds(goal), m.holds(goal.negate())
            conflicts = m.conflicts()
            if pos and neg:
                verdict = "CONFLICT"
            elif pos:
                verdict = "YES"
            elif neg:
                verdict = "NO"
            else:
                verdict = "UNKNOWN"
            entry = {
                "standpoint": s,
                "verdict": verdict,
                "necessarily": pos,            # □_s goal
                "possibly": not neg,           # ◇_s goal
                "consistent": not conflicts,
                "conflicts": [str(a) for a, _ in conflicts],
                "proof": self.proof_tree(m, goal) if pos else None,
                "counter_proof": self.proof_tree(m, goal.negate()) if neg else None,
            }
            results.append(entry)
        verdicts = {r["standpoint"]: r["verdict"] for r in results}
        decided = {v for v in verdicts.values() if v in ("YES", "NO")}
        return {
            "goal": str(goal),
            "results": results,
            "disagreement": len(decided) > 1,
            "summary": verdicts,
        }

    def check(self, facts: Iterable[Literal], standpoints: Optional[Iterable[str]] = None) -> List[dict]:
        """Consistency report for every standpoint, with minimal conflict sets."""
        facts = tuple(facts)
        sps = list(standpoints) if standpoints else [s for s in self.kb.standpoints if s != UNIVERSAL]
        report = []
        for s in sps:
            m = self.model(s, facts)
            items = []
            for p, q in m.conflicts():
                js = self.justifications(m, p, q)
                items.append({
                    "literal": str(p),
                    "justifications": [sorted(j) for j in js],
                    "rules_involved": sorted({x for j in js for x in j if not x.startswith("F:")}),
                    "proof_pos": self.proof_tree(m, p),
                    "proof_neg": self.proof_tree(m, q),
                })
            report.append({"standpoint": s, "consistent": not items, "conflicts": items,
                           "derived": sorted(str(l) for l in m.derived if l not in m.facts)})
        return report


def _show_theta(c: Comparison, theta: Dict[str, Term]) -> str:
    l = theta.get(c.left, c.left) if is_var(c.left) else c.left
    r = theta.get(c.right, c.right) if is_var(c.right) else c.right
    return f"{l} {c.op} {r}"


def stratify(rules: List[Rule]) -> List[List[Rule]]:
    """Split rules into strata so that every `not L` refers only to lower strata.

    Nodes are signed predicates (p and ¬p are different nodes). Raises ValueError
    if the program has recursion through negation-as-failure."""
    heads = {key(r.head) for r in rules}
    stratum: Dict[Tuple[str, bool], int] = {h: 0 for h in heads}
    n = len(heads) + 1
    for _ in range(n + 1):
        changed = False
        for r in rules:
            h = key(r.head)
            need = stratum[h]
            for b in r.body:
                if isinstance(b, Literal) and key(b) in stratum:
                    need = max(need, stratum[key(b)])
                elif isinstance(b, Naf) and key(b.lit) in stratum:
                    need = max(need, stratum[key(b.lit)] + 1)
            if need > stratum[h]:
                if need > n:
                    raise ValueError("program is not stratified (recursion through `not`)")
                stratum[h] = need
                changed = True
        if not changed:
            break
    else:
        raise ValueError("program is not stratified (recursion through `not`)")
    levels: Dict[int, List[Rule]] = {}
    for r in rules:
        levels.setdefault(stratum[key(r.head)], []).append(r)
    return [levels[k] for k in sorted(levels)]


def _minimize(sets: Iterable[FrozenSet[str]]) -> List[FrozenSet[str]]:
    sets = list(set(sets))
    out = [s for s in sets if not any(o < s for o in sets)]
    return sorted(out, key=lambda s: (len(s), sorted(s)))
