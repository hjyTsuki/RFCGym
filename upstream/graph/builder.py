"""Load seed_graph.yaml -> a networkx DiGraph with typed edges."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

try:
    import networkx as nx
except ImportError as e:
    raise ImportError(
        "upstream requires networkx; install with `pip install networkx pyyaml`"
    ) from e

from .schema import EdgeType, Node, Edge


DEFAULT_SEED = Path(__file__).resolve().parent.parent / "data_sources" / "seed_graph.yaml"


def load_seed(path: Path = DEFAULT_SEED) -> Tuple[Dict[str, Node], List[Edge]]:
    """Parse YAML into typed Node/Edge lists. Validates references."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    nodes: Dict[str, Node] = {}
    for entry in raw["nodes"]:
        n = Node(
            id=entry["id"],
            family=entry["family"],
            version=str(entry.get("version", "any")),
            rfc=list(entry.get("rfc", [])),
            layer=entry.get("layer", "app"),
            wire=entry.get("wire", "binary"),
            canonical=bool(entry.get("canonical", False)),
            note=entry.get("note"),
        )
        if n.id in nodes:
            raise ValueError(f"Duplicate node id: {n.id}")
        nodes[n.id] = n

    # Validate: each family must have exactly one canonical node
    families: Dict[str, list] = {}
    for n in nodes.values():
        families.setdefault(n.family, []).append(n)
    for fam, members in families.items():
        n_canonical = sum(1 for m in members if m.canonical)
        if n_canonical == 0:
            raise ValueError(
                f"Family '{fam}' has no canonical node; mark exactly one node "
                f"with `canonical: true`."
            )
        if n_canonical > 1:
            cans = [m.id for m in members if m.canonical]
            raise ValueError(
                f"Family '{fam}' has multiple canonical nodes: {cans}. "
                f"Exactly one must be canonical."
            )

    edges: List[Edge] = []
    for entry in raw["edges"]:
        src, dst = entry["from"], entry["to"]
        if src not in nodes:
            raise ValueError(f"Edge references unknown node: {src}")
        if dst not in nodes:
            raise ValueError(f"Edge references unknown node: {dst}")
        edges.append(Edge(
            src=src,
            dst=dst,
            type=EdgeType(entry["type"]),
            note=entry.get("note"),
        ))

    return nodes, edges


def build_graph(path: Path = DEFAULT_SEED) -> nx.MultiDiGraph:
    """Build a networkx MultiDiGraph. Multi because two protocols may have
    multiple distinct relations (e.g. http_2 -> http_1_1 is both version_of
    AND translatable)."""
    nodes, edges = load_seed(path)
    g = nx.MultiDiGraph()
    for nid, node in nodes.items():
        g.add_node(nid, **{
            "family": node.family,
            "version": node.version,
            "rfc": node.rfc,
            "layer": node.layer,
            "wire": node.wire,
            "canonical": node.canonical,
            "note": node.note,
        })
    for e in edges:
        g.add_edge(e.src, e.dst, key=e.type.value, type=e.type, note=e.note)
    return g


def node_obj(g: nx.MultiDiGraph, nid: str) -> Node:
    """Reconstruct a Node from the graph attribute dict (read-only)."""
    a = g.nodes[nid]
    return Node(
        id=nid, family=a["family"], version=a["version"],
        rfc=a["rfc"], layer=a["layer"], wire=a["wire"],
        canonical=a.get("canonical", False), note=a.get("note"),
    )


def canonical_of(g: nx.MultiDiGraph, nid: str) -> str:
    """Return the canonical node id of the same family as `nid`."""
    fam = g.nodes[nid]["family"]
    for n in g.nodes():
        if g.nodes[n]["family"] == fam and g.nodes[n].get("canonical", False):
            return n
    raise ValueError(f"No canonical node found for family '{fam}'")


def is_canonical(g: nx.MultiDiGraph, nid: str) -> bool:
    return bool(g.nodes[nid].get("canonical", False))


def stats(g: nx.MultiDiGraph) -> dict:
    """Quick stats for the CLI."""
    by_type: Dict[str, int] = {}
    for _, _, k, _ in g.edges(keys=True, data=True):
        by_type[k] = by_type.get(k, 0) + 1
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "by_edge_type": by_type,
    }
