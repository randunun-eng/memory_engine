"""Build frozen relevance labels for P1 #4 eval baseline.

Run-once script. For each query in eval_queries.yaml, asks
gemini-2.5-flash to identify which neurons from eval_neurons.yaml are
relevant. Writes eval_relevance.yaml as the ground-truth set.

Usage:
    uv run python tests/eval/build_eval_relevance.py

Requires GEMINI_API_KEY in env. Costs ~20 Gemini Flash calls
(~$0.01 total on current free-tier pricing).

The labels are checked into git alongside the fixtures so the baseline
is reproducible without re-calling the LLM on every CI run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from sentence_transformers import SentenceTransformer  # noqa: F401 — prove deps OK

from memory_engine.policy.backends.google_ai_studio import GoogleAIStudioBackend

FIXTURES = Path(__file__).parent.parent / "fixtures"
NEURONS_PATH = FIXTURES / "eval_neurons.yaml"
QUERIES_PATH = FIXTURES / "eval_queries.yaml"
RELEVANCE_PATH = FIXTURES / "eval_relevance.yaml"


PROMPT_TEMPLATE = """You are labeling relevance for a retrieval evaluation.

QUERY: "{query}"
LENS: {lens}

Consider every NEURON below. For each, decide whether a helpful response to the
QUERY would benefit from citing that neuron as context. Be strict: a neuron is
relevant only if it materially answers or informs the query. Tangentially-related
neurons are NOT relevant.

Return JSON only, no prose, no markdown fences. Shape:
{{
  "relevant_ids": ["<neuron id>", ...]
}}

Neurons:
{neuron_block}
"""


def _format_neurons(neurons: list[dict]) -> str:
    lines = []
    for n in neurons:
        kind = n.get("kind") or ("counterparty_fact" if n.get("counterparty_id") else "domain_fact")
        cp = n.get("counterparty_id") or "-"
        lines.append(f"- [{n['id']}] (kind={kind} cp={cp}) {n['content']}")
    return "\n".join(lines)


async def _label_one_query(
    backend: GoogleAIStudioBackend,
    query: dict,
    neurons: list[dict],
) -> list[str]:
    prompt = PROMPT_TEMPLATE.format(
        query=query["query"],
        lens=query["lens"],
        neuron_block=_format_neurons(neurons),
    )
    raw = await backend(model="gemini-2.5-flash", prompt=prompt, temperature=0.0)
    # Reuse the dispatch parser's tolerance — strip code fences + thought blocks
    text = raw.strip()
    for tag in ("thought", "thinking", "think"):
        open_t, close_t = f"<{tag}>", f"</{tag}>"
        while open_t in text.lower():
            s = text.lower().find(open_t)
            e = text.lower().find(close_t, s)
            if e == -1:
                text = text[:s].strip()
                break
            text = (text[:s] + text[e + len(close_t) :]).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text and not text.startswith("{"):
        first, last = text.find("{"), text.rfind("}")
        if first != -1 and last > first:
            text = text[first : last + 1]
    parsed = json.loads(text)
    ids = parsed.get("relevant_ids") or []
    if not isinstance(ids, list):
        raise ValueError(f"Expected list for relevant_ids, got {type(ids).__name__}")
    valid_ids = {n["id"] for n in neurons}
    return [i for i in ids if i in valid_ids]


async def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    neurons = yaml.safe_load(NEURONS_PATH.read_text(encoding="utf-8"))
    queries = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))

    backend = GoogleAIStudioBackend(api_key=api_key, max_rpm=10, warn_rpm=8)
    try:
        labels: list[dict] = []
        for q in queries:
            # Adversarial queries have known-empty ground truth by construction.
            # Skip the LLM call so we don't spend quota AND so the labeler
            # doesn't invent matches.
            if q.get("adversarial"):
                print(f"skipping {q['id']} (adversarial → relevant_ids=[])", flush=True)
                labels.append(
                    {
                        "query_id": q["id"],
                        "query": q["query"],
                        "lens": q["lens"],
                        "relevant_ids": [],
                    }
                )
                continue
            print(f"labeling {q['id']}: {q['query']!r} ...", flush=True)
            relevant = await _label_one_query(backend, q, neurons)
            print(f"  → {len(relevant)} relevant: {relevant}")
            labels.append(
                {
                    "query_id": q["id"],
                    "query": q["query"],
                    "lens": q["lens"],
                    "relevant_ids": relevant,
                }
            )
    finally:
        await backend.aclose()

    RELEVANCE_PATH.write_text(
        "# P1 #4 eval relevance labels — generated by build_eval_relevance.py\n"
        "# Model: gemini-2.5-flash. DO NOT edit by hand; re-run the script.\n"
        "#\n" + yaml.safe_dump(labels, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"\nWrote {len(labels)} query labels to {RELEVANCE_PATH}")
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
