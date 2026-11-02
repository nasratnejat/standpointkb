#!/usr/bin/env python3
"""
StandpointKB web server — standard library only.

    python3 server.py            # http://localhost:8000
    python3 server.py --port 9000

Optional: export ANTHROPIC_API_KEY=...  to enable the LLM helpers
(natural-language → rule, plain-language explanations).
"""

from __future__ import annotations

import argparse
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from standpointkb import plain, seed
from standpointkb.engine import Reasoner, parse_literal
from standpointkb.explain import llm_available, llm_explain, llm_nl2rule, verbalise

WEB = Path(__file__).parent / "web"
LOCK = threading.Lock()
STATE = {"rev": seed.build(), "labels": dict(seed.CASE_LABELS), "facts_text": dict(seed.CASES)}


def state_payload() -> dict:
    rev = STATE["rev"]
    return {
        "kb": rev.kb.to_dict(),
        "cases": [{"name": k, "facts": STATE["facts_text"].get(k, ""), "label": STATE["labels"].get(k, "")}
                  for k in rev.cases],
        "history": rev.history,
        "version": rev.version,
        "overview": rev.overview(),
        "llm": llm_available(),
    }


def handle(path: str, body: dict) -> dict:
    rev = STATE["rev"]
    if path == "/api/state":
        return state_payload()

    if path == "/api/case":
        name = body["name"].strip()
        facts = seed.parse_facts(body["facts"])
        for f in facts:
            if not f.ground():
                raise ValueError(f"facts must not contain variables: {f}")
        rev.cases[name] = facts
        STATE["facts_text"][name] = body["facts"]
        STATE["labels"].setdefault(name, body.get("label", "custom case"))
        return state_payload()

    if path == "/api/query":
        facts = rev.cases[body["case"]]
        goal = parse_literal(body["goal"])
        res = Reasoner(rev.kb).query(facts, goal, body.get("standpoints"))
        for r in res["results"]:
            if r["proof"] and r["counter_proof"]:
                expl = (verbalise(r["proof"], r["standpoint"])[:-1]
                        + verbalise(r["counter_proof"], r["standpoint"])[:-1]
                        + [f"⇒ {r['standpoint']} derives both {body['goal']} and its negation — "
                           f"this standpoint is inconsistent and needs revision."])
            else:
                expl = verbalise(r["proof"] or r["counter_proof"], r["standpoint"])
            r["explanation"] = expl or [
                f"Neither {body['goal']} nor its negation is derivable according to {r['standpoint']} "
                f"— both remain possible (◇)."]
        return res

    if path == "/api/check":
        return {"report": Reasoner(rev.kb).check(rev.cases[body["case"]])}

    if path == "/api/suggest":
        return rev.suggest(body["case"], body["standpoint"], body["literal"])

    if path == "/api/apply":
        entry = rev.apply(body["op"], body.get("reason", ""))
        return {"applied": entry, **state_payload()}

    if path == "/api/undo":
        last = rev.undo()
        return {"undone": last, **state_payload()}

    if path == "/api/reset":
        STATE["rev"] = seed.build()
        STATE["labels"] = dict(seed.CASE_LABELS)
        STATE["facts_text"] = dict(seed.CASES)
        return state_payload()

    if path == "/api/citizen":
        prof = body.get("profile", {})
        city = body.get("city", "dresden")
        if city not in rev.kb.standpoints:
            raise ValueError(f"unknown city {city!r}")
        # store the profile as case 'you' so the expert view can inspect the same facts
        facts = plain.profile_to_facts(prof)
        rev.cases[plain.ME] = facts
        STATE["facts_text"][plain.ME] = ", ".join(str(f) for f in facts)
        STATE["labels"][plain.ME] = "Profile from the citizen view"
        out = plain.checklist(rev.kb, prof, city)
        out["version"] = rev.version
        return out

    if path == "/api/llm/explain":
        return {"text": llm_explain(body["question"], body["results"])}

    if path == "/api/llm/nl2rule":
        return llm_nl2rule(body["text"], rev.kb)

    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, payload, ctype="application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._dispatch({})
        f = WEB / ("index.html" if self.path in ("/", "") else self.path.lstrip("/"))
        if f.resolve().parent != WEB.resolve() or not f.exists():
            return self._send(404, {"error": "not found"})
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}.get(f.suffix, "application/octet-stream")
        self._send(200, f.read_bytes(), ctype)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid JSON"})
        self._dispatch(body)

    def _dispatch(self, body: dict):
        try:
            with LOCK:
                out = handle(self.path.split("?")[0], body)
            self._send(200, out)
        except KeyError as e:
            self._send(404 if str(e).strip("'").startswith("/api") else 400, {"error": f"unknown: {e}"})
        except (ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"StandpointKB running on http://{a.host}:{a.port}  (LLM helpers: {'on' if llm_available() else 'off'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
