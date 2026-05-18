# RFCGym Pipeline — Diff vs Upstream CVE-Factory

This document records every change made to the CVE-Factory fork to repurpose
it as the RFCGym protocol vulnerability hunting pipeline.

Upstream reference: `D:/Research/Code/RFCGym/CVE-Factory/` (untouched clone).

---

## 1. Conceptual Shift

| Dimension | CVE-Factory | RFCGym |
|---|---|---|
| Input | `CVE-XXXX-NNNNN.md` | `SCN-{protocol}-{slug}.md` (under `scenarios/`) |
| Goal | Reproduce a known CVE for fix evaluation | Build env for new-bug discovery |
| Pipeline output | `Dockerfile + tests + solution.sh` | `Dockerfile + service-alive + known-attacks tests` (no solution) |
| Vuln test assertion | `test_vuln` FAILs in vulnerable state | `test_known_attacks` PASSes when attack succeeds (inverted) |
| Strong oracle | "func PASS + vuln FAIL" then "vuln PASS after fix" | "service alive + ≥1 known attack reproduces" |
| Solver phase | Yes (validates solution.sh) | **Removed** |
| Runtime evaluation | None (CVE is fully reproduced) | New: Fuzzer agent runs in built env |
| Bug-layer awareness | None | L1/L2/L3 classification in PhaseDefinition + scenario |

---

## 2. File-by-file changes

### `orchestrator/tool_controller.py`

- **AgentType enum renamed** (preserves `CHANGER`/`COMPARER`/`EXPERT`):
  - `ANALYZER` → `PROTOCOL_ANALYZER`
  - `GENERATOR` → `SCENARIO_GENERATOR`
  - `BUILDER` → `SCENARIO_BUILDER`
  - `VALIDATOR` → `ATTACK_VERIFIER`
  - `CHECKER` → `ENV_FINALIZER`
  - `JUDGER` → `POC_JUDGER`
  - `SOLVER` → **removed**
  - **New**: `FUZZER`, `KNOWN_ATTACKER`
- **DISALLOWED_TOOLS** rewritten to match new agent set; `FUZZER` is denied
  `WebSearch`/`WebFetch` (anti-cheat); `POC_JUDGER` keeps web access for
  novelty checks against CVE DBs.
- **DANGEROUS_COMMAND_PATTERNS** appended: outbound network requests to
  non-private IPs are blocked (RFCGym tests must stay within Docker network).

### `orchestrator/models.py`

- **PhaseDefinition** dataclass gains two fields:
  - `bug_layer: Optional[str]` — L1/L2/L3
  - `requires_known_attacks: bool` — gate for attack_verifier
- **PHASE_DEFINITIONS** fully rewritten:
  - `analyzer` → `protocol_analyzer` (output files renamed; adds
    `vendor_matrix.md` + `known_attacks.yaml`)
  - `generator` → `scenario_generator` (output `tests/test_service_alive.py` +
    `tests/test_known_attacks.py`, **no `solution.sh`**)
  - `builder` → `scenario_builder` (output_dirs adds `pcaps/`)
  - `validator` → `attack_verifier`
  - `solver` → **removed**
  - `checker` → `env_finalizer`
  - `judger` → `poc_judger` (new output: `poc_scores.json` + `poc_report.md`)
  - **New phases**: `fuzzer` (runtime eval) + `poc_judger`
  - Retained as-is (string keys preserved): `changer`, `comparer`, `expert`

### `orchestrator/async_orchestrator.py`

- `_prepare_initial_message()` dictionary fully rewritten for the new
  AgentType set; messages now reference protocol/scenario semantics, not CVE
  reproduction.
- `process_cve()` main pipeline:
  - Phase 1–3 keys → `protocol_analyzer`, `scenario_generator`, `scenario_builder`
  - `vulnerable_verification` → `attack_verification` (same method body,
    repurposed; was `_run_phase_vulnerable_verification`)
  - **Phase 5 `solution_verification` removed** (no fix loop)
  - `cve_check` → `env_finalize`
- `run_phase2_remaining()` mirrors the same changes.
- `_run_agent('validator', ...)` → `_run_agent('attack_verifier', ...)`
- `_run_agent('checker', ...)` → `_run_agent('env_finalizer', ...)`
- `_run_agent('judger', ...)` → `_run_agent('poc_judger', ...)`
- `run_judger()` → `run_poc_judger()` (and helper inner function)
- **Not removed (dead code, retained for now)**:
  - `_run_phase_solution_verification()` — never called; will be deleted once
    the rest of the pipeline is validated. Keeping it makes the diff easier
    to read.
  - `_format_solution_results_for_agent()` — same as above.
  - `run_expert()` + `_run_phase_expert_verification()` — retained because
    `expert` phase key is still in PHASE_DEFINITIONS (lightly repurposed).

### `orchestrator/file_access_controller.py`

- `ACCESS_RULES` fully rewritten:
  - `SCENARIO_BUILDER` now also denies `.agent_state/analyzer_output/known_attacks.yaml`
    and `for_attack_verifier.md` (blind building extended to cover oracle files).
  - **`FUZZER`** has hard denylist for oracle/hint files; can only write to
    `pocs/`, `fuzz_results/`, `.agent_state/fuzzer_output/`, and `pcaps/`.
  - **`POC_JUDGER`** read-only across the working dir; writes only its own
    output.
  - **`KNOWN_ATTACKER`** writes only verifier output + pcaps.

### `orchestrator/run.py`

- `process_judger()` now calls `orchestrator.run_poc_judger()` instead of
  `run_judger()`.

### `agents/*.md`

- Renamed files (prefix prepended to each, original content largely retained):
  - `analyzer.md` → `protocol_analyzer.md`
  - `generator.md` → `scenario_generator.md`
  - `builder.md` → `scenario_builder.md` (+ `builder_with_proxy.md` → `scenario_builder_with_proxy.md`)
  - `validator.md` → `attack_verifier.md` (+ proxy variant)
  - `checker.md` → `env_finalizer.md` (+ proxy variant)
  - `judger.md` → `poc_judger.md`
- **Deleted**: `solver.md`, `solver_with_proxy.md`
- **New file**: `fuzzer.md` — complete prompt for the runtime evaluation
  target. Defines two-stage protocol (hypothesize → probe), POC archive
  format, anti-cheat reminders.
- Each renamed prompt now opens with a `> RFCGym Variant — Read This First`
  block documenting the new output contract (file names, no `solution.sh`,
  inverted assertion semantics, etc.). The bulk of the original CVE-Factory
  guidance below the prefix remains because most of it (Docker hygiene,
  feedback XML format, etc.) is reusable.

### `config.yaml`

- `agents.prompts`, `agents.limits`, `agents.timeouts`, `models.agent_models`
  all rewritten to use the new agent names.
- **New section `protocol_fuzzing`**:
  - `scenarios_dir: ../scenarios`
  - `bug_layers` enumerates L1/L2/L3 with descriptive names
  - `attack_oracle.require_known_attack_for_l{1,2,3}` — L2/L3 default to True
    (strong oracle); L1 default False
  - `fuzz_runtime`: `max_tests_per_ambiguity=1000`, `time_budget_seconds=7200`,
    `two_stage_mode=true`, `hide_hints_from_fuzzer=true`
  - `poc_judging.oracle_mode` per-layer: L1/L2 use rules, L3 uses LLM + human
  - `poc_judging.novelty_check`: optional CVE DB cross-reference

---

## 3. New inputs

### `scenarios/SCN-HTTP3-CDN-RANGE.md`

First gold-standard scenario, transcribed from H3Act §5.1.1 (Range header
amplification). Contains protocol list, bug layer, vendor matrix, topology,
known attacks, ambiguity hints, references.

### `scenarios/SCN-HTTP3-CDN-RANGE.known_attacks.yaml`

Human-curated reference oracle for SCN-HTTP3-CDN-RANGE. Defines four attack
variants (Removal, 4K Expansion, 512K Expansion, 1M Expansion) each with:
- Trigger spec (method/path/headers)
- Wire-level expected effect (origin headers, status, amplification ratio
  bounds)
- Vendor affected list (from H3Act paper table)
- Evidence checks

`verifier_pass_threshold.min_attacks_reproduced=1` defines environment
readiness.

---

## 4. What still works as in CVE-Factory

- The Multi-Agent SDK session model (`agent_runner.py`)
- The XML feedback protocol (`feedback_processor.py`) — schema unchanged
- The FileStateManager + responsible-agent routing
- ScriptExecutor (Docker build/start/stop/test) — methods are reusable; we
  may add `verify_service_alive` and `run_known_attacks` later
- The phase concurrency model (sliding window + per-agent semaphores)
- The `.logs/` persistence + `--phase1`/`--phase2` split execution
- DinD requirement (RFCGym needs DinD too — possibly even more so since
  Fuzzer agents may produce malicious traffic)

---

## 5. Known dead code (to clean up later)

- `_run_phase_solution_verification` and its helpers — orphaned but harmless
- Some references in `_run_phase_check` (mostly Checker → ENV_FINALIZER naming)
  may still mention `cve_check` strings in log messages — cosmetic only

---

## 6. Still to do (not in P0)

| Item | Owner | Status |
|---|---|---|
| `scripts/check_scenario_ready.py` (replace check_cve_ready.py) | TBD | not started |
| `scripts/run_fuzz_eval.py` | TBD | not started |
| `script_executor.py` — `verify_service_alive` + `run_known_attacks` | TBD | not started |
| `protocol_graph_walker.py` (Scaling path A) | TBD | not started |
| `paper_to_scenario.py` (Scaling path B) | TBD | not started |
| Real validation: run pipeline on `SCN-HTTP3-CDN-RANGE` end-to-end | TBD | not started |

---

## 7. Stage-1 review tightening (post-initial)

After the first walkthrough of Stage 1, the following adjustments were made to
strictly scope this pipeline to **environment construction only**:

### `agents/protocol_analyzer.md`

- Added an **"Authoritative Sources"** section pinning the canonical protocol
  source to <https://www.rfc-editor.org/> (index:
  <https://www.rfc-editor.org/rfc-index.html>). RFCs must be cited by number +
  section, not by URL alone.
- Output file count dropped from 7 → 6: **removed `for_fuzzer.md`** because
  the fuzzer / POC judging are evaluation-stage concerns and out of scope for
  the env construction pipeline.

### `orchestrator/models.py`

- `PHASE_DEFINITIONS['protocol_analyzer'].required_files` no longer lists
  `for_fuzzer.md`.

### `orchestrator/async_orchestrator.py`

- `_prepare_initial_message` for `PROTOCOL_ANALYZER` updated:
  - References rfc-editor.org as canonical source
  - Lists 6 output files (no for_fuzzer.md)
  - Adds explicit "evaluation/fuzzing is out of scope" framing
- `run_phase2_remaining` no longer calls `_run_phase('poc_judger', ...)` after
  cleanup. The construction pipeline now terminates at cleanup.
- Phase definitions for `fuzzer` and `poc_judger` are **retained** in
  `PHASE_DEFINITIONS` (still loadable as standalone agents), but they are no
  longer wired into the construction flow.

### Net effect

```
Construction pipeline (this codebase):
  protocol_analyzer -> scenario_generator -> scenario_builder
                    -> attack_verification -> env_finalize -> cleanup
                    [STOP]

Evaluation pipeline (separate, NOT yet implemented):
  fuzzer (runtime) -> poc_judger
```

---

## 8. How to roll back

Original CVE-Factory is at `D:/Research/Code/RFCGym/CVE-Factory/` untouched.
To start over: `rm -rf pipeline/ && cp -r CVE-Factory/orchestrator pipeline/orchestrator`
(and same for `agents/`, `config.yaml`, `scripts/`).

## 9. Bug-layer taxonomy v3 — four flat layers (final)

After two iterations on the taxonomy, the final scheme is **four flat layers**,
distinguished by *what protocol structure causes the bug*:

| Layer | Trigger | # Protocols | Attack method |
|:---:|---|:---:|---|
| **L1** | RFC itself is ambiguous/flawed | 1 (spec only) | Formal analysis, paper-style |
| **L2** | Translation between protocol versions | 1 (multi-version) | Diff translated wire output |
| **L3** | Vendor implementations disagree on one spec | 1 (multi-vendor, same version) | Diff parallel implementations |
| **L4** | Multiple protocols disagree on same object | ≥2 | Trust-chain analysis, identity decoupling |

**Out of scope** (analyzer emits `<status>error</status>`):
single-implementation memory/race bugs without protocol-structural cause.

**Litmus questions** (first YES wins):
1. Find bug by reading only RFC? → L1
2. Translation boundary between versions? → L2
3. Same (protocol, version) two vendors diverge? → L3
4. Two distinct protocols disagree on same object? → L4
5. Otherwise → OUT OF SCOPE

**L4-only requirement**: must produce
`.agent_state/analyzer_output/trust_graph.yaml` describing which component
trusts which field. L1/L2/L3 may omit this file.

### Files updated for v3

- `config.yaml::protocol_fuzzing.bug_layers` — four flat layers with
  structured metadata (`name`, `protocols_involved`, `bug_location`,
  `attack_method`, `description`, `examples`); L4 has
  `requires_trust_graph: true`. `attack_oracle` and `poc_judging.oracle_mode`
  enumerate all four layers.
- `agents/protocol_analyzer.md` — four-layer classification table, 5-step
  litmus, worked examples per layer, optional `trust_graph.yaml` schema for L4.
- `orchestrator/models.py` — `PhaseDefinition.bug_layer` doc-comment lists
  `L1 / L2 / L3 / L4` with per-layer attack methods.
- `scenarios/SCN-HTTP3-CDN-RANGE.md` — `Bug Layer: L2` (was `L2a`); body
  now distinguishes when the same scenario would be L3 (no translation, just
  vendor variance) vs L4 (cross-protocol).
- `scenarios/SCN-HTTP3-CDN-RANGE.known_attacks.yaml` — `bug_layer: L2`.

### Why four (not the previous two)

Each layer has a **fundamentally different attack methodology and oracle
shape**:

- L1: textual reasoning (can't be auto-fuzzed)
- L2/L3: wire-level differential testing (similar mechanism, different
  source of divergence — version translation vs vendor variance)
- L4: semantic identity decoupling (requires trust-graph modeling)

Folding L2/L3 into a single layer (the v2 attempt) was algorithmically
tempting but **muddied the Generator's oracle template** — version
translation produces a clear "input → translated output" trace, vendor
variance produces "N parallel outputs, diff them." Different oracle code.

---

## 10. Stage-1 final output contract (post-tightening)

For reviewers: this is the locked contract for the Protocol Analyzer phase.

| Output | Consumer | Role |
|---|---|---|
| `public.md` | All downstream | Protocol overview, RFC anchors, bug_layer |
| `for_scenario_generator.md` | Scenario Generator | Test strategy hints |
| `for_scenario_builder.md` | Scenario Builder | Docker stack requirements |
| `for_attack_verifier.md` | Attack Verifier | Per-attack expected wire effects |
| `vendor_matrix.md` | All downstream | `[{name, version, type, role}, ...]` |
| `known_attacks.yaml` | Attack Verifier (strong oracle) | Machine-readable attack specs |

Removed from contract: `for_fuzzer.md` — belongs to the evaluation pipeline.
