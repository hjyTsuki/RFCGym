"""LLM prompt builder for the scenario synthesizer.

Input  : a Walk (visited nodes + traversed edges + candidate bug_layer)
Output : a prompt string that asks the LLM to:
           (1) accept or reject the combination as a real attack scenario
           (2) confirm or override the bug_layer
           (3) cite at least one paper / CVE / advisory if accept
           (4) draft an SCN-*.md skeleton

The LLM response is expected to be parsable JSON with a strict schema.
"""
from __future__ import annotations

from typing import Iterable

from ..graph.schema import Node
from ..graph.builder import node_obj
from ..sampler.random_walk import Walk

import networkx as nx


SYSTEM_PROMPT = """You are the RFCGym Scenario Synthesizer.

Your job: given a small subgraph of network protocols, decide whether their
combination has a plausible protocol-dependent attack scenario worth building
into a test environment.

RFCGym scope (protocol-dependent bugs only):
  L1 = spec-level design flaw (single protocol RFC ambiguous/unsafe)
  L2 = cross-version translation within one protocol family
  L3 = cross-vendor variance within one protocol+version
  L4 = cross-protocol composition mismatch (>= 2 distinct protocols share an
       object they interpret differently)

OUT OF SCOPE: single-implementation bugs (memory corruption, OOB, races).
Reject combinations whose only attack surface is implementation quality.

Decide based on:
  - whether peer-reviewed prior work exists (paper / CVE / advisory)
  - whether the combination has a concrete shared-object disagreement
  - whether at least one wire-level oracle is constructible

Respond with strict JSON only - no commentary, no markdown fences.
"""


JSON_SCHEMA = """
{
  "decision": "accept" | "reject" | "needs_review",
  "bug_layer": "L1" | "L2" | "L3" | "L4",
  "scenario_slug": "SHORT-KEBAB-CASE",
  "title": "One-line scenario title",
  "summary": "2-3 sentence description of the attack mechanism",
  "shared_object": "The protocol object that gets misinterpreted (e.g. 'Range header', 'sender identity')",
  "components": [
    {"id": "<node_id>", "role": "ingress | translator | origin | authenticator | display | ..."}
  ],
  "references": [
    {"kind": "paper | cve | advisory | rfc", "id": "...", "url": "..." (optional)}
  ],
  "reject_reason": "if decision == 'reject' or 'needs_review', explain in one sentence",
  "notes_to_analyzer": "Free-form hints for the downstream Protocol Analyzer agent"
}
"""


def _describe_node(g: nx.MultiDiGraph, nid: str) -> str:
    n = node_obj(g, nid)
    rfc_str = f"RFC {','.join(str(x) for x in n.rfc)}" if n.rfc else "no RFC"
    note = f" ({n.note})" if n.note else ""
    return f"- `{n.id}` = {n.family}/{n.version} ({rfc_str}, layer={n.layer}, wire={n.wire}){note}"


def build_user_prompt(walk: Walk, graph: nx.MultiDiGraph) -> str:
    """Render a single Walk into the user prompt."""
    lines = [
        f"## Subgraph (candidate bug_layer: {walk.candidate_bug_layer})",
        "",
        "### Protocols involved",
    ]
    for nid in walk.nodes:
        lines.append(_describe_node(graph, nid))

    if walk.edges:
        lines.extend(["", "### Structural relations"])
        for e in walk.edges:
            note = f" ({e.note})" if e.note else ""
            lines.append(f"- `{e.src}` --[{e.type.value}]--> `{e.dst}`{note}")
    else:
        lines.extend(["", "### Structural relations", "(none; single-node walk)"])

    lines.extend([
        "",
        f"### Walker's heuristic rationale",
        walk.rationale or "(no rationale)",
        "",
        "## Your task",
        "",
        "1. Decide accept / reject / needs_review for this protocol combination as an RFCGym scenario.",
        "2. Confirm or override the bug_layer (L1/L2/L3/L4).",
        "3. If accept: cite at least one paper/CVE/advisory establishing this is a real attack surface.",
        "4. Propose a scenario_slug (kebab-case, <=40 chars) and a one-line title.",
        "5. Identify the shared object that gets misinterpreted across components.",
        "",
        "Respond with JSON conforming to this schema:",
        "",
        JSON_SCHEMA,
    ])
    return "\n".join(lines)


def build_messages(walk: Walk, graph: nx.MultiDiGraph) -> list:
    """Return Claude/OpenAI-compatible messages list."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(walk, graph)},
    ]
