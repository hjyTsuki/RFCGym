# RFCGym Upstream — Protocol Graph Sampling

Produces SCN-*.md drafts by random-walking a curated protocol graph and asking
an LLM to confirm whether the sampled combination is a plausible
protocol-dependent attack scenario.

## Quick start

```bash
pip install networkx pyyaml

# Graph stats
python -m upstream.cli stats

# Sample 5 random walks
python -m upstream.cli walk --n 5 --seed 42

# Dry-run: see the LLM prompt that would be sent for each walk
python -m upstream.cli walk --n 3 --seed 42 --print-prompt

# Save walks as JSON for batch synthesis
python -m upstream.cli walk --n 50 --output-dir ../scenarios/_drafts/
```

## Architecture

```
seed_graph.yaml
      │
      ▼
graph/builder.py ── networkx MultiDiGraph ──┐
                                            │
                                            ▼
                          sampler/random_walk.py
                                            │
                                  Walk(nodes, edges,
                                       candidate_bug_layer)
                                            │
                                            ▼
                        scenario_synthesizer/prompt.py
                                            │
                              [LLM judges: accept/reject + paper refs]
                                            │
                                            ▼
                            scenarios/SCN-*.md draft
                                            │
                                            ▼
                            pipeline/ (Stage 1 protocol_analyzer
                                       enriches the draft)
```

## Edge types and bug-layer mapping

| Edge | Inferred bug_layer when traversed |
|---|---|
| `version_of` | L2 (cross-version translation) |
| `translatable` | L2 if same family else L4 |
| `composes_with` | L4 (cross-protocol composition) |
| `embeds` | L4 (carrier vs payload disagreement) |
| `dispatches_to` | L4 (parser dispatch ambiguity) |
| `layered_on` | mostly L4 when combined with composes_with |

Walk of n=1 node → L1 candidate (spec-level review).

## Adding nodes / edges

Edit `data_sources/seed_graph.yaml`:

```yaml
nodes:
  - {id: my_protocol, family: foo, version: "1", rfc: [9999], layer: app, wire: text}

edges:
  - {from: my_protocol, to: tcp, type: layered_on}
```

The builder validates that every edge references known nodes.

## Roadmap

- v1: hand-curated seed graph (~25 nodes), random walker, prompt builder.
  **No LLM call yet — outputs prompt strings only.** ✓
- v2: two-stage walker with `version_of` constraint + canonical marking;
  refined family taxonomy. ✓
- v3: RFC index crawler + Wireshark dissector scanner + merge tool to expand
  the seed graph automatically. ✓ (this iteration)
- v4: integrate Claude/OpenAI SDK; auto-generate SCN-*.md drafts; dedup against
  existing `scenarios/`.

## Data Source Crawlers (v3)

### RFC Editor index

```bash
# Download + parse the full RFC Editor catalog
python -m upstream.data_sources.rfc_index_crawler
# Output: data_sources/rfc_catalog.json  (~9700 RFCs, ~4500 protocol candidates)

# Force refresh from network
python -m upstream.data_sources.rfc_index_crawler --refresh

# Keep only protocol-likely entries
python -m upstream.data_sources.rfc_index_crawler --filter-protocols --output protocols.json
```

Heuristic flag `is_protocol_candidate` per entry uses status (INTERNET
STANDARD / PROPOSED STANDARD) + keyword vocabulary (`protocol`, `transport`,
`handshake`, etc.) + protocol-name regex (`http|tls|dns|smtp|...`).
Conservative; LLM should confirm before promotion.

### Wireshark dissector source scan

Wireshark ships 1500+ protocol dissectors as C source; each registers ports,
sub-dissector dispatch, and friendly names. Parsing them gives us edges
(`A dispatches_to B`) the RFC corpus does not.

```bash
# Clone Wireshark source somewhere (shallow is fine)
git clone --depth=1 https://gitlab.com/wireshark/wireshark.git /tmp/ws

# Scan dissectors
python -m upstream.data_sources.wireshark_dissector_scan \
    --src /tmp/ws/epan/dissectors \
    --output upstream/data_sources/wireshark_relations.json
```

Output structure: per-protocol record with `ports`, `media_types`,
`dispatches_to`, `dispatched_from`, `registered_aliases`.

### Merge to graph candidates

```bash
python -m upstream.data_sources.merge
# Reads:  rfc_catalog.json + wireshark_relations.json
# Writes:
#   graph_candidates_nodes.json
#   graph_candidates_edges.json
#   merge_report.md  (review summary)
```

Merger maps RFC titles to families via a hand-curated regex table
(`FAMILY_HINTS` in `merge.py`); add patterns there to cover new families.

**Important**: merge output is candidate-only. Promotion to `seed_graph.yaml`
is a manual decision — review `merge_report.md`, optionally LLM-classify
borderline cases, then edit YAML.
