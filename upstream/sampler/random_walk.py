"""Two-stage random-walk sampler over the protocol graph (v3).

Stage 1 — Pick a seed node uniformly at random from ALL nodes.

Stage 2 — Sample a single first-edge type from this seed. The choice
          determines the bug_layer and the walk's structure:

    L1_STOP   (synthetic "edge to None")
        Walk terminates at the seed. Treated as a single-protocol design
        review candidate (L1).

    L2 (version_of)
        Pick a same-family different-version neighbor. Walk is the atomic
        2-node version pair. The seed may be any version; the other end
        may be any other version of the same family. Walk does NOT extend.

    L4 (composes_with / embeds, both endpoints canonical)
        We do NOT mix non-canonical versions with cross-protocol edges.
        Implementation: if the seed is non-canonical, it is swapped to
        the canonical of its family for the walk record. The target must
        be a canonical node. Walk may extend one more cross-protocol hop
        (also canonical-canonical) with probability `extend_l4_prob`.
        Result: 2 or 3 canonical nodes.

This mirrors the user's instruction:
  "首先随机选择节点；然后开始随机游走，判断选择的边，
   如果是同协议不同版本，那么停止，做版本之间差异;
   如果是不同协议，那么选中协议的版本，使用最稳定常用的版本,
   不纠结不同版本不同协议的组合,这个可以选择1~3个节点;
   可以每个节点都构造指向None的边，就作为单个协议问题"
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from ..graph.schema import EdgeType
from ..graph.builder import canonical_of, is_canonical


@dataclass
class TraversedEdge:
    src: str
    dst: str
    type: EdgeType
    note: Optional[str] = None


@dataclass
class Walk:
    """A single sample from the walker. Fed to the scenario synthesizer."""
    nodes: List[str] = field(default_factory=list)
    edges: List[TraversedEdge] = field(default_factory=list)
    candidate_bug_layer: Optional[str] = None    # 'L1' | 'L2' | 'L4'
    rationale: str = ""
    seed_node: Optional[str] = None              # original seed before canonical swap
    seed_swapped: bool = False                   # True if L4 walk swapped seed -> canonical

    @property
    def signature(self) -> str:
        return "-".join(sorted(self.nodes))


# A synthetic marker representing the "stop here" option (edge to None).
L1_STOP = object()


class RandomWalker:
    def __init__(
        self,
        graph: nx.MultiDiGraph,
        seed: Optional[int] = None,
        layer_weights: Optional[Dict[str, float]] = None,
        extend_l4_prob: float = 0.5,
    ):
        self.g = graph
        self.rng = random.Random(seed)
        # Per-option layer weight applied when sampling among {L1, L2, L4}.
        # Default: each individual option contributes 1.0; tune to bias.
        self.layer_weights = layer_weights or {"L1": 1.0, "L2": 1.0, "L4": 1.0}
        self.extend_l4_prob = extend_l4_prob
        self._all_nodes = list(self.g.nodes())

    # ---------- public API ----------

    def walk(self) -> Walk:
        seed = self.rng.choice(self._all_nodes)
        return self._sample_from(seed)

    def sample(self, count: int, dedup: bool = True) -> List[Walk]:
        seen = set()
        out: List[Walk] = []
        attempts = 0
        while len(out) < count and attempts < count * 10:
            attempts += 1
            w = self.walk()
            if dedup and w.signature in seen:
                continue
            seen.add(w.signature)
            out.append(w)
        return out

    # ---------- core sampling ----------

    def _sample_from(self, seed: str) -> Walk:
        """Stage 2: from a fixed seed, sample one option and build the walk."""
        # Build the option pool: each option is (kind, weight, payload)
        options: List[Tuple[str, float, object]] = []

        # ---- L1_STOP: always available (synthetic edge to None) ----
        options.append(("L1", self.layer_weights["L1"], L1_STOP))

        # ---- L2: same-family version_of neighbors of the actual seed ----
        for nbr, etype, note in self._typed_neighbors(seed):
            if etype == EdgeType.VERSION_OF:
                options.append(("L2", self.layer_weights["L2"], (nbr, etype, note)))

        # ---- L4: cross-protocol neighbors of the canonical seed; target must be canonical ----
        try:
            canonical_seed = canonical_of(self.g, seed)
        except ValueError:
            canonical_seed = None

        if canonical_seed is not None:
            for nbr, etype, note in self._typed_neighbors(canonical_seed):
                if etype in (EdgeType.COMPOSES_WITH, EdgeType.EMBEDS):
                    if is_canonical(self.g, nbr) and nbr != canonical_seed:
                        options.append((
                            "L4",
                            self.layer_weights["L4"],
                            (nbr, etype, note, canonical_seed),
                        ))

        # Weighted random choice
        kinds = [o[0] for o in options]
        weights = [o[1] for o in options]
        chosen_idx = self.rng.choices(range(len(options)), weights=weights, k=1)[0]
        kind, _w, payload = options[chosen_idx]

        if kind == "L1":
            return Walk(
                nodes=[seed], edges=[],
                candidate_bug_layer="L1",
                rationale="L1: walker chose to stop at seed (single-protocol review)",
                seed_node=seed,
            )

        if kind == "L2":
            target, etype, note = payload  # type: ignore[misc]
            return Walk(
                nodes=[seed, target],
                edges=[TraversedEdge(src=seed, dst=target, type=etype, note=note)],
                candidate_bug_layer="L2",
                rationale=f"L2: cross-version pair via {etype.value}",
                seed_node=seed,
            )

        # kind == "L4"
        target, etype, note, used_seed = payload  # type: ignore[misc]
        nodes = [used_seed, target]
        edges = [TraversedEdge(src=used_seed, dst=target, type=etype, note=note)]
        seed_swapped = used_seed != seed

        # Optionally extend by one canonical-canonical cross-protocol hop
        if self.rng.random() < self.extend_l4_prob:
            current = target
            more = [
                (nbr, et, n2)
                for (nbr, et, n2) in self._typed_neighbors(current)
                if et in (EdgeType.COMPOSES_WITH, EdgeType.EMBEDS)
                and is_canonical(self.g, nbr)
                and nbr not in nodes
            ]
            if more:
                nbr, et, n2 = self.rng.choice(more)
                edges.append(TraversedEdge(src=current, dst=nbr, type=et, note=n2))
                nodes.append(nbr)

        rationale = "L4: cross-protocol composition (canonical versions only)"
        if seed_swapped:
            rationale += f" (seed {seed} swapped to canonical {used_seed})"

        return Walk(
            nodes=nodes, edges=edges,
            candidate_bug_layer="L4",
            rationale=rationale,
            seed_node=seed,
            seed_swapped=seed_swapped,
        )

    # ---------- helpers ----------

    def _typed_neighbors(self, nid: str) -> List[Tuple[str, EdgeType, Optional[str]]]:
        """Neighbors reachable in either direction with typed edge info."""
        items: List[Tuple[str, EdgeType, Optional[str]]] = []
        for _, dst, k, data in self.g.out_edges(nid, keys=True, data=True):
            items.append((dst, EdgeType(k), data.get("note")))
        for src, _, k, data in self.g.in_edges(nid, keys=True, data=True):
            items.append((src, EdgeType(k), data.get("note")))
        return items
