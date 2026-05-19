"""Wireshark dissector source-tree scanner.

Wireshark ships 1500+ protocol dissectors as C files under
`epan/dissectors/packet-*.c`. Each dissector typically:

  * registers itself:           `register_dissector("name", ...)`
  * is dispatched-to by ports:  `dissector_add_uint("tcp.port", 80, ...)`
  * is dispatched-to by string: `dissector_add_string("media_type", "text/html", ...)`
  * calls into other dissectors: `find_dissector("tls")`,
                                  `call_dissector(handle, ...)`

We grep these patterns out of the C source and produce a JSON graph of
"protocol X dispatches to protocol Y" relations. This complements the
RFC index crawler: RFC index gives us *nodes* (protocols by spec),
Wireshark gives us *edges* (observed dispatch / layering relations).

Usage:
    # 1) Clone wireshark source somewhere
    git clone --depth=1 https://gitlab.com/wireshark/wireshark.git /tmp/ws

    # 2) Point this scanner at it
    python -m upstream.data_sources.wireshark_dissector_scan \\
        --src /tmp/ws/epan/dissectors \\
        --output upstream/data_sources/wireshark_relations.json

Output JSON structure:
    {
      "scanned_files": int,
      "protocols": [
        {
          "name": "http",
          "file": "packet-http.c",
          "title": "Hypertext Transfer Protocol",  # from proto_register_protocol
          "ports": {"tcp": [80, 443, 8080], "udp": [], ...},
          "media_types": ["text/html", ...],
          "dispatches_to": ["tls", "websocket", "media", ...],
          "dispatched_from": []  # filled in after pass 2
        },
        ...
      ]
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set


# === Regex patterns matching Wireshark's dissector registration API ===

# proto_register_protocol("Long Name", "ShortName", "filter.name")
RE_REGISTER_PROTOCOL = re.compile(
    r'proto_register_protocol\s*\(\s*'
    r'"([^"]+)"\s*,\s*'        # long name
    r'"([^"]+)"\s*,\s*'        # short name
    r'"([^"]+)"\s*\)',         # filter name
    re.DOTALL,
)

# register_dissector("name", dissect_fn, proto_handle)
RE_REGISTER_DISSECTOR = re.compile(
    r'register_dissector\s*\(\s*"([^"]+)"',
)

# dissector_add_uint("table_name", PORT, handle)
RE_ADD_UINT = re.compile(
    r'dissector_add_uint\s*\(\s*'
    r'"([^"]+)"\s*,\s*'             # table name, e.g. "tcp.port"
    r'(\d+|0x[0-9a-fA-F]+|[A-Z_][A-Z0-9_]*)'  # port number or macro name
)

# dissector_add_string("table_name", "value", handle)
RE_ADD_STRING = re.compile(
    r'dissector_add_string\s*\(\s*'
    r'"([^"]+)"\s*,\s*'
    r'"([^"]+)"'
)

# find_dissector("name")  --> creates a handle to another protocol's dissector
RE_FIND_DISSECTOR = re.compile(r'find_dissector\s*\(\s*"([^"]+)"')

# call_dissector(handle_var, ...) - we can't always tie this back to a name,
# but if the handle_var was set by find_dissector earlier we already caught it.


def scan_one(c_path: Path) -> dict:
    """Parse one packet-*.c file into a partial protocol record."""
    text = c_path.read_text(encoding="utf-8", errors="replace")

    name = ""
    title = ""
    short = ""
    rec_registered_names: List[str] = []
    rec_dispatches_to: Set[str] = set()
    rec_ports: Dict[str, List[int]] = {}
    rec_media: List[str] = []

    # Protocol registration (long/short/filter)
    m = RE_REGISTER_PROTOCOL.search(text)
    if m:
        title = m.group(1)
        short = m.group(2)
        name = m.group(3)  # filter name is most stable; what other code refs

    # All dissectors this file registers itself as
    for m in RE_REGISTER_DISSECTOR.finditer(text):
        rec_registered_names.append(m.group(1))

    # Inbound dispatch: ports & media types
    for m in RE_ADD_UINT.finditer(text):
        table = m.group(1)
        port_str = m.group(2)
        # only collect numeric port-style tables
        if not table.endswith(".port"):
            continue
        try:
            port = int(port_str, 0)  # supports "80" and "0x50"
        except ValueError:
            continue  # macro name - skip; we'd need to parse #defines
        rec_ports.setdefault(table.split(".port")[0], []).append(port)

    for m in RE_ADD_STRING.finditer(text):
        table = m.group(1)
        value = m.group(2)
        if "media_type" in table or "media.type" in table:
            rec_media.append(value)

    # Outbound calls: find_dissector("other_name")
    for m in RE_FIND_DISSECTOR.finditer(text):
        rec_dispatches_to.add(m.group(1))

    # Pick the canonical name: filter name > registered dissector name > file stem
    if not name and rec_registered_names:
        name = rec_registered_names[0]
    if not name:
        name = c_path.stem.replace("packet-", "")

    return {
        "name": name,
        "file": c_path.name,
        "title": title,
        "short_name": short,
        "registered_aliases": sorted(set(rec_registered_names)),
        "ports": {k: sorted(set(v)) for k, v in rec_ports.items()},
        "media_types": sorted(set(rec_media)),
        "dispatches_to": sorted(rec_dispatches_to),
    }


def build_reverse_edges(records: List[dict]) -> List[dict]:
    """Compute dispatched_from from dispatches_to."""
    by_name: Dict[str, dict] = {r["name"]: r for r in records}
    for r in records:
        r["dispatched_from"] = []
    for r in records:
        for tgt_name in r["dispatches_to"]:
            tgt = by_name.get(tgt_name)
            if tgt is not None:
                tgt["dispatched_from"].append(r["name"])
    for r in records:
        r["dispatched_from"] = sorted(set(r["dispatched_from"]))
    return records


def scan_tree(src_dir: Path) -> List[dict]:
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"{src_dir} does not exist. Clone wireshark first: "
            f"`git clone --depth=1 https://gitlab.com/wireshark/wireshark.git`"
        )
    c_files = sorted(src_dir.glob("packet-*.c"))
    print(f"[scan] found {len(c_files)} dissector files in {src_dir}", file=sys.stderr)

    records: List[dict] = []
    for i, path in enumerate(c_files, 1):
        if i % 100 == 0:
            print(f"[scan] {i}/{len(c_files)}", file=sys.stderr)
        try:
            records.append(scan_one(path))
        except Exception as e:
            print(f"[warn] {path.name}: {e}", file=sys.stderr)

    # Deduplicate: some files split a protocol across files; we merge by name.
    by_name: Dict[str, dict] = {}
    for r in records:
        nm = r["name"]
        if nm in by_name:
            # Merge
            existing = by_name[nm]
            for k in ("registered_aliases", "media_types", "dispatches_to"):
                existing[k] = sorted(set(existing[k]) | set(r[k]))
            for proto, ports in r["ports"].items():
                existing["ports"].setdefault(proto, [])
                existing["ports"][proto] = sorted(set(existing["ports"][proto]) | set(ports))
        else:
            by_name[nm] = r

    return build_reverse_edges(list(by_name.values()))


def main():
    p = argparse.ArgumentParser(description="Scan Wireshark dissector C sources")
    p.add_argument("--src", type=Path, required=True,
                   help="Path to wireshark/epan/dissectors")
    p.add_argument("--output", type=Path,
                   default=Path(__file__).parent / "wireshark_relations.json")
    args = p.parse_args()

    records = scan_tree(args.src)

    # Pretty stats
    edges = sum(len(r["dispatches_to"]) for r in records)
    has_port = sum(1 for r in records if r["ports"])
    print(f"[done] {len(records)} unique protocols, {edges} dispatch edges, "
          f"{has_port} have port bindings", file=sys.stderr)

    args.output.write_text(
        json.dumps(
            {
                "scanned_files": len(records),
                "edge_count": edges,
                "protocols": sorted(records, key=lambda r: r["name"]),
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[write] {args.output} ({args.output.stat().st_size:,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
