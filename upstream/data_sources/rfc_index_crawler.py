"""RFC Editor index crawler.

Downloads https://www.rfc-editor.org/in-notes/rfc-index.xml (caches locally),
parses it, and produces a JSON catalog of all RFCs annotated with:
  - doc_id (e.g. "RFC9110")
  - title
  - date
  - status (INTERNET STANDARD / PROPOSED STANDARD / INFORMATIONAL / ...)
  - keywords
  - abstract (first ~500 chars)
  - is_protocol_candidate: bool   - heuristic; LLM should confirm before promoting to graph

Usage:
    python -m upstream.data_sources.rfc_index_crawler              # uses cached or downloads
    python -m upstream.data_sources.rfc_index_crawler --refresh    # force re-download
    python -m upstream.data_sources.rfc_index_crawler --top 100    # only top 100 in output
    python -m upstream.data_sources.rfc_index_crawler --filter-protocols  # only protocol-likely entries

Output: upstream/data_sources/rfc_catalog.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET


INDEX_URL = "https://www.rfc-editor.org/in-notes/rfc-index.xml"
CACHE_FILE = Path(__file__).resolve().parent / "rfc-index.xml.cache"
OUTPUT_FILE = Path(__file__).resolve().parent / "rfc_catalog.json"
# Actual namespace observed in 2026: HTTPS, not HTTP.
NS = {"r": "https://www.rfc-editor.org/rfc-index"}


# Heuristic: title or keywords that strongly suggest a protocol document.
# Tuned for high precision on "yes this is a protocol" without too many false negatives.
PROTOCOL_KEYWORDS = {
    "protocol", "transport", "tunneling", "encapsulation",
    "handshake", "negotiation", "authentication", "encryption",
    "signaling", "messaging", "routing", "discovery",
    "encoding", "framing", "session", "transfer",
}

# Negative signal: definitely not a protocol document
NON_PROTOCOL_HINTS = {
    "policy", "process", "procedures", "considerations",
    "guidelines", "best current practice", "report",
    "registration", "registry", "applicability",
}


def fetch_index(refresh: bool = False) -> Path:
    """Download rfc-index.xml to cache, or use cached copy."""
    if CACHE_FILE.exists() and not refresh:
        print(f"[cache] using {CACHE_FILE} ({CACHE_FILE.stat().st_size:,} bytes)",
              file=sys.stderr)
        return CACHE_FILE

    print(f"[fetch] downloading {INDEX_URL}", file=sys.stderr)
    req = urllib.request.Request(
        INDEX_URL,
        headers={"User-Agent": "RFCGym-upstream-crawler/0.1 (research)"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    CACHE_FILE.write_bytes(data)
    print(f"[fetch] wrote {CACHE_FILE} ({len(data):,} bytes)", file=sys.stderr)
    return CACHE_FILE


def _text(elem: Optional[ET.Element], default: str = "") -> str:
    if elem is None:
        return default
    return (elem.text or "").strip()


def _findall_text(parent: ET.Element, path: str) -> List[str]:
    out: List[str] = []
    for el in parent.findall(path, NS):
        if el.text:
            out.append(el.text.strip())
    return out


def is_protocol_candidate(title: str, keywords: List[str], status: str) -> bool:
    """Heuristic: would this RFC plausibly be a node in a protocol graph?"""
    text = " ".join([title.lower()] + [k.lower() for k in keywords])

    # Negative signals - definitely skip
    if any(neg in text for neg in NON_PROTOCOL_HINTS):
        return False

    # Most active protocol docs have "STANDARD" in status
    standard_like = any(s in status.upper() for s in ("STANDARD", "DRAFT STANDARD"))

    # Positive signals
    positive = (
        any(p in text for p in PROTOCOL_KEYWORDS) or
        # very common prefixes used in protocol RFCs
        re.search(r"\b(http|tls|dns|smtp|imap|pop3?|sip|rtsp|rtp|sctp|tcp|udp|ip|ipv6|mqtt|coap|oauth|saml|ldap|snmp|ssh|ntp|ftp|tftp|websocket|graphql|grpc|quic)\b", text, re.IGNORECASE)
    )

    return positive and (standard_like or "INFORMATIONAL" in status.upper())


def parse_index(xml_path: Path) -> List[Dict]:
    """Stream-parse rfc-index.xml into a list of dicts.

    Uses iterparse for memory efficiency (file is ~5 MB+ and growing).
    """
    print(f"[parse] reading {xml_path}", file=sys.stderr)
    entries: List[Dict] = []

    # iterparse to walk <rfc-entry> elements one at a time
    for event, elem in ET.iterparse(str(xml_path), events=("end",)):
        # The tag may have a namespace prefix
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "rfc-entry":
            continue

        doc_id = _text(elem.find("r:doc-id", NS)) or _text(elem.find("doc-id"))
        if not doc_id:
            elem.clear()
            continue

        title = _text(elem.find("r:title", NS)) or _text(elem.find("title"))

        # Authors
        author_names: List[str] = []
        for au in elem.findall("r:author", NS) or elem.findall("author"):
            n = au.find("r:name", NS)
            if n is None:
                n = au.find("name")
            if n is not None and n.text:
                author_names.append(n.text.strip())

        # Date
        date_el = elem.find("r:date", NS)
        if date_el is None:
            date_el = elem.find("date")
        if date_el is not None:
            month_el = date_el.find("r:month", NS)
            if month_el is None:
                month_el = date_el.find("month")
            year_el = date_el.find("r:year", NS)
            if year_el is None:
                year_el = date_el.find("year")
            month = _text(month_el)
            year = _text(year_el)
            date_str = f"{month} {year}".strip()
        else:
            date_str = ""

        # Keywords - both XML structures observed
        keywords = (
            _findall_text(elem, "r:keywords/r:kw")
            or _findall_text(elem, "keywords/kw")
        )

        # Abstract - <abstract><p>...</p></abstract>
        abstract_parts: List[str] = []
        for p in elem.findall("r:abstract/r:p", NS) or elem.findall("abstract/p"):
            if p.text:
                abstract_parts.append(p.text.strip())
        abstract = " ".join(abstract_parts)[:500]

        status = _text(elem.find("r:current-status", NS)) or _text(elem.find("current-status"))

        # Cross-references
        obsoletes = _findall_text(elem, "r:obsoletes/r:doc-id") or _findall_text(elem, "obsoletes/doc-id")
        obsoleted_by = _findall_text(elem, "r:obsoleted-by/r:doc-id") or _findall_text(elem, "obsoleted-by/doc-id")
        updates = _findall_text(elem, "r:updates/r:doc-id") or _findall_text(elem, "updates/doc-id")
        updated_by = _findall_text(elem, "r:updated-by/r:doc-id") or _findall_text(elem, "updated-by/doc-id")

        entries.append({
            "doc_id": doc_id,
            "title": title,
            "authors": author_names,
            "date": date_str,
            "status": status,
            "keywords": keywords,
            "abstract": abstract,
            "obsoletes": obsoletes,
            "obsoleted_by": obsoleted_by,
            "updates": updates,
            "updated_by": updated_by,
            "is_protocol_candidate": is_protocol_candidate(title, keywords, status),
        })

        elem.clear()  # free memory

    print(f"[parse] parsed {len(entries):,} RFC entries", file=sys.stderr)
    return entries


def main():
    p = argparse.ArgumentParser(description="RFC Editor index crawler")
    p.add_argument("--refresh", action="store_true", help="Force re-download of index")
    p.add_argument("--top", type=int, default=None,
                   help="Output only first N entries (after filtering)")
    p.add_argument("--filter-protocols", action="store_true",
                   help="Keep only entries flagged is_protocol_candidate=True")
    p.add_argument("--output", type=Path, default=OUTPUT_FILE,
                   help="Where to write the JSON catalog")
    args = p.parse_args()

    xml_path = fetch_index(refresh=args.refresh)
    entries = parse_index(xml_path)

    if args.filter_protocols:
        before = len(entries)
        entries = [e for e in entries if e["is_protocol_candidate"]]
        print(f"[filter] {before:,} -> {len(entries):,} entries are protocol candidates",
              file=sys.stderr)

    if args.top:
        entries = entries[: args.top]

    args.output.write_text(json.dumps(entries, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[write] {args.output} ({len(entries):,} entries, "
          f"{args.output.stat().st_size:,} bytes)", file=sys.stderr)

    # Summary stats
    n_proto = sum(1 for e in entries if e["is_protocol_candidate"])
    statuses: Dict[str, int] = {}
    for e in entries:
        statuses[e["status"]] = statuses.get(e["status"], 0) + 1
    print(f"  protocol candidates: {n_proto:,}", file=sys.stderr)
    print(f"  status distribution:", file=sys.stderr)
    for s, n in sorted(statuses.items(), key=lambda x: -x[1])[:8]:
        print(f"    {s!r}: {n:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
