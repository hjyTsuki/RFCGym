# SCN-SPF-DESIGN: SPF spec-level design flaws

> Draft auto-generated from upstream walk #9 (seed=11). One of two L1
> scenarios that survived human review; reviewed against published
> literature and RFC 7208 text itself.

## Scenario Summary

Sender Policy Framework (RFC 7208) is the most widely deployed sender
authentication protocol but its **specification itself** contains design
choices that lead to systemic abuse classes — independent of any
implementation. Studying SPF in isolation is enough to enumerate these
flaws.

## Bug Layer

**L1 — spec-level design flaw**

Read RFC 7208 alone and the following ambiguities / footguns are visible:

1. **10-DNS-lookup limit**: the spec mandates this limit but does not
   tell verifiers how to behave when it is hit. Many treat over-limit
   as `permerror`, which silently disables the policy.
2. **Macro language**: `%{i}/%{s}/%{h}` macros let a record encode the
   client IP and HELO host into DNS queries — a covert channel by design.
3. **Envelope-vs-displayed sender split**: SPF authenticates the SMTP
   envelope `MAIL FROM`, not the `From:` header that users see. The RFC
   is explicit about this gap — it is a *design* choice that compositions
   (DMARC, displayed-From) must paper over.
4. **`+all` qualifier**: technically legal SPF; means "any IP may send
   for this domain". Common in misconfigurations and there is no spec
   mechanism to warn the user.

## Protocols In Scope

Just one: **SPF (RFC 7208)**. This scenario does NOT depend on any
specific MTA implementation or any other protocol — it is a pure
spec-review case.

## Vendor Matrix

```yaml
# No vendor stack needed for L1 spec-review.
# The "implementation under test" is the RFC text itself.
auditors:
  - {name: human_expert, role: spec_reviewer}
  - {name: claude_opus_4_5, role: llm_reviewer, type: blackbox-api}
```

## Known Attacks (Ground Truth)

| ID | Mechanism | Reference |
|---|---|---|
| A1 | DNS-lookup-limit exhaustion via crafted `include:` chain → policy bypass | RFC 7208 §4.6.4 (states the limit but not the fail-open default) |
| A2 | Macro abuse for low-bandwidth covert channel from receiver to attacker DNS | RFC 7208 §7 (macros defined; abuse documented in academic papers) |
| A3 | `+all` deployment, common in cloud/CDN onboarding | Internet-wide measurement papers (Hu & Wang 2018 etc.) |
| A4 | Envelope/From decoupling enabling spoofing of high-trust display names | Composition Kills §3 (USENIX'20) — this is the *reason* DMARC exists |

## Ambiguity / Discussion Hints for the Analyzer

- Should `permerror` be fail-open or fail-closed? RFC leaves it implementation-defined → impl-variance is downstream, but the **choice itself** is L1.
- Macro language was a controversial inclusion in RFC 7208 design discussions; tracker discussion still relevant.
- The SPF / DMARC / DKIM stack as a whole was designed *because* SPF alone has these gaps. Documenting why SPF alone is insufficient *is* an L1 finding.

## References

- **RFC 7208** — Sender Policy Framework v1,
  <https://www.rfc-editor.org/rfc/rfc7208>
- **Composition Kills** — provides the canonical writeup of why SPF
  alone cannot authenticate the displayed sender:
  <https://www.usenix.org/conference/usenixsecurity20/presentation/chen-jianjun>
- Academic measurement papers on SPF deployment (multiple)

## Walks That Produced This Scenario

- Walk #9 (single-node L1): `spf`

## Note on L1 Scope

The Stage-1 Protocol Analyzer should treat L1 scenarios differently from
L2/L4: the deliverable is primarily a written argument (in `public.md`)
backed by RFC citations + measurement evidence, not a Docker stack. The
Attack Verifier phase may still construct an environment that exercises
A1 (DNS lookup limit) and A3 (`+all`) as **demonstration tests**, but the
core deliverable is the design-flaw argument itself.
