"""Measure the Gemini planner on the 50 test instructions (NFR-10: at least 90% right, NFR-02: 95% within 10 s).

Needs a real key, so it is a script and not part of pytest:
    GEMINI_API_KEY=... python scripts/measure_ai.py            (Windows PowerShell: $env:GEMINI_API_KEY="...")
Optional: GEMINI_MODEL, and --pause SECONDS between calls (the free tier allows only a few requests per minute).
Writes the result to docs/ai-measurement.md.
"""
import argparse
import datetime
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_agent"))
sys.path.insert(0, str(ROOT / "tests"))

from agent_gemini import GeminiPlanner   # noqa: E402
from ai_instructions import CASES        # noqa: E402


def as_strings(args):
    """The test list writes numbers as text ("3"); the model gives 3. Compare them the same way."""
    return {k: str(v).lower() for k, v in (args or {}).items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause", type=float, default=6.0, help="seconds to wait between calls (free tier limit)")
    options = parser.parse_args()
    planner = GeminiPlanner.from_env()

    rows, times = [], []
    for number, (text, tool, args) in enumerate(CASES, 1):
        start = time.perf_counter()
        plan = planner.plan(text)
        seconds = time.perf_counter() - start
        times.append(seconds)
        got_tool, got_args = (plan.tool, plan.args) if plan.kind == "tool" else (None, {})
        right = got_tool == tool and (tool is None or as_strings(got_args) == as_strings(args))
        rows.append((text, tool, got_tool, right, seconds))
        print(f"{number:2}. {'ok ' if right else 'BAD'} {seconds:5.2f}s  {text}  ->  {got_tool} {got_args}")
        time.sleep(options.pause)

    accuracy = sum(r[3] for r in rows) / len(rows)
    p95 = statistics.quantiles(times, n=20)[-1]
    within_10 = sum(t <= 10 for t in times) / len(times)
    lines = [
        f"# AI assistant measurement ({datetime.date.today().isoformat()}, model {planner.model})", "",
        "Made with `python scripts/measure_ai.py` on the 50 instructions in `tests/ai_instructions.py`.", "",
        "| Requirement | Target | Result | Met |", "|---|---|---|---|",
        f"| NFR-10 accuracy | at least 90% | {accuracy:.0%} | {'yes' if accuracy >= 0.9 else 'no'} |",
        f"| NFR-02 answer time | 95% within 10 s | {within_10:.0%} within 10 s (95th percentile {p95:.2f} s) "
        f"| {'yes' if within_10 >= 0.95 else 'no'} |", "",
        "Note: the time is only the language model, measured from this computer. The instructions were written by "
        "the team; NFR-10 is more convincing with instructions written by others.", "",
        "| Instruction | Expected tool | Model chose | Right | Seconds |", "|---|---|---|---|---|",
    ] + [f"| {t} | {e or '(text answer)'} | {g or '(text answer)'} | {'yes' if r else 'no'} | {s:.2f} |"
         for t, e, g, r, s in rows]
    (ROOT / "docs" / "ai-measurement.md").write_text("\n".join(lines) + "\n")
    print(f"\nAccuracy {accuracy:.0%}, {within_10:.0%} within 10 s. Written to docs/ai-measurement.md")


if __name__ == "__main__":
    main()
