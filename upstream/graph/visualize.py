"""Visualize the protocol graph as a PNG.

Usage:
    python -m upstream.graph.visualize --layout spring --output seed_graph.png

Three layouts available: spring (organic), kamada_kawai (energy-minimized,
usually cleanest for small graphs), shell (concentric rings by family).
Edge type encoded by color + line style; node family by fill color.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError as e:
    raise ImportError(
        "Visualization requires matplotlib; install with `pip install matplotlib`"
    ) from e

import networkx as nx

from .builder import build_graph
from .schema import EdgeType


# Each edge type gets a distinct color + line style for easy reading
EDGE_STYLES: Dict[EdgeType, dict] = {
    EdgeType.VERSION_OF: dict(
        color="#1f77b4",      # blue
        style="solid",
        width=1.8,
        label="version_of (newer -> older)",
    ),
    EdgeType.COMPOSES_WITH: dict(
        color="#d62728",      # red
        style="dashed",
        width=1.5,
        label="composes_with (L4 trust composition)",
    ),
    EdgeType.EMBEDS: dict(
        color="#ff7f0e",      # orange
        style="dotted",
        width=1.5,
        label="embeds (carrier <- payload)",
    ),
    EdgeType.LAYERED_ON: dict(color="#7f7f7f", style="solid", width=0.8, label="layered_on"),
    EdgeType.TRANSLATABLE: dict(color="#2ca02c", style="dashdot", width=1.2, label="translatable"),
    EdgeType.DISPATCHES_TO: dict(color="#9467bd", style="dotted", width=1.0, label="dispatches_to"),
}


def family_colors(g: nx.MultiDiGraph) -> Dict[str, tuple]:
    families = sorted({g.nodes[n]["family"] for n in g.nodes()})
    cmap = plt.colormaps.get_cmap("tab20")
    if len(families) == 1:
        return {families[0]: cmap(0)}
    return {f: cmap(i / (len(families) - 1)) for i, f in enumerate(families)}


def _compute_layout(g: nx.MultiDiGraph, layout: str, seed: int):
    if layout == "spring":
        return nx.spring_layout(g, k=1.8, iterations=200, seed=seed)
    if layout == "kamada_kawai":
        # Simple graph view for kamada (it doesn't accept MultiDiGraph keys)
        simple = nx.DiGraph()
        simple.add_nodes_from(g.nodes(data=True))
        for u, v in g.edges():
            simple.add_edge(u, v)
        return nx.kamada_kawai_layout(simple)
    if layout == "shell":
        # Group by family - each family is one concentric ring
        families: Dict[str, list] = {}
        for n in g.nodes():
            families.setdefault(g.nodes[n]["family"], []).append(n)
        shells = sorted(families.values(), key=len, reverse=True)
        return nx.shell_layout(g, nlist=shells)
    if layout == "circular":
        return nx.circular_layout(g)
    raise ValueError(f"Unknown layout: {layout}")


def draw(
    layout: str = "kamada_kawai",
    output: Path = Path("seed_graph.png"),
    seed: int = 42,
    figsize: tuple = (18, 13),
    dpi: int = 160,
) -> Path:
    g = build_graph()
    pos = _compute_layout(g, layout, seed)

    fig, ax = plt.subplots(figsize=figsize)

    # Nodes grouped by family for legend
    fcolors = family_colors(g)
    for fam, color in fcolors.items():
        nodes_in_family = [n for n in g.nodes() if g.nodes[n]["family"] == fam]
        nx.draw_networkx_nodes(
            g, pos,
            nodelist=nodes_in_family,
            node_color=[color] * len(nodes_in_family),
            node_size=1200,
            edgecolors="black",
            linewidths=1.0,
            ax=ax,
        )

    # Labels: show "family/version" except where version=="any" (just family)
    labels = {}
    for n in g.nodes():
        v = g.nodes[n]["version"]
        f = g.nodes[n]["family"]
        labels[n] = f if v == "any" else f"{f}\n{v}"
    nx.draw_networkx_labels(g, pos, labels=labels, font_size=7, ax=ax)

    # Edges by type with distinct color/style
    for etype, style in EDGE_STYLES.items():
        elist = [(u, v) for u, v, k in g.edges(keys=True) if k == etype.value]
        if not elist:
            continue
        nx.draw_networkx_edges(
            g, pos,
            edgelist=elist,
            edge_color=style["color"],
            style=style["style"],
            width=style["width"],
            arrows=True,
            arrowsize=14,
            arrowstyle="-|>",
            connectionstyle="arc3,rad=0.08",
            min_source_margin=18,
            min_target_margin=18,
            ax=ax,
        )

    # Legends: edges (top-left), families (top-right)
    edge_handles = [
        mpatches.Patch(color=style["color"], label=style["label"])
        for etype, style in EDGE_STYLES.items()
        if any(k == etype.value for _, _, k in g.edges(keys=True))
    ]
    leg1 = ax.legend(handles=edge_handles, loc="upper left", fontsize=9,
                     title="Edge types", title_fontsize=10, framealpha=0.9)
    ax.add_artist(leg1)

    family_handles = [
        mpatches.Patch(color=c, label=fam) for fam, c in fcolors.items()
    ]
    ax.legend(handles=family_handles, loc="upper right", fontsize=8,
              title="Protocol families", title_fontsize=10, framealpha=0.9,
              ncol=2 if len(family_handles) > 8 else 1)

    ax.set_title(
        f"RFCGym Seed Protocol Graph — {g.number_of_nodes()} nodes, "
        f"{g.number_of_edges()} edges (application layer only)  ·  layout={layout}",
        fontsize=13,
    )
    ax.set_axis_off()
    plt.tight_layout()

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output


def main():
    p = argparse.ArgumentParser(description="Render seed_graph.yaml as PNG")
    p.add_argument("--layout", default="kamada_kawai",
                   choices=["spring", "kamada_kawai", "shell", "circular"])
    p.add_argument("--output", type=Path, default=Path("seed_graph.png"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--all", action="store_true",
                   help="Render all four layouts to seed_graph_{layout}.png")
    args = p.parse_args()

    if args.all:
        out_base = Path(args.output).with_suffix("")
        for layout in ["spring", "kamada_kawai", "shell", "circular"]:
            f = draw(layout=layout, output=Path(f"{out_base}_{layout}.png"), seed=args.seed)
            print(f"Wrote {f}")
    else:
        f = draw(layout=args.layout, output=args.output, seed=args.seed)
        print(f"Wrote {f}")


if __name__ == "__main__":
    main()
