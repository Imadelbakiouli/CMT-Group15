"""Planner that uses Google Gemini to understand free text (T13, UR-12, FR-16).

It does exactly the same job as RuleBasedPlanner: one sentence of the user in, at most one tool call out.
Only the planning is done by the language model. Everything after it stays the same in app.py:
the arguments are checked by validate(), important actions still wait for a "yes" (FR-17),
and the CMT service still checks every right with the user's own token (SEC-07).

Security (SEC-08, prompt injection):
- Gemini gets ONLY the sentence the user typed, never titles, abstracts, reviews or other answers of the system.
  So text an attacker writes in a paper can never reach the model as an instruction.
- The model can only pick a tool from our fixed list. An unknown tool name or more than one call is ignored.
- If Gemini is not reachable or answers something odd, nothing is done.

Privacy: the user's sentence is sent to Google. The free tier of the Gemini API may use that data to improve
Google's products, so do not use the free tier with real conference data (see docs/ai-assistant.md).
"""
import os

import requests

from agent_planner import HELP_TEXT, NOT_UNDERSTOOD, Plan, normalize
from agent_tools import TOOLS

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-flash-latest"     # change with GEMINI_MODEL in .env
TIMEOUT = 8                               # seconds; NFR-02 wants an answer within 10 seconds
NOT_AVAILABLE = "The assistant cannot reach the language model right now, so I did nothing. Please try again later."

INSTRUCTIONS = (
    "You are the assistant of a conference management tool. The user writes one request. "
    "If one of the functions does what the user asks, call exactly that one function with the numbers and "
    "email address the user gave. Never guess numbers or emails that the user did not give. "
    "If the request is unclear, needs more than one action, or no function fits, do not call a function: "
    "answer in one or two short sentences and ask the user to say what they want, one thing at a time."
)

# How our kinds of arguments look for Gemini
KIND_SCHEMA = {
    "int": {"type": "integer", "description": "a whole number above zero"},
    "email": {"type": "string", "description": "an email address"},
    "outcome": {"type": "string", "enum": ["accept", "reject"]},
}


def function_declarations():
    """Our tool list (agent_tools.TOOLS) in the format Gemini expects."""
    declarations = []
    for tool in TOOLS.values():
        declaration = {"name": tool.name, "description": tool.description}
        if tool.params:
            declaration["parameters"] = {
                "type": "object",
                "properties": {name: dict(KIND_SCHEMA[kind]) for name, kind in tool.params},
                "required": [name for name, _ in tool.params],
            }
        declarations.append(declaration)
    return declarations


def whole_numbers(args):
    """Gemini sometimes sends 4.0 instead of 4. Turn those into whole numbers; validate() checks the rest."""
    return {k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in args.items()}


class GeminiPlanner:
    def __init__(self, api_key, model=DEFAULT_MODEL, post=requests.post, timeout=TIMEOUT):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty")
        self.api_key, self.model, self.post, self.timeout = api_key, model, post, timeout

    @classmethod
    def from_env(cls):
        """Settings come from the environment (.env), never from the code repository."""
        return cls(os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL)

    def plan(self, message):
        if normalize(message) in ("help", "what can you do"):
            return Plan("reply", text=HELP_TEXT)
        body = {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": message}]}],    # only the user's own sentence
            "tools": [{"functionDeclarations": function_declarations()}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        }
        try:
            answer = self.post(API_URL.format(model=self.model), json=body, timeout=self.timeout,
                               headers={"x-goog-api-key": self.api_key})     # key in a header, not in the URL
            if answer.status_code != 200:
                return Plan("reply", text=NOT_AVAILABLE)
            parts = answer.json()["candidates"][0]["content"]["parts"]
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            return Plan("reply", text=NOT_AVAILABLE)
        return plan_from_parts(parts)


def plan_from_parts(parts):
    """Turn Gemini's answer into a Plan. Only one known tool is accepted; anything else does nothing."""
    calls = [p["functionCall"] for p in parts if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)]
    if len(calls) > 1:
        return Plan("reply", text="Please ask for one thing at a time, so I did nothing.")
    if calls:
        name, args = calls[0].get("name"), calls[0].get("args") or {}
        if name not in TOOLS or not isinstance(args, dict):
            return Plan("reply", text=NOT_UNDERSTOOD)
        return Plan("tool", tool=name, args=whole_numbers(args))
    text = " ".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()
    return Plan("reply", text=text[:1000] or NOT_UNDERSTOOD)
