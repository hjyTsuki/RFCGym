# SCN-EMAIL-AUTH-CHAIN: SPF / DKIM / DMARC / ARC sender authentication composition

> Draft auto-generated from upstream walks #1 + #4 + #8 (seed=11). Reviewed
> by human against published literature.

## Scenario Summary

Email sender authentication is built from four independent protocols that
each authenticate a *different* notion of "sender". An attacker constructs
a message where each individual check passes, yet the displayed sender
(`From:` header) is forged.

## Bug Layer

**L4 — cross-protocol composition**

The four protocols layer over SMTP and disagree on which field identifies
the sender:

| Protocol | Authenticates | Field consulted |
|---|---|---|
| SPF (RFC 7208) | SMTP envelope sender | `MAIL FROM` / `Return-Path` |
| DKIM (RFC 6376) | a header set chosen by signer | DKIM `h=` selector list |
| DMARC (RFC 7489) | alignment of SPF/DKIM result with header | `From:` domain |
| ARC (RFC 8617) | trust chain across forwarders | accumulated `ARC-*` headers |

The "shared object" the system *assumes* is consistent is
"the sender of this email." Attacker decouples envelope sender / signed
From / displayed From / forwarder identity.

## Protocols In Scope

- SMTP 5321 (RFC 5321)
- IMF 5322 (RFC 5322) — provides the `From:` header
- SPF (RFC 7208)
- DKIM (RFC 6376)
- DMARC (RFC 7489)
- ARC (RFC 8617)

## Vendor Matrix (suggested)

```yaml
mta:
  - {name: postfix, version: 3.8, type: open-source, role: mta}
  - {name: exim, version: 4.98, type: open-source, role: mta}
mua_display:
  - {name: thunderbird, version: latest, type: open-source, role: mua}
  - {name: gmail-web, version: latest, type: blackbox-api, role: mua}
  - {name: outlook-web, version: latest, type: blackbox-api, role: mua}
auth_components:
  - {name: opendkim, version: 2.10, type: open-source, role: dkim_signer_verifier}
  - {name: opendmarc, version: 1.4, type: open-source, role: dmarc_verifier}
```

## Known Attacks (Ground Truth)

| ID | Mechanism | Reference |
|---|---|---|
| A1 | Displayed From ≠ DKIM-signed From ≠ SPF-authenticated Return-Path | Composition Kills (USENIX'20) |
| A2 | Multiple `From:` headers; verifier picks one, MUA shows another | Composition Kills §5 |
| A3 | SPF pass on shared multi-tenant IP; tenant boundary not enforced | **CVE-2024-7208** |
| A4 | Shared SPF record across tenants in multi-tenant hosting | **CVE-2024-7209** |
| A5 | SMTP smuggling: `<LF>.<CR><LF>` sequence injects a second message with forged envelope sender; SPF passes the legitimate envelope and the smuggled email | **CVE-2023-51764** (Postfix), **CVE-2023-51765** (Sendmail), **CVE-2023-51766** (Exim) |
| A6 | ARC chain forging in multi-hop forwarding scenario | Composition Kills §6 |

## Ambiguity Hints

- Multiple `From:` / `Sender:` / `Reply-To:` headers (RFC 5322 §3.6.2 allows)
- `Return-Path` and `From:` legitimately differ for forwarders (DMARC alignment edge case)
- DKIM `h=` selector list — signer may omit critical headers
- DKIM `l=` body-length tag → attacker appends after signed prefix
- ARC `oldest-pass` chain interpretation across MTAs
- SMTP smuggling end-of-data sequence variants: `<LF>.<CR><LF>`, `<CR><CR><LF>`, etc.

## Tool Reference

The Composition Kills authors released **espoofer**
(<https://github.com/chenjj/espoofer>) which scripts many of these attacks
and is a useful baseline test corpus for the Attack Verifier phase.

## References

- **Composition Kills** — Chen, Paxson, Jiang, USENIX Security 2020,
  <https://www.usenix.org/conference/usenixsecurity20/presentation/chen-jianjun>
- **SMTP Smuggling** — SEC Consult, CCC Congress 2023,
  <https://sec-consult.com/blog/detail/smtp-smuggling-spoofing-e-mails-worldwide/>
- CVE-2023-51764 / 51765 / 51766 (Postfix / Sendmail / Exim SMTP smuggling)
- CVE-2024-7208 / 7209 (multi-tenant SPF/DKIM bypass)
- RFC 7208 (SPF), RFC 6376 (DKIM), RFC 7489 (DMARC), RFC 8617 (ARC)

## Walks That Produced This Scenario

- Walk #1 (2-node L4): `smtp_5321 ↔ dkim`
- Walk #4 (3-node L4): `dkim → dmarc → arc`
- Walk #8 (2-node L4): `dkim ↔ dmarc`
