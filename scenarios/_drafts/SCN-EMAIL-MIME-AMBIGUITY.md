# SCN-EMAIL-MIME-AMBIGUITY: MTA / Detector / MUA disagree on MIME parsing

> Draft auto-generated from upstream walks #3 + #6 (seed=11). Reviewed by
> human against published literature. Ready for Stage-1 Protocol Analyzer.

## Scenario Summary

Email attachment detection engines, MTAs, and MUAs parse MIME structure
inconsistently. An attacker crafts a multipart/mixed message whose
boundary, `Content-Type`, or `Content-Transfer-Encoding` is interpreted
one way by the detector (treats body as benign text) and another way by
the MUA (extracts and offers a malicious attachment).

## Bug Layer

**L4 — cross-protocol composition**

Three protocols compose around the same logical object ("the attachments
of this message"):

| Component | Protocol | Sees |
|---|---|---|
| MTA (e.g. Postfix) | SMTP 5321 | DATA payload |
| Content detector (Amavis / Coremail / iCloud filter) | MIME 2045–2049 | parsed MIME tree |
| MUA (Outlook / Thunderbird / Gmail Web) | IMF 5322 + MIME | rendered attachment list |

A MIME boundary mismatch, a malformed RFC 2231 continuation, or an
unusual `Content-Transfer-Encoding` value can produce different attachment
trees in detector vs MUA.

## Protocols In Scope

- **SMTP 5321** (RFC 5321) — envelope transport
- **IMF 5322** (RFC 5322) — message format / headers
- **MIME** (RFC 2045–2049, RFC 2231 multi-line continuations)

## Vendor Matrix (suggested for builder)

```yaml
detector:
  - {name: amavis, version: 2.13, type: open-source, role: content_filter}
  - {name: spamassassin, version: 4.0, type: open-source, role: content_filter}
mta:
  - {name: postfix, version: 3.8, type: open-source, role: mta}
  - {name: exim, version: 4.98, type: open-source, role: mta}
mua:
  - {name: thunderbird, version: latest, type: open-source, role: mua}
  - {name: roundcube, version: 1.6, type: open-source, role: webmail}
```

## Known Attacks (Ground Truth)

| ID | Mechanism | Reference |
|---|---|---|
| A1 | RFC 2231 multi-line filename evasion → Exim 4.97.1 passes attachment past filter | **CVE-2024-39929** |
| A2 | MIME boundary delimiter disagreement between detector and MUA | Inbox Invasion §4 (CCS'24) |
| A3 | base64 padding / encoded-word boundary inconsistency | Inbox Invasion §4 |
| A4 | Nested multipart structures parsed shallowly by detector | Inbox Invasion §4 |

## Ambiguity Hints

- `boundary` parameter with leading whitespace, escape characters, or duplicates
- `Content-Type` parameter ordering and case
- RFC 2231 `name*0=...; name*1=...` continuations
- Mixed `Content-Transfer-Encoding` (7bit vs base64 inside same nested part)

## References

- **Inbox Invasion** — Zhang et al., CCS 2024,
  <https://dl.acm.org/doi/10.1145/3658644.3670386>,
  PDF: <https://www.jianjunchen.com/p/inbox-invasion.CCS24.pdf>
- **CVE-2024-39929** — Exim RFC 2231 MIME parsing bypass,
  <https://censys.com/advisory/cve-2024-39929>
- RFC 5321 (SMTP), RFC 5322 (IMF), RFC 2045–2049 (MIME), RFC 2231 (Parameter Continuations)

## Walks That Produced This Scenario

- Walk #3 (3-node L4): `imf_5322 → mime → smtp_5321`
- Walk #6 (2-node L4): `imf_5322 → mime`
