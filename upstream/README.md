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

- v1 (current): hand-curated seed graph (~25 nodes), random walker, prompt
  builder. **No LLM call yet — outputs prompt strings only.**
- v2: integrate Claude/OpenAI SDK; auto-generate SCN-*.md drafts; dedup against
  existing `scenarios/`.
- v3: Wireshark dissector C-source extraction (`data_sources/wireshark_extract.py`)
  to auto-populate edges from real dispatch tables.
- v4: Weighting edges by real-world prevalence (W3Techs CDN share, Wireshark
  pcap statistics).
