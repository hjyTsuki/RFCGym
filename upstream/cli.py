"""RFCGym upstream CLI.

Usage:
    python -m upstream.cli stats
    python -m upstream.cli walk --n 5 --seed 42
    python -m upstream.cli walk --n 5 --print-prompt   # dry-run: print LLM prompt instead of calling
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph.builder import build_graph, stats
from .sampler.random_walk import RandomWalker
from .scenario_synthesizer.prompt import build_messages


def cmd_stats(args):
    g = build_graph()
    s = stats(g)
    print(f"Nodes:        {s['nodes']}")
    print(f"Edges total:  {s['edges']}")
    print("Edges by type:")
    for k, v in sorted(s["by_edge_type"].items(), key=lambda x: -x[1]):
        print(f"  {k:14s} {v}")


def cmd_walk(args):
    g = build_graph()
    walker = RandomWalker(g, seed=args.seed)
    walks = walker.sample(args.n)

    if not walks:
        print("No walks produced (graph empty?)", file=sys.stderr)
        return 1

    for i, w in enumerate(walks, 1):
        print(f"\n{'='*70}")
        print(f"Walk #{i}  [{w.candidate_bug_layer}]  signature={w.signature}")
        print(f"{'='*70}")
        print(f"Nodes : {w.nodes}")
        if w.edges:
            for e in w.edges:
                note = f"  // {e.note}" if e.note else ""
                print(f"        {e.src} --[{e.type.value}]--> {e.dst}{note}")
        else:
            print("Edges : (single-node walk)")
        print(f"Rationale: {w.rationale}")

        if args.print_prompt:
            print("\n--- LLM messages (dry-run) ---")
            for msg in build_messages(w, g):
                print(f"\n[{msg['role'].upper()}]")
                print(msg["content"])
                print()

    if args.output_dir:
        outdir = Path(args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        for i, w in enumerate(walks, 1):
            f = outdir / f"walk_{i:03d}_{w.signature}.json"
            f.write_text(
                json.dumps(
                    {
                        "nodes": w.nodes,
                        "edges": [
                            {"src": e.src, "dst": e.dst, "type": e.type.value, "note": e.note}
                            for e in w.edges
                        ],
                        "candidate_bug_layer": w.candidate_bug_layer,
                        "rationale": w.rationale,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        print(f"\nWrote {len(walks)} walks to {outdir}")

    return 0


def main():
    p = argparse.ArgumentParser(prog="upstream", description="RFCGym upstream sampler")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="Print graph stats")
    p_stats.set_defaults(func=cmd_stats)

    p_walk = sub.add_parser("walk", help="Sample random walks")
    p_walk.add_argument("--n", type=int, default=5, help="Number of walks to sample")
    p_walk.add_argument("--seed", type=int, default=None, help="RNG seed (for reproducibility)")
    p_walk.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the LLM prompt for each walk (dry-run, no API call)",
    )
    p_walk.add_argument("--output-dir", type=Path, default=None, help="Write walks as JSON files")
    p_walk.set_defaults(func=cmd_walk)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
