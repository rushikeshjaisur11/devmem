"""Decide what a chat message is: a question about the repos ("ask") or a new requirement/plan for another agent ("implement").
Deterministic and offline: no LLM call, so it is instant, free and explainable (the reason is shown to the user)."""
from __future__ import annotations

import re

STEPS = re.compile(r"^#{2,4}\s+(?:step\s*)?\d+|^\s*\d+[.)]\s+\S|^\s*[-*]\s+\[[ xX]\]\s+\S", re.M | re.I)
PLAN_HINT = re.compile(r"^\s*(?:docs?|references?|constraints?)\s*:", re.I | re.M)
IMPL_START = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:we\s+(?:need|want|have|should)\s+to|i\s+(?:need|want)\s+to|i'?d like to|need\s+to|want\s+to|let'?s|"
    r"implement|add|build|create|write|develop|integrate|migrate|refactor|support|introduce|enable|set\s*up|make|convert|replace|upgrade|"
    r"extend|expose|automate|fix|change|update|remove|rename)\b", re.I)
IMPL_WORDS = re.compile(r"\b(requirement|new feature|new endpoint|should be able to|must support|acceptance criteria|user story|as a user|"
                        r"implement(?:ing|ation)?|integrat(?:e|ion))\b", re.I)
HOW_TO_BUILD = re.compile(r"^\s*how (?:do|can|should|would) (?:i|we) (?:add|build|implement|create|integrate|handle|support|migrate|write|set up)\b", re.I)
ASK_START = re.compile(
    r"^\s*(?:who|what|whats|what's|where|which|when|why|is there|are there|do we|does|did|show me|find|list|explain|tell me|"
    r"has anyone|any existing|how (?:does|do we|is|are|many|come|did)|can you (?:explain|show|find|tell|list))\b", re.I)


def classify(text: str) -> dict:
    t = text.strip()
    if STEPS.search(t) and (t.count("\n") >= 2 or PLAN_HINT.search(t)):
        return {"intent": "implement", "reason": "it looks like a plan (numbered steps / step headings)"}
    if PLAN_HINT.search(t):
        return {"intent": "implement", "reason": "it has Docs:/Constraints: lines, so it is a plan"}
    impl = ask = 0
    why_i, why_a = [], []
    if HOW_TO_BUILD.search(t):
        impl += 4; why_i.append("asks how to build something")
    elif IMPL_START.search(t):
        impl += 3; why_i.append("it starts like a requirement")
    if IMPL_WORDS.search(t):
        impl += 2; why_i.append("it uses requirement wording")
    if len(t) > 240 or t.count("\n") >= 2:
        impl += 2; why_i.append("it is long enough to be a requirement")
    if ASK_START.search(t) and not HOW_TO_BUILD.search(t):
        ask += 3; why_a.append("it is phrased as a question")
    if t.endswith("?"):
        ask += 2; why_a.append("it ends with a question mark")
    if len(t) < 80:
        ask += 1
    if impl > ask:
        return {"intent": "implement", "reason": "; ".join(why_i)}
    return {"intent": "ask", "reason": "; ".join(why_a) or "no sign of a new requirement, so it is treated as a question"}


def pick_target(text: str, scope: list[str], indexed: list[str]) -> list[str]:
    """The repo the agent will work in. One name => decided; several => the user must choose."""
    if len(scope) == 1:
        return scope
    pool = scope or indexed
    if len(pool) == 1:
        return pool
    named = [n for n in pool if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text, re.I)]
    return named if len(named) == 1 else pool
