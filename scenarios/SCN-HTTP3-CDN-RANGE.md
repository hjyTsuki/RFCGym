# SCN-HTTP3-CDN-RANGE: HTTP/3 → HTTP/1.1 Range Header Translation Anomaly

## Scenario Summary

When a CDN translates HTTP/3 client requests to HTTP/1.1 back-to-origin requests,
ambiguity in how the `Range` header is forwarded creates two classes of attack:

1. **Range Removal Amplification (DoS)** — CDN strips `Range`, origin returns the
   full resource → bandwidth amplification from a 1-byte client request.
2. **Range Expansion Amplification (DoS)** — CDN rounds the requested byte range
   up to a vendor-specific page size (4 KiB / 512 KiB / 1 MiB) → amplification
   smaller than full-removal but still significant.

## Bug Layer

**L2 — Cross-version translation.**

The HTTP semantics (RFC 9110) are consistent across HTTP/1.1, /2, /3 in
principle, but each version has its own wire format. The **HTTP/3 →
HTTP/1.1 translation layer** at the CDN edge is precisely the boundary where
the same protocol object (`Range` header) acquires diverging semantics:

| Component | Behavior |
|---|---|
| HTTP/3 client | Sends `Range: bytes=0-0` |
| CDN translation layer | Decides whether to forward / strip / expand the header |
| HTTP/1.1 origin | Receives whatever the layer chose |

The bug class lives at the version translation boundary, not in any single
component's implementation quality. Per the RFCGym 4-layer taxonomy this is
**L2 (cross-version)**. Attack method: differential testing of translation
outputs across vendor stacks.

If the same `Range` ambiguity instead manifested between two vendors both
running HTTP/1.1 (no translation), it would be **L3 (cross-vendor)**. If it
involved HTTP coexisting with a different protocol (e.g. WebSocket or gRPC
on the same port) disagreeing on the object, it would be **L4
(cross-protocol)**. Single-implementation bugs in any vendor's parser are
**out of scope**.

## Protocols In Scope

- **HTTP/3** (RFC 9114 — HTTP semantics over QUIC)
- **HTTP/1.1** (RFC 9110 — semantics, RFC 9112 — message syntax)
- **Range Requests** (RFC 9110 §14 — Range, Content-Range, 206 Partial Content)
- **QUIC** (RFC 9000 — transport, not directly attacked but required)

## Vendor Matrix (Suggested)

ingress (HTTP/3 edge):
  - { name: nginx, version: "1.27", type: open-source, role: ingress }
  - { name: envoy, version: "1.30", type: open-source, role: ingress }
  - { name: caddy, version: "2.8",  type: open-source, role: ingress }
  - { name: haproxy, version: "2.9", type: open-source, role: ingress }
  # Optional real-world targets (require external account):
  # - { name: cloudflare, version: latest, type: blackbox-api, role: ingress }
  # - { name: tencent-cdn, version: latest, type: blackbox-api, role: ingress }

origin (HTTP/1.1 only):
  - { name: nginx, version: "1.24", type: open-source, role: origin }

## Topology

```
client(HTTP/3) → [ingress vendor X] → [origin nginx 1.24 HTTP/1.1]
                                                ↓
                                       29 MB test.png served
                                       with Accept-Ranges: bytes
```

Each ingress vendor runs as its own service; the same client testcase is sent
to each ingress to expose vendor divergence.

## Known Attacks (Ground Truth, From H3Act §5.1.1)

The following are **observed** behaviors in commercial CDNs (Cloudflare, Fastly,
Aliyun, Tencent, Huawei) per the H3Act paper. They serve as the strong oracle
for environment readiness.

### Attack A1: Range Removal Amplification
- Client sends HTTP/3: `Range: bytes=0-0` for a large resource
- Vendor strips `Range` from the back-to-origin HTTP/1.1 request
- Origin returns full resource (status 200, full body)
- Client receives only 1 byte (vendor truncates), origin paid full bandwidth
- **Amplification ratio**: ~`resource_size / 1`. For 29 MB → ~30M×.

### Attack A2: Range Expansion (4 KiB)
- Client sends `Range: bytes=0-0`
- Vendor rounds to `Range: bytes=0-4095` toward origin
- Origin returns 4 KiB
- Amplification: ~4096×

### Attack A3: Range Expansion (512 KiB)
- Same as A2 but vendor rounds to 512 KiB
- Amplification: ~524288×

### Attack A4: Range Expansion (1 MiB)
- Same as A2 but vendor rounds to 1 MiB
- Amplification: ~1048576×

## Ambiguity Hints (visible to Fuzzer only if hide_hints=false)

- `Range` header forwarding policy on translation
- `Range` header value transformation (rounding, removal, multi-range collapse)
- Interaction with `Accept-Ranges` from origin (does the vendor cache vary on it?)
- Behavior on invalid `Range` (e.g. `bytes=99999-`, `bytes=-1`, `bytes=0-0,1-1,2-2,...`)
- Conditional header coexistence (`If-Range`, `If-Match`)

## References

Authoritative protocol specs (download via <https://www.rfc-editor.org/>):

- **RFC 9114** — HTTP/3 (https://www.rfc-editor.org/rfc/rfc9114)
- **RFC 9110** — HTTP Semantics, §14 Range Requests (https://www.rfc-editor.org/rfc/rfc9110)
- **RFC 9111** — HTTP Caching (https://www.rfc-editor.org/rfc/rfc9111)
- **RFC 9112** — HTTP/1.1 Message Syntax (https://www.rfc-editor.org/rfc/rfc9112)
- **RFC 9000** — QUIC Transport (https://www.rfc-editor.org/rfc/rfc9000)

Research papers / prior disclosures:

- H3Act paper §5.1.1 (the source of this scenario)
- Li et al. 2018 "Rangetastic" — original Range amplification disclosure
- Prior Aliyun/Huawei/CloudFront patches noted in H3Act §5.1.1
