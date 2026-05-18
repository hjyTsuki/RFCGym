"""Random-walk sampler over the protocol graph.

Strategy (mirrors the README sketch):
  1. Pick a total node count n ∈ {1, 2, 3} uniformly (configurable weights).
  2. Pick a seed node uniformly.
  3. If n > 1, do n-1 random walks; the chosen edge type biases the inferred
     bug_layer (L2 via version_of/translatable, L4 via composes_with/embeds).
  4. Emit a Walk record with the visited nodes + traversed edges + inferred
     candidate bug_layer.

The synthesizer LLM later confirms or overrides bug_layer and checks if a
known paper / CVE exists for the combination.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import networkx as nx

from ..graph.schema import EdgeType, EDGES_FOR_L2, EDGES_FOR_L4


@dataclass
class TraversedEdge:
    src: str
    dst: str
    type: EdgeType
    note: Optional[str] = None


@dataclass
class Walk:
    """A single random-walk extraction. Fed to the scenario synthesizer."""
    nodes: List[str] = field(default_factory=list)
    edges: List[TraversedEdge] = field(default_factory=list)
    candidate_bug_layer: Optional[str] = None   # 'L1' | 'L2' | 'L3' | 'L4'
    rationale: str = ""

    @property
    def signature(self) -> str:
        """Stable id for deduplication: sorted node ids joined."""
        return "-".join(sorted(self.nodes))


# Default weights for n; tune to taste.
DEFAULT_SIZE_WEIGHTS = {1: 0.25, 2: 0.55, 3: 0.20}


class RandomWalker:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        seed: Optional[int] = None,
        size_weights: Optional[dict] = None,
    ):
        self.g = graph
        self.rng = random.Random(seed)
        self.size_weights = size_weights or DEFAULT_SIZE_WEIGHTS
        self._all_nodes = list(self.g.nodes())

    # ---------- public API ----------

    def walk(self, max_attempts: int = 20) -> Optional[Walk]:
        """Produce one Walk. Returns None if the seed had no outgoing edges
        and we needed n>1 (after max_attempts retries)."""
        n = self._pick_n()
        for _ in range(max_attempts):
            w = self._try_walk(n)
            if w is not None:
                return w
        return None

    def sample(self, count: int, dedup: bool = True) -> List[Walk]:
        """Sample `count` distinct walks (by node-set signature when dedup)."""
        seen = set()
        out: List[Walk] = []
        attempts = 0
        while len(out) < count and attempts < count * 10:
            attempts += 1
            w = self.walk()
            if w is None:
                continue
            if dedup and w.signature in seen:
                continue
            seen.add(w.signature)
            out.append(w)
        return out

    # ---------- internals ----------

    def _pick_n(self) -> int:
        items, weights = zip(*self.size_weights.items())
        return self.rng.choices(items, weights=weights, k=1)[0]

    def _try_walk(self, n: int) -> Optional[Walk]:
        seed = self.rng.choice(self._all_nodes)
        nodes = [seed]
        edges: List[TraversedEdge] = []
        current = seed
        version_terminated = False

        for i in range(n - 1):
            # CONSTRAINT (v2): once we have traversed any version_of edge,
            # the walk must terminate. A cross-version scenario is atomic -
            # one version pair, no chaining to other versions or other
            # protocol families.
            if edges and edges[0].type == EdgeType.VERSION_OF:
                version_terminated = True
                break

            # Outgoing edges from current node (undirected feel: also try
            # incoming so we can walk against composes_with arrows).
            out_edges = self._neighbors_with_edges(current)
            # Avoid revisiting nodes already in walk.
            out_edges = [
                (nbr, etype, note)
                for (nbr, etype, note) in out_edges
                if nbr not in nodes
            ]

            # CONSTRAINT (v2 part 2): after the first edge, version_of must
            # NOT appear. Otherwise a walk like composes_with -> version_of
            # would mix L4 + L2 semantics in a single scenario. version_of
            # is therefore only ever the FIRST and ONLY edge of a walk.
            if i >= 1:
                out_edges = [
                    (nbr, etype, note)
                    for (nbr, etype, note) in out_edges
                    if etype != EdgeType.VERSION_OF
                ]

            if not out_edges:
                return None  # dead-end; caller retries
            nbr, etype, note = self.rng.choice(out_edges)
            edges.append(TraversedEdge(src=current, dst=nbr, type=etype, note=note))
            nodes.append(nbr)
            current = nbr

        layer, rationale = self._infer_bug_layer(
            len(nodes), edges, version_terminated=version_terminated
        )
        return Walk(
            nodes=nodes,
            edges=edges,
            candidate_bug_layer=layer,
            rationale=rationale,
        )

    def _neighbors_with_edges(self, nid: str) -> List[Tuple[str, EdgeType, Optional[str]]]:
        """All neighbors reachable by any typed edge (out or in direction)."""
        items: List[Tuple[str, EdgeType, Optional[str]]] = []
        # Outgoing
        for _, dst, k, data in self.g.out_edges(nid, keys=True, data=True):
            items.append((dst, EdgeType(k), data.get("note")))
        # Incoming (so e.g. "imf composes_with spf" is also walkable from spf)
        for src, _, k, data in self.g.in_edges(nid, keys=True, data=True):
            items.append((src, EdgeType(k), data.get("note")))
        return items

    @staticmethod
    def _infer_bug_layer(
        n: int,
        edges: List[TraversedEdge],
        version_terminated: bool = False,
    ) -> Tuple[str, str]:
        """Inference rules:
          n=1                            -> L1 candidate (spec-level review)
          edges has version_of (any)     -> L2 (cross-version translation)
                                            note if walker truncated due to constraint
          edges in EDGES_FOR_L4 only     -> L4 (cross-protocol composition)
          mixed                          -> L4 default
        """
        if n == 1:
            return ("L1", "single-node walk: candidate for spec-level review")

        types = {e.type for e in edges}

        # version_of dominates: if present, the walk is an L2 candidate
        if EdgeType.VERSION_OF in types:
            suffix = " (walk truncated by version_of constraint)" if version_terminated else ""
            return (
                "L2",
                f"first edge is version_of -> cross-version translation{suffix}",
            )

        if types & set(EDGES_FOR_L4):
            l4_types = [t.value for t in types & set(EDGES_FOR_L4)]
            return ("L4", f"contains composing edge {l4_types} -> cross-protocol")

        return ("L4", f"mixed edges {[t.value for t in types]} -> default to cross-protocol")
