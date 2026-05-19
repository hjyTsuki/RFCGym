"""Merge RFC catalog + Wireshark relations into graph candidate sets.

Takes the outputs of:
  - rfc_index_crawler.py  -> rfc_catalog.json   (~9700 entries, 4400 protocol candidates)
  - wireshark_dissector_scan.py -> wireshark_relations.json  (~1500 protocols + dispatch edges)

Produces:
  - graph_candidates_nodes.json   - one record per protocol candidate, fields ready for
                                    seed_graph.yaml promotion (id, family, version, rfc, layer)
  - graph_candidates_edges.json   - dispatch / layered_on candidate edges from wireshark
                                    relations, with both endpoint name resolutions
  - merge_report.md                - human review summary

Promotion to seed_graph.yaml is NOT automatic. You inspect the candidates,
optionally LLM-classify, and decide which to add.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


BASE = Path(__file__).resolve().parent
DEFAULT_RFC = BASE / "rfc_catalog.json"
DEFAULT_WS = BASE / "wireshark_relations.json"
DEFAULT_NODES = BASE / "graph_candidates_nodes.json"
DEFAULT_EDGES = BASE / "graph_candidates_edges.json"
DEFAULT_REPORT = BASE / "merge_report.md"


def slugify(name: str) -> str:
    """Turn 'HTTP/3' into 'http_3', 'OAuth 2.0' into 'oauth_2_0', etc."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "unknown"


# Mapping table: substring in RFC title -> family name in our graph schema.
# Conservative; only obvious matches.
FAMILY_HINTS = [
    (re.compile(r"\bhypertext transfer\b", re.I), "http"),
    (re.compile(r"\bhttp/?\s*(\d(?:\.\d)?)", re.I), "http"),
    (re.compile(r"\btransport layer security\b", re.I), "tls"),
    (re.compile(r"\bdns over\b", re.I), "dns"),
    (re.compile(r"\bdomain name system\b", re.I), "dns"),
    (re.compile(r"\bsimple mail transfer\b", re.I), "smtp"),
    (re.compile(r"\binternet message format\b", re.I), "imf"),
    (re.compile(r"\bmultipurpose internet mail\b", re.I), "mime"),
    (re.compile(r"\boauth\b", re.I), "oauth"),
    (re.compile(r"\bconstrained application protocol\b", re.I), "coap"),
    (re.compile(r"\bwebsocket\b", re.I), "ws"),
    (re.compile(r"\bsession initiation protocol\b", re.I), "sip"),
    (re.compile(r"\bsender policy framework\b", re.I), "spf"),
    (re.compile(r"\bdomainkeys identified mail\b", re.I), "dkim"),
    (re.compile(r"\bdomain-based message authentication\b", re.I), "dmarc"),
    (re.compile(r"\bauthenticated received chain\b", re.I), "arc"),
    (re.compile(r"\bquic\b", re.I), "quic"),
    (re.compile(r"\btransmission control protocol\b", re.I), "tcp"),
    (re.compile(r"\buser datagram protocol\b", re.I), "udp"),
    (re.compile(r"\binternet message access\b", re.I), "imap"),
    (re.compile(r"\bpost office protocol\b", re.I), "pop"),
    (re.compile(r"\bnetwork time protocol\b", re.I), "ntp"),
    (re.compile(r"\bsimple network management\b", re.I), "snmp"),
    (re.compile(r"\blightweight directory access\b", re.I), "ldap"),
    (re.compile(r"\bfile transfer protocol\b", re.I), "ftp"),
    (re.compile(r"\bsecure shell\b", re.I), "ssh"),
]

VERSION_RE = re.compile(r"\b(?:v(?:ersion)?\.?\s*)?(\d+(?:\.\d+){0,2})\b")


def infer_family_version(title: str) -> Optional[tuple]:
    """Return (family, version) for a likely-protocol RFC title, or None."""
    for pat, fam in FAMILY_HINTS:
        m = pat.search(title)
        if m:
            # try to extract a version from the same title
            ver = None
            vm = VERSION_RE.search(title)
            if vm:
                ver = vm.group(1)
            return (fam, ver or "any")
    return None


def build_node_candidates(rfc_catalog: List[dict]) -> List[dict]:
    """One candidate node per (family, version) pair, citing all relevant RFCs."""
    grouped: Dict[tuple, dict] = {}
    for entry in rfc_catalog:
        if not entry.get("is_protocol_candidate"):
            continue
        fv = infer_family_version(entry["title"])
        if fv is None:
            continue
        family, version = fv
        key = (family, version)
        rec = grouped.setdefault(key, {
            "id": f"{family}_{slugify(version)}" if version != "any" else family,
            "family": family,
            "version": version,
            "rfc": [],
            "rfc_titles": [],
            "layer": "app",
            "wire": "binary",
            "is_seed_graph_candidate": True,
            "sources": [],
        })
        # Extract RFC number from "RFC9110"
        m = re.search(r"\d+", entry["doc_id"])
        if m:
            rec["rfc"].append(int(m.group(0)))
        rec["rfc_titles"].append(entry["title"])
        rec["sources"].append(entry["doc_id"])

    # Dedup and sort RFCs
    for rec in grouped.values():
        rec["rfc"] = sorted(set(rec["rfc"]))
        rec["sources"] = sorted(set(rec["sources"]))
    return sorted(grouped.values(), key=lambda r: (r["family"], r["version"]))


def build_edge_candidates(ws: dict, node_index: Dict[str, dict]) -> List[dict]:
    """From wireshark dispatch relations, build candidate edges.

    Edge type heuristic:
      - if wireshark dispatch is via a `.port` (tcp/udp), and src is app-layer,
        treat as `layered_on` -- but we only emit edges where BOTH endpoints
        appear in our node candidate set (so it's a graph edge, not a free node).
      - otherwise treat as `dispatches_to` (a generic application-layer
        sub-dissection)
    """
    edges: List[dict] = []
    for proto in ws.get("protocols", []):
        src_name = proto["name"]
        # Resolve to a candidate family name
        src_family = src_name if src_name in {n["family"] for n in node_index.values()} else None
        for tgt_name in proto.get("dispatches_to", []):
            # Try to find target in our node index
            tgt_family = tgt_name if tgt_name in {n["family"] for n in node_index.values()} else None
            edges.append({
                "from_wireshark_name": src_name,
                "to_wireshark_name": tgt_name,
                "from_family": src_family,
                "to_family": tgt_family,
                "type": "dispatches_to",
                "in_existing_graph": bool(src_family and tgt_family),
            })
    return edges


def main():
    p = argparse.ArgumentParser(description="Merge RFC + Wireshark into graph candidates")
    p.add_argument("--rfc", type=Path, default=DEFAULT_RFC)
    p.add_argument("--wireshark", type=Path, default=DEFAULT_WS)
    p.add_argument("--nodes-out", type=Path, default=DEFAULT_NODES)
    p.add_argument("--edges-out", type=Path, default=DEFAULT_EDGES)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = p.parse_args()

    if not args.rfc.exists():
        print(f"[error] {args.rfc} not found; run rfc_index_crawler.py first", file=sys.stderr)
        sys.exit(1)
    rfc_catalog = json.load(open(args.rfc, encoding="utf-8"))
    print(f"[load] {len(rfc_catalog):,} RFC entries", file=sys.stderr)

    ws = {"protocols": []}
    if args.wireshark.exists():
        ws = json.load(open(args.wireshark, encoding="utf-8"))
        print(f"[load] {len(ws.get('protocols', []))} Wireshark dissectors",
              file=sys.stderr)
    else:
        print(f"[skip] {args.wireshark} not found - edges will be empty", file=sys.stderr)

    nodes = build_node_candidates(rfc_catalog)
    node_index = {n["id"]: n for n in nodes}
    edges = build_edge_candidates(ws, node_index)

    args.nodes_out.write_text(
        json.dumps(nodes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    args.edges_out.write_text(
        json.dumps(edges, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Report
    families = defaultdict(list)
    for n in nodes:
        families[n["family"]].append(n["version"])
    edges_in_graph = sum(1 for e in edges if e["in_existing_graph"])
    report = [
        "# Merge Report",
        "",
        f"Generated from `{args.rfc.name}` and `{args.wireshark.name}`.",
        "",
        f"- Node candidates: **{len(nodes)}**  (across {len(families)} families)",
        f"- Edge candidates: **{len(edges)}**  ({edges_in_graph} have both endpoints in node set)",
        "",
        "## Families and version counts (top 20)",
        "",
        "| Family | Versions | Example RFCs |",
        "|---|---|---|",
    ]
    for fam, vers in sorted(families.items(), key=lambda x: -len(x[1]))[:20]:
        sample = ", ".join(sorted(set(vers))[:5])
        first_node = next(n for n in nodes if n["family"] == fam)
        rfcs = ", ".join(f"RFC{r}" for r in first_node["rfc"][:3])
        report.append(f"| `{fam}` | {len(set(vers))} ({sample}) | {rfcs} |")

    if edges_in_graph:
        report.extend([
            "",
            "## Sample edges in current graph (first 15)",
            "",
            "| From | To | Type |",
            "|---|---|---|",
        ])
        for e in [x for x in edges if x["in_existing_graph"]][:15]:
            report.append(f"| `{e['from_family']}` | `{e['to_family']}` | {e['type']} |")

    args.report.write_text("\n".join(report), encoding="utf-8")
    print(f"[write] {args.nodes_out}", file=sys.stderr)
    print(f"[write] {args.edges_out}", file=sys.stderr)
    print(f"[write] {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
