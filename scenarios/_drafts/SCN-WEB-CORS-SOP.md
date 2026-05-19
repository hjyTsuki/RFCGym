# SCN-WEB-CORS-SOP: CORS misconfiguration breaks Same-Origin Policy

> Draft auto-generated from upstream walk #7 (seed=11), absorbing walk #2
> (sop standalone). Reviewed against published literature.

## Scenario Summary

CORS (Cross-Origin Resource Sharing) is the standardized mechanism for
opting out of the browser's Same-Origin Policy (SOP). A web service that
misconfigures the CORS handshake can implicitly turn off SOP for sensitive
endpoints — typically by reflecting the request `Origin:` header verbatim
while also asserting `Access-Control-Allow-Credentials: true`. The result
is that any malicious website can perform authenticated cross-origin
requests to the victim's API.

## Bug Layer

**L4 — cross-protocol composition**

| Protocol | Role |
|---|---|
| HTTP/1.1 (RFC 9110) | Carries `Origin`, `Access-Control-*` headers |
| CORS (W3C / Fetch spec) | Defines how server opts in to cross-origin access |
| SOP (HTML / web platform) | Default browser policy that CORS *opts out of* |

The "shared object" is the **trust boundary** between origins. CORS lets
the server widen it; misconfiguration widens it to "everyone with
credentials".

## Protocols In Scope

- HTTP/1.1 (RFC 9110, RFC 9112)
- CORS (Fetch Living Standard)
- Same-Origin Policy (HTML Living Standard + Fetch)

## Vendor Matrix (suggested)

```yaml
server_libraries:
  - {name: rs-cors, version: 1.3.0, type: open-source, role: cors_middleware,
     note: "The Go library with CVE-2018-20744"}
  - {name: rs-cors, version: latest, type: open-source, role: cors_middleware,
     note: "Patched baseline for negative-control"}
  - {name: nginx, version: 1.27, type: open-source, role: reverse_proxy,
     note: "When CORS is hand-rolled in nginx.conf"}
  - {name: expressjs-cors, version: latest, type: open-source, role: cors_middleware}
browser:
  - {name: chromium, version: latest, type: open-source, role: client}
  - {name: firefox, version: latest, type: open-source, role: client}
```

## Known Attacks (Ground Truth)

| ID | Mechanism | Reference |
|---|---|---|
| A1 | Wildcard "*" silently converted to origin-reflection by library; attacker site issues credentialed XHR | **CVE-2018-20744** (Go rs/cors) |
| A2 | Server reflects arbitrary `Origin:` because of regex-with-no-anchor (e.g. `evil.attacker.com.victim.com`) | Common pattern; multiple CVEs |
| A3 | `null` origin accepted (e.g. `Origin: null` from sandboxed iframe or file:// page) | EnableSecurity / Cure53 reports |
| A4 | Pre-flight bypass: server treats simple requests as exempt, attacker exfils via `<form>` POST | OWASP CORS docs |

## Ambiguity Hints

- Anchoring of `Origin` regex (string-contains vs full-equality)
- Handling of `null` origin
- `Access-Control-Allow-Credentials: true` paired with broad allow-origin
- Pre-flight cache (`Access-Control-Max-Age`) abuse window
- Subdomain wildcard semantics (`*.victim.com` interaction with multi-level subdomains)
- HTTP/1.1 vs HTTP/2 header normalization differences (separate sub-scenario)

## References

- **CVE-2018-20744** — Origin reflection in Go rs/cors,
  <https://nvd.nist.gov/vuln/detail/CVE-2018-20744>,
  <https://vuldb.com/?id.130274>
- W3C Fetch Living Standard (CORS): <https://fetch.spec.whatwg.org/>
- HTML Living Standard (SOP): <https://html.spec.whatwg.org/multipage/origin.html>
- OWASP CORS OriginHeaderScrutiny:
  <https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html>

## Walks That Produced This Scenario

- Walk #7 (3-node L4): `http_1_1 → cors → sop`
- Walk #2 absorbed (standalone `sop` — reclassified from L1 to L4 with this scenario)
