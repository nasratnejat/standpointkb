"""
Explanations.

1. Template verbaliser (always available, deterministic): turns a proof tree produced
   by the reasoner into plain-language sentences.
2. Optional LLM helpers (only if ANTHROPIC_API_KEY is set):
   - verbalise a proof for a lay reader
   - translate a natural-language regulation into a candidate rule
   Principle: *the LLM proposes, the logic disposes* — LLM output is never used for
   reasoning; a translated rule is parsed, safety-checked and shown to the user, who
   must accept it before it enters the knowledge base.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import List, Optional

from .engine import KnowledgeBase, parse_rule

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")


def humanise(lit: str) -> str:
    neg = lit.startswith("¬")
    core = lit.lstrip("¬")
    m = re.match(r"(\w+)\((.*)\)", core)
    if not m:
        return lit
    pred, args = m.group(1).replace("_", " "), m.group(2)
    return f"{'NOT ' if neg else ''}{pred} ({args})"


def verbalise(tree: Optional[dict], standpoint: str) -> List[str]:
    """Depth-first, bottom-up list of sentences."""
    if not tree:
        return []
    out: List[str] = []

    def rec(n: dict) -> None:
        for c in n.get("children", []):
            rec(c)
        k = n["kind"]
        if k == "fact":
            out.append(f"Given: {humanise(n['literal'])}.")
        elif k == "absent":
            out.append(f"There is no information that {humanise(n['literal'][4:])} (so the exception does not apply).")
        elif k == "check":
            out.append(f"Check passed: {n['literal']}.")
        elif k == "rule":
            src = f" ({n['source']})" if n.get("source") else ""
            out.append(f"Rule {n['rule_id']} of standpoint {n['standpoint']}{src} therefore yields "
                       f"{humanise(n['literal'])}.")

    rec(tree)
    out.append(f"⇒ According to {standpoint}: {humanise(tree['literal'])}.")
    return out


# ---------------------------------------------------------------------- #
# optional LLM
# ---------------------------------------------------------------------- #

def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _call(system: str, user: str, max_tokens: int = 800) -> str:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return "".join(b.get("text", "") for b in data.get("content", []))


def llm_explain(question: str, results: list) -> str:
    system = ("You explain the output of a logic reasoner to a non-expert living in Germany. "
              "Use ONLY the derivations given; do not add legal facts. Be brief (max 120 words). "
              "If standpoints disagree, say so plainly and name which authority says what. "
              "End with: 'This is a research demo, not legal advice.'")
    payload = [{"standpoint": r["standpoint"], "verdict": r["verdict"],
                "proof": r.get("proof"), "counter_proof": r.get("counter_proof")} for r in results]
    return _call(system, f"Question: {question}\nReasoner output:\n{json.dumps(payload, ensure_ascii=False)}")


def llm_nl2rule(text: str, kb: KnowledgeBase) -> dict:
    preds = set()
    for r in kb.rules.values():
        preds.add(re.sub(r"\(.*", "", str(r.head)).lstrip("¬"))
        for b in r.body:
            m = re.match(r"(?:not\s+)?¬?(\w+)\(", str(b))
            if m:
                preds.add(m.group(1))
    sps = [s for s in kb.standpoints if s != "*"]
    system = (
        "Translate a German or English regulation into ONE Datalog-style rule.\n"
        "Syntax: body1 ∧ body2 ∧ Var <= 123 → head(X). Variables are Capitalised. "
        "Use ¬ for strong negation in head or body, `not p(X)` for an exception (negation as failure). "
        f"Prefer these existing predicates where they fit: {sorted(preds)}. "
        f"Pick the standpoint from: {sps} (federal law → bund, Saxon law → sachsen, city practice → city).\n"
        'Answer with JSON only: {"rule": "...", "standpoint": "...", "source": "short citation or origin", '
        '"confidence": 0-1, "comment": "what was simplified"}'
    )
    raw = _call(system, text, 400)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"LLM did not return JSON: {raw[:200]}")
    prop = json.loads(m.group(0))
    parse_rule(prop["rule"])  # validate syntax + safety; raises on failure
    if prop.get("standpoint") not in kb.standpoints:
        prop["standpoint"] = "bund"
    return prop
