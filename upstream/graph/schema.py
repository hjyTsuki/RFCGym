"""Protocol graph schema.

Node = a protocol (or a specific version of a protocol).
Edge = a structural relation between two protocols. The edge type drives the
       bug_layer inference when the random walker hands a subgraph to the
       scenario synthesizer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class EdgeType(str, Enum):
    """How two protocols relate. Drives bug_layer mapping at synthesis time."""

    VERSION_OF = "version_of"
    """Same family, different version (e.g. HTTP/3 -> HTTP/1.1).
    A walk of two version_of-connected nodes is an L2 candidate."""

    COMPOSES_WITH = "composes_with"
    """Two distinct protocols share a logical object or trust assumption
    (e.g. SPF authenticates Return-Path while IMF From is what users see).
    A walk through composes_with edges is the prime L4 candidate."""

    LAYERED_ON = "layered_on"
    """One protocol carries the other transport-wise (HTTPS on TCP, QUIC on
    UDP). Less interesting alone for L4 unless combined with composes_with."""

    EMBEDS = "embeds"
    """One protocol carries another as payload (gRPC over HTTP/2, MIME inside
    SMTP). Often L4 territory because the carrier and payload may disagree
    on framing / length / encoding."""

    TRANSLATABLE = "translatable"
    """There exists a gateway/proxy that converts between the two (CDN
    translating HTTP/3 to HTTP/1.1). If both endpoints are in the same family
    this is L2; if in different families it is L4."""

    DISPATCHES_TO = "dispatches_to"
    """Wireshark-style: a parent dissector routes payload to a child based on
    content. Indicates a parsing decision point."""


# Edge types most likely to yield each bug layer when sampled.
EDGES_FOR_L2: tuple = (EdgeType.VERSION_OF, EdgeType.TRANSLATABLE)
EDGES_FOR_L4: tuple = (EdgeType.COMPOSES_WITH, EdgeType.EMBEDS, EdgeType.DISPATCHES_TO)


@dataclass
class Node:
    id: str
    family: str
    version: str = "any"
    rfc: List[int] = field(default_factory=list)
    layer: str = "app"            # 'app' | 'sec' | 'transport' | 'link' | 'format' | ...
    wire: str = "binary"           # 'text' | 'binary'
    canonical: bool = False        # exactly one node per family should set this True;
                                   # walker uses canonical version when crossing protocols
    note: Optional[str] = None

    def display(self) -> str:
        return f"{self.family}/{self.version}" if self.version != "any" else self.family


@dataclass
class Edge:
    src: str
    dst: str
    type: EdgeType
    note: Optional[str] = None

    @property
    def is_l2_candidate(self) -> bool:
        return self.type in EDGES_FOR_L2

    @property
    def is_l4_candidate(self) -> bool:
        return self.type in EDGES_FOR_L4
