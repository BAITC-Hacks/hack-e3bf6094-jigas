# Second research wave: make priorities easier to investigate

23 September 2026. **Prioritized changes accepted by the team.** Implementation contract: [SPEC 1.4](SPEC.md), [PRODUCT_SPEC 1.3](PRODUCT_SPEC.md), [PLAN 1.3](PLAN.md). The measurements below retain their recorded code/input revision and are not release acceptance results.

Adopted: HA-17 (P0) for factual explanations, edge widths, secondary-pattern filters and tied priorities; then complete P1 pairs **HA-11 → HA-14 → HA-16 → HA-13** after HA-07.2 + HA-08.1 + HA-15.2 + HA-15.3 + HA-17.2. HA-15 is the separately accepted React migration; wave-two UI extends that frontend. Explanation tasks were renumbered to HA-17 to resolve concurrent card creation; their URLs are unchanged. HA-16 covers distinct-seed counts and bounded dated witnesses under strict/same-day conventions. Final HA-08.2 follows the chosen feature set. Node removal, amount attribution, a new score and a runtime assistant remain deferred experiments. Original research estimates and proposals below are a dated record; the current plan governs delivery.

**Recommendation:** finish and document the integrated release, make every role explanation show its actual rule, then prioritize the frontier review workflow and dated route evidence. Keep the current ranking as a disclosed baseline while collecting analyst feedback. These improvements directly support the track's question: **which client should the analyst examine next, and why?**

## 1. What was inspected

- The supplied [track brief](<../track_data/HackAlem AI_ Граф денег_ восстановление финансовой структуры организованной группы по транзакционной сети.md>), dataset README, and `Положение_HackAlem_AI.md` (§5.4–5.6).
- Current product/spec/plan, the [first comparison](BLUEPRINT_COMPARISON.md), implementation, README, demo and validation documents.
- Remote code at [d58551f](https://github.com/BAITC-Hacks/hack-e3bf6094-jigas/commit/d58551f03d12c819e141c4ccd88c70c176b396d1). During this review the team completed the integration wiring and moved functions into `hackalem/`. A final fetch at approximately 15:17 Astana found `6a06550`, which adds the package CLI and changes no analytical functions or HTML.
- Trello at approximately 15:15 Astana: https://trello.com/b/6tCEnwZQ/hackathon. Eight P0 subtasks were done; four were under review; integration, contract checks and README were in progress; the eight P1 subtasks remained in backlog. These are a dated board snapshot, not a prediction of current completion.
- Primary research and official documentation linked below. No customer or transaction data was sent to external research services.

The numerical results were calculated from the official ZIP using the implemented validation, graph, role, ranking and community functions. The probe and its evidence are [second_wave.py](../research/second_wave.py) and [second_wave_evidence.json](../research/second_wave_evidence.json). They record the code revision, source hashes, input hashes and dependency versions.

This review ran research calculations and their internal consistency checks. Product acceptance tests, Docker reproduction, browser behavior, analyst task times and AML accuracy were not measured by this research run.

## 2. What earns points and what is mandatory

The track's rubric is explicit:

| Criterion | Points | Practical implication |
|---|---:|---|
| Task fit and working scenario | 25 | Demonstrate raw Parquet → outputs → arbitrary client lookup |
| Technical implementation | 25 | Actual behavior must match the explanation and architecture |
| README and reproducibility | 25 | An expert must run and understand the submission independently |
| Value and applicability | 15 | Help an analyst choose and justify a next investigation |
| Development potential and originality | 10 | Demonstrate a defensible extension with useful evidence |

The first three categories account for **75/100 points**. This does not predict a jury score; it explains why finishing the release and explanation quality has high priority.

The five admission requirements are: one reproducible command; all 2,248 nodes with roles/scores/evidence; documented formal role criteria; complete cluster summaries; and at least 20 priorities with a searchable directed visualization. The three CSV schemas are fixed. `evidence` is limited to 200 characters. The jury can request explanations of three arbitrary IDs within a minute. Full calculation must take at most five minutes on the supplied dataset.

README must cover role thresholds, limitations and a textual plan for roughly one million nodes. Required artifacts also include the actual CSVs, an architecture diagram and a five-minute demonstration. The supplied regulations set 18:00 Astana as the final version cutoff and make independent startup an admission condition. The team's 16:30 freeze is an internal reserve for acceptance, not the official deadline.

An AI assistant is an optional track feature. The technical rubric mentions AI/agentic AI among implementation technologies but supplies no separate requirement for an LLM in the running product. The [event website](https://hackalem.ai/) calls for Codex use during development; the provided regulation copy phrases this differently. Preserve the actual development history and follow event instructions. Neither source establishes a requirement to add a runtime chatbot to this track.

## 3. Current implementation against the requirements

| Area | Observed state | Remaining work with direct submission value |
|---|---|---|
| Data and graph | Exact IDs and integer tiyn; reconciled transactions; isolates retained; directed features implemented | Preserve these contracts through integration |
| Roles and priorities | Six roles, secondary matches and score components implemented | Explain each matched rule in ordinary analyst language |
| Communities | Weighted projection, Louvain, connectivity repair, deterministic IDs and summaries implemented | Show observed group structure and disclose sensitivity |
| Export and HTML | CSV validation/export, embedded offline assets, graph/search/filters implemented | Confirm the complete user scenario on the submitted release |
| Pipeline | `hackalem.pipeline.run_pipeline` now wires all stages and stages output before publishing a success manifest | Record actual fresh-run, offline and timing results |
| Documentation | README, architecture and validation documents still describe a pre-integration draft at the inspected revision | Update to the actual package structure; include role rules, scale discussion and measured release facts |
| P1 | Daily features, structural overview and next-data requests are specified; their implementation is absent from the inspected code | Choose a small completed feature after P0 acceptance |

The earlier CLI at `5df99ba` still called `write_outputs` with a features table. That obsolete integration call was replaced in `d58551f`; it is **not an outstanding finding against the later revision**. The remaining distinction is between integrated source code and a release with recorded acceptance evidence.

The existing [pipeline](https://github.com/BAITC-Hacks/hack-e3bf6094-jigas/blob/d58551f03d12c819e141c4ccd88c70c176b396d1/hackalem/pipeline.py) already supports phase timings, input/output hashes and an environment manifest. Use those results in the handoff; a second reporting framework is unnecessary.

## 4. New measured findings

### 4.1 Role explanations omit the facts that triggered the role

The implemented `evidence` template lists `P`, `M`, `A`, `C`, `H`, volume, seed reach and betweenness for every role. It does not state the role's decisive condition. The HTML shows several raw metrics but does not expose a matched rule with its threshold. See [scoring.py](https://github.com/BAITC-Hacks/hack-e3bf6094-jigas/blob/d58551f03d12c819e141c4ccd88c70c176b396d1/hackalem/scoring.py#L247) and [the node card renderer](https://github.com/BAITC-Hacks/hack-e3bf6094-jigas/blob/d58551f03d12c819e141c4ccd88c70c176b396d1/report_template.html#L1239).

For example, the highest-ranked primary consolidator has **8 payers**, receives **2,160,500 KZT**, and sends **517,000 KZT**. Its current CSV explanation omits the eight payers and the `in_deg ≥ 3` rule. A short replacement could be:

> Признаки консолидации: 8 плательщиков при пороге ≥3; вход 2 160 500 KZT, выход 517 000 KZT. Входящие вне выборки неизвестны.

Generate these facts from the same fields and parameter object used for classification. In the card, show observed value, threshold, matched secondary roles and limitations; keep score arithmetic available as detail. For coordinator, include the calculated betweenness threshold and structural meaning. For transit, state the observed ratio and its limited coverage. For terminal, include depth and follow-up days.

This is aligned with NIST's principles that explanations be meaningful to their user and accurately reflect the process that produced the result. It also directly addresses the track's arbitrary-ID explanation test. [NISTIR 8312](https://nvlpubs.nist.gov/nistpubs/ir/2021/NIST.IR.8312.pdf)

### 4.2 Monthly reachability overstates the availability of chronological routes

The current `seed_reach_count` correctly measures directed connectivity over one to four edges in the monthly aggregate. We additionally counted whether each seed/target pair has a route using actual transaction dates, with the same four-transfer limit:

| Route convention | Seed/target pairs | Nodes reachable from any seed | Pairs involving current top-20 targets |
|---|---:|---:|---:|
| Monthly topology, ignoring dates | 4,219 | 2,198 | 77 |
| Dates may be equal or increase | 1,898 | 1,371 | 30 |
| Each next transfer is on a later day | 1,623 | 1,220 | 27 |

Even allowing equal dates, **55.0% of the static seed/target pairs have no date-compatible route under these definitions**. Eighteen of the current top 20 have fewer date-compatible seed connections; three have none even when same-day steps are allowed. For example, the non-seed account `100000002957787100` has four static seed connections and zero date-compatible ones under either convention.

These counts do not establish false positives or disprove structural relevance. They show that monthly connectivity cannot serve as evidence that money flowed from a particular seed to a target in chronological order. Missing transfers, month boundaries and the four-hop limit still matter. Temporal-network research explains why aggregation can preserve links while losing the ordering required for a route. [Holme and Saramäki, Temporal Networks](https://arxiv.org/abs/1108.1780)

**Recommended feature:** in a node card, distinguish direct seed payers, monthly seed connectivity and routes compatible with transaction dates. Let the analyst open one bounded route with dates and amounts; if only same-day ordering makes it possible, say so. An absent route should have an explicit empty state.

The probe allows unlimited waiting within July; it does not implement the separate 1–2-day pass-through hypothesis. It checks route existence, with no amount attribution, opening balance or consumption of money. A compatible route is therefore evidence of possible chronological connectivity, not proof that the same funds traversed it. Keep the production score unchanged during this first addition.

### 4.3 The frontier workflow deserves earlier implementation

There are **444 frontier nodes**, receiving **56,672,164.68 KZT** in the observed extract. None is in the current global top 20. The largest 20 frontier recipients account for **23,441,698 KZT**, or **41.36%** of all observed frontier inflow; the largest five account for 16.74%.

The existing top table filters only the global top 20. The graph/search can find other nodes, but the UI does not provide a ranked frontier worklist. An analyst must discover these accounts separately.

This strengthens the case for **HA-14 before the full HA-13 overview**: a table of all frontier recipients ordered by observed inflow, with the reason and a concrete request for the next outgoing layer. This list expresses where more data would be useful; it does not estimate hidden outflow or replace the global priority score.

HA-14's complete request rules need HA-11 daily profiles for same-day ambiguity, but they do not require SCC or community-flow aggregation. A boundary request itself already has the necessary depth/degree fields. A proposed revised P1 order is **HA-11 → HA-14 → HA-13**, subject to the P0 gate and available acceptance time.

### 4.4 Secondary roles and ties need clearer presentation

The current top 20 contains **18 primary coordinators and 2 primary distributors**, while **all 20 also match consolidator**. Thus filtering the top table by the primary role `consolidator` produces no rows despite consolidation being present in every selected account. This is a consequence of documented role precedence, not missing role computation.

Keep primary-role filtering explicit. Add a clearly labelled “matches this pattern” option using the existing `matched_roles`, or make the secondary chips actionable. It would help the analyst follow the track's consolidation question without changing classification.

The top 20 has **12 distinct exact scores**. Three nodes share the cutoff score and one enters through the deterministic numeric-ID tie break. Mark equal priority and allow exploration of peers; a lower numeric identifier is not a stronger analytical reason.

Changing one weight at a time by ±20%, then renormalizing all weights, retains **17–20 of the original 20 nodes** across eight variants. The volume-only top 20 overlaps by **9 nodes**. These are ranking diagnostics, not usefulness or accuracy estimates. Role thresholds were not perturbed. This evidence supports retaining the baseline while improving how the list is explained.

### 4.5 Every edge is assigned the same visual width

The [edge renderer](https://github.com/BAITC-Hacks/hack-e3bf6094-jigas/blob/d58551f03d12c819e141c4ccd88c70c176b396d1/report_template.html#L1036) uses:

```text
width = max(1.25, min(4.5, log10(abs(sum_tiyn) + 1)))
```

The smallest edge is 5,000 KZT = 500,000 tiyn. Its logarithm already exceeds the 4.5 cap. Consequently **all 3,119 edges get width 4.5**, although edge sums range from 5,000 to 4,400,000 KZT. This is a calculation from the code and dataset, not a browser screenshot finding.

Use a normalized scale over the dataset and retain exact amounts in the table/tooltips. Existing vis-network also supports native `value`/`scaling`; if using automatic scaling per displayed subset, disclose that visual comparison is relative to the current view. A fixed global normalization avoids that change of meaning. [Official edge scaling documentation](https://visjs.github.io/vis-network/docs/network/edges.html)

### 4.6 Connectivity repair is useful; community membership is sensitive

The production settings produce **89 groups: 70 groups among nonisolated nodes and 19 isolated singletons**. Eight groups contain multiple seeds. This is consistent with the task's remark about eight multi-seed communities; it does not imply only eight communities in total. Likewise the full graph has 35 weak components, of which 16 contain edges and 19 are isolates.

At production settings, raw Louvain returns **one disconnected community** before the existing split into connected components. Preserve that repair. The possibility of disconnected Louvain output is documented in the original Leiden research; adopting another dependency is not necessary to retain the protection already implemented. [Traag, Waltman and van Eck](https://arxiv.org/abs/1810.08473)

Across resolutions 0.8, 1.0 and 1.2 and random seeds 0, 1, 2 and 42, the repaired output has **64–79 nonisolated groups**. Adjusted Rand agreement with production ranges from **0.747 to 1.000**, with 1.000 being the identical baseline. Holding resolution at 1.0 and changing the random seed gives **0.895–0.938** agreement. Isolates are excluded from this comparison.

Deterministic IDs make a fixed calculation reproducible; they do not make membership certain. Show groups as an exploratory partition. HA-13's overview remains useful, but it should not present cluster borders or the largest SCC as an established organization.

### 4.7 A topology experiment can support the optional resilience feature

We removed fixed top-20 sets and counted surviving seed/target pairs still connected within four directed hops. Each denominator excludes pairs whose seed or target was itself removed.

| Removed set | Eligible pairs before removal | Pairs after removal | Pair loss | Largest remaining weak component |
|---|---:|---:|---:|---:|
| Current priority top 20 | 2,293 | 1,076 | 53.07% | 1,173 nodes |
| Observed volume top 20 | 2,374 | 1,232 | 48.10% | 1,376 nodes |
| Betweenness top 20 | 2,375 | 1,484 | 37.52% | 1,257 nodes |

Thirty uniformly random 20-node removals, seed 42, yield a median pair loss of **0.70%**, with a range of 0.048–8.58%. This is a descriptive reference; it is not a degree-matched control or a significance test. The methods remove different seed sets and have different denominators, so these percentages do not establish a universally superior ranking.

The result gives the optional resilience demo a concrete calculation: show before/after connectivity, the removed nodes and the definition. It measures the observed graph under fixed deletion. It does not estimate money prevented, criminal-network resilience, alternate banks or adaptive rerouting. Keep this below explanation, frontier and dated-route work in the implementation order.

## 5. Recommended changes and task mapping

Implementation estimates below are rough planning ranges for a developer familiar with the repository, **excluding review**. They are not measured delivery times or a commitment to fit every item before freeze.

| Order | Change | Why now | Existing task / current code area | Rough effort |
|---:|---|---|---|---:|
| 1 | Accept and record the complete release; finish README role rules, scale discussion and source attribution | Required scenario and reproducibility carry substantial rubric weight | HA-07.2, HA-08, HA-09, HA-10; `hackalem/pipeline.py`, README, validation/demo docs | 30–60 min |
| 2 | Role-specific evidence and matched-rule display | Directly supports explaining arbitrary IDs; no new analytics library | Accepted HA-17.1/15.2, follow-up to HA-03.2 / HA-06; `hackalem/scoring.py`, `report_template.html` | 20–35 min |
| 3 | Normalize edge widths; clarify secondary-pattern filtering and ties | Makes existing information visible and interpretable | Accepted HA-17.2; existing HTML | 10–20 min |
| 4 | Complete HA-11 and bring HA-14 forward | Frontier concentration gives a concrete investigation list and next action | HA-11 → HA-14; `hackalem/graph.py`, `hackalem/report.py`, pipeline, HTML | Use existing pair estimates |
| 5 | Add a route with dates to the selected-node explanation | Quantified mismatch between monthly reach and date-compatible reach | Accepted HA-16 after HA-14; graph/report/HTML | 35–60 min |
| 6 | Community/SCC overview and membership sensitivity | Useful network orientation with disclosed uncertainty | HA-13 and HA-12.2 | Existing planned scope |
| 7 | Fixed top-N removal view | Directly addresses an optional track feature with measurable behavior | Proposed later experiment; reuse NetworkX and existing graph | 20–35 min |

Each selected addition should end with a usable display, consistent exports and its defined acceptance evidence. If P0 acceptance remains unfinished at the internal freeze, devote the remaining work to admission requirements.

Concrete acceptance criteria for the proposed changes:

1. **Explanations:** every role has a factual, bounded-length CSV explanation; three arbitrary IDs can be explained using actual feature values and thresholds. Include a frontier and an isolated node among the cases.
2. **Graph display:** unequal amounts can produce distinguishable widths, equal amounts have equal widths under the same scale, and filtering does not silently change a claimed global scale.
3. **Pattern filtering:** an account matching consolidator remains discoverable even when coordinator is its primary role. Display which role interpretation the filter uses.
4. **Frontier worklist:** include all 444 nodes in the provided dataset, including those outside the global top 20; reconcile sums; preserve global scores; state missing outgoing coverage.
5. **Dated route:** use actual directed transfers, at most four hops, and recorded dates. Show same-day ambiguity and the possibility that no route is available. No attribution of the same money without a separate amount-conserving model.
6. **Evaluation:** use the already planned HA-12 blind review. Judge whether an explanation is supported, whether the next request is useful, and how long the task takes. Record reviewer expertise and sample size.

The module refactor also changes task instructions: `starter.py` is now a compatibility facade. New calculations belong in `hackalem/graph.py` or `hackalem/scoring.py`; schema assembly in `hackalem/report.py`; orchestration in `hackalem/pipeline.py`; display in the existing HTML. Current ownership references are updated in PLAN §9 and SPEC §11.

## 6. What the external research adds

| Primary source | Implication for this project | Limit |
|---|---|---|
| [NIST, Four Principles of Explainable AI](https://nvlpubs.nist.gov/nistpubs/ir/2021/NIST.IR.8312.pdf) | Match explanations to analyst understanding and actual calculation | Does not validate our thresholds or role labels |
| [BIS / Bank of England, Project Hertha, 2025](https://www.bis.org/publications/project-hertha-identifying-financial-crime-patterns-real-time-retail-payment-systems.pdf), pp. 21–23 | Explanations, investigative feedback and labelled outcomes are central to improving network analytics | Uses a synthetic payment ecosystem; its reported detection improvements cannot be transferred to HackAlem |
| [Holme and Saramäki, Temporal Networks](https://arxiv.org/abs/1108.1780) | Route ordering deserves separate treatment from aggregated topology | Temporal connectivity alone provides no financial provenance |
| [Traag et al., Louvain to Leiden](https://arxiv.org/abs/1810.08473) | Check community connectivity and avoid treating one partition as certain | Algorithmic guarantees do not establish AML meaning |
| [NetworkX betweenness documentation](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html) | Exact all-pairs centrality can be replaced by explicit sampling when scale requires it | Runtime and ranking behavior need measurement on the larger input |
| [vis-network edge documentation](https://visjs.github.io/vis-network/docs/network/edges.html) | Existing rendering tools already support value-based widths | Define whether the scale is global or view-relative |

Hertha is especially relevant to the next product stage: it reports poor outcomes from unsupervised methods without labels and emphasizes feedback from investigations. For this hackathon, the practical first step is the planned blind review of explanations and worklists. A later outcome ledger should distinguish reviewer usefulness judgments from confirmed investigation outcomes. A learned classifier becomes defensible when suitable outcomes and an evaluation split exist.

For the required million-node discussion, describe columnar input/aggregation, bounded or sampled centrality, community/ego views and delivery of only the displayed subgraph. Retain exact amounts, IDs and explanation provenance. Do not claim that the current all-node HTML or exact Python betweenness has been demonstrated at that scale.

A runtime LLM, GNN, external AML enrichment and a replacement graph database currently add integration and evaluation work without evidence of incremental value on this extract. Revisit a narrowly scoped assistant once it can cite the same verified node/route facts and beat direct filters on an analyst task. The submission can already demonstrate original work through uncertainty-aware exploration and reproducible temporal diagnostics.

## 7. Reproduce the measurements

Dependencies are the repository's existing pandas 2.2.3, NumPy 2.2.6, NetworkX 3.6.1 and PyArrow 25.0.1. The recorded run used Python 3.13.3 on macOS arm64.

To reproduce the **recorded revision** from the current checkout, extract that commit without switching the working branch:

```sh
research_snapshot=$(mktemp -d)
git archive d58551f03d12c819e141c4ccd88c70c176b396d1 | tar -x -C "$research_snapshot"
python research/second_wave.py \
  --repo "$research_snapshot" \
  --revision d58551f03d12c819e141c4ccd88c70c176b396d1 \
  --out /tmp/hackalem-second-wave-evidence.json
```

On the review machine the existing research dependency directory was supplied via `PYTHONPATH=/private/tmp/hackalem-research-deps`. No new runtime dependencies were installed for these experiments. The script reads the ZIP directly and reuses the production functions; it does not publish application outputs.

The small self-check covers temporal ordering, the hop limit, excluding self-reach and label-invariant partition comparison. Additional assertions compare temporal/static reach and baseline clustering. Timestamps and source hashes identify the run; the research timing is not a product SLA measurement.

**Next evidence to collect:** a fresh accepted release, an explanation of three arbitrary IDs by a reviewer, and a blind comparison of the existing workflow against the proposed explanations/frontier list. The measurements here identify concrete weaknesses and useful extensions; they do not establish role accuracy or improved analyst performance.
