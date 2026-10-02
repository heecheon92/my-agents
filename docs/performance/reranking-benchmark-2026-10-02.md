# Reranking benchmark — 2026-10-02

[한국어 요약](./reranking-benchmark-2026-10-02.ko.md)

## Scope

Evaluate evidence ordering and selection, not final-response quality. Deterministic RRF order,
OpenRouter Jev rubric scoring, and local BGE cross-encoder scoring receive the same authorized
shortlists. No answer-generation model is used as a judge. Prompts, source text, account/email,
document/chunk identifiers, and credentials stay out of this report.

## Owner-provided live comparison

Measurement ID: **RERANK-2026-10-02-OWNER**. Source: the owner's pasted local console output;
these measurements were not independently rerun by the agent. Configuration: VS Code
`FastAPI: uvicorn main:app (local pgvector)`, OpenAI query embeddings, all authorized sources,
one-message fresh conversations, high-recall retrieval. Scenario: a named research paper's
experimental-methods lookup asking for the vector database, dimensionality, and metric.

All three modes received the same 40 candidate IDs in the same initial order. Each gathered
100 raw candidates, fused to 79 distinct chunk IDs, reranked 40, and packed 12. The two copies
of the direct methods passage had these ranks:

| Mode | Direct methods ranks | Candidate gather ms | Reranking ms | Retrieval latency ms | Total ContextForge ms |
| --- | --- | ---: | ---: | ---: | ---: |
| Jev | **1, 2** | 1221.388 | **1429.463** | 2671.964 | 2970.709 |
| Cross-encoder | 11, 12 | 1439.346 | 11057.830 | 12517.891 | 12825.825 |
| Deterministic | 3, 4 | 1395.168 | 0.062 | 1406.961 | 1704.039 |

Jev completed five scoring requests successfully, promoted direct factual evidence, and removed
unrelated study-note front matter from the packed context. The cross-encoder ranked a general
preprocessing passage first and acknowledgments/declarations above the direct methods answer.
The agent inspected full chunks, not just the shortened printed excerpts, to confirm those
passages did not contain the requested implementation facts. All modes still packed passages
containing the answer; no final-response-quality conclusion follows from these logs.

The cross-encoder trace includes model loading and Hugging Face asset probes, so its timing
is a cold-start sample. It must not be compared to Jev as a measured steady-state disadvantage.
Reranker score scales differ; their numerical magnitudes are not comparable across modes.
`budget_truncated=true` reflects the 12-chunk cap in this sample, not demonstrated exhaustion
of the 24000-character budget. Several duplicate passages consume two slots because the same
paper is readable through personal and group KB copies with separate chunk IDs.

## Agent-run controlled suite

Measurement ID: **RERANK-2026-10-02-AGENT**. **54 scored runs:** six cases × three modes ×
three repetitions, plus one separately timed BGE cold-load/inference pass. All 90 Jev scoring
HTTP requests returned 200 and every pass used Jev scores; there were no fallback runs.
[Redacted per-run measurements and anonymous gold labels](./data/reranking-2026-10-02.json).

### Controlled environment and execution

- Loaded the selected VS Code profile's process environment, including loopback PostgreSQL on
  port 5433 and OpenAI response mode. `Settings` loaded the already configured local credentials;
  no credentials or local environment files were changed.
- Used an approved registered account's actual Product DB identity and production authorization
  filters. All-source scope includes its readable personal/group sources and ambient system KB.
  The source corpus was read under a PostgreSQL repeatable-read, read-only transaction. No
  documents, conversations, account preferences, or other DB records were created or deleted.
- Called production `ContextForgeService` directly to capture each authorized fused shortlist.
  This is a component replay benchmark, not an HTTP/frontend or complete-response benchmark.
  Query planning and OpenAI embedding calls happen once during preparation, outside timed scoring.
- Froze all 40 candidates and the query/plan once per case; replayed the identical inputs across
  modes/repetitions. Snapshot and pre-scoring label digests are retained in the redacted artifact.
- Ran sequentially with rotated mode order. BGE was loaded once and its first inference measured
  separately; the 18 scored BGE repetitions used the same warm instance. Jev instrumentation
  uses a fresh HTTP transport per batch, matching the production adapter.
- Host: Darwin/arm64, 12 physical cores, 48 GiB RAM, MPS available; cross-encoder device was
  library-auto. Python 3.14.5; sentence-transformers 5.5.1; torch 2.12.0; transformers 5.9.0;
  httpx 0.28.1. BGE model ID: `BAAI/bge-reranker-v2-m3`, batch size 16. Its upstream weight
  revision is not explicitly pinned by the application.
- Jev model: `typesafe/jev-1.13`; batch size 8; input request cap 24000 UTF-8 bytes; excerpt cap
  3000 bytes; shared timeout 10 seconds. Candidate limits: vector 20, lexical 20, structured 80,
  rerank 40, pack 12, character cap 24000. No production reranker behavior was changed for this suite.

### Relevance protocol

The agent inspected full passages and fixed all 240 candidate labels **before** either scored
reranker ran. No Jev/BGE score or final answer was used as ground truth. Identical normalized
content received identical labels. This is a single-agent annotation, not an expert-reviewed
or independently blinded dataset.

| Grade | Interpretation |
| --- | --- |
| 0 | Unrelated, wrong task/source context, or no useful requested evidence |
| 1 | Topical/background material, an isolated declaration, or quoted problem without the answer |
| 2 | Direct but limited support for part of the request |
| 3 | Substantial direct support, such as one of two required policy facts or two of three distinctions |
| 4 | Directly supports the complete requested fact set |

Requested facts are also labeled as independent evidence **facets**. A GPA number for athletes,
for example, cannot satisfy the normal undergraduate-progress or outbound-exchange facet.
Every frozen shortlist contained all requested facets, so subsequent misses reflect ordering
or packing, rather than first-stage absence. Some questions require multiple chunks; they need
not have a single grade-4 candidate. Both a complete abstract fact statement and a complete
methods fact statement receive grade 4; the separately reported methods-section rank captures
that finer preference in the owner comparison.

- **nDCG@5 / @12:** rank-discounted graded relevance, normalized against the ideal order within
  the same frozen shortlist. Gain is `2^grade - 1`, discount `log2(rank + 1)` for one-based rank.
  Scores near 1 place stronger labeled evidence earlier; they are not model confidence.
- **Facet coverage:** fraction of requested facts supported in the top-k or actually packed
  chunks. This detects missing complementary evidence even when individual top hits look relevant.
- **Unique-content rate@12:** distinct whitespace-normalized texts divided by top-12 slots.
  Standard nDCG counts duplicate candidates; this separate metric exposes wasted slots.
- Quality aggregates average the three repeats within each case and then the six cases equally.
  Repeats describe stability, not 18 independent query samples. Latency medians cover 18 passes
  per mode and exclude preparation, final answers, and the BGE cold pass.

### Aggregate results

| Metric | Deterministic | Jev | Warm BGE |
| --- | ---: | ---: | ---: |
| Mean nDCG@5 | 0.595 | **0.964** | 0.657 |
| Mean nDCG@12 | 0.638 | **0.957** | 0.714 |
| Mean facet coverage@5 | 66.7% | **98.1%** | 83.3% |
| Mean facet coverage@12 | 75.0% | **100.0%** | 91.7% |
| Mean actually packed facet coverage | 75.0% | **100.0%** | 91.7% |
| Mean unique-content rate@12 | 79.2% | **85.2%** | 72.2% |
| Mean grade-0 slots in top 12 | 3.50 | **1.17** | 3.50 |
| Median scoring latency | 0.018 ms | **1518.340 ms** | 2557.392 ms |
| Observed scoring latency range | 0.012–0.040 ms | 1382.360–2528.099 ms | 1467.098–3476.159 ms |

BGE's separate cold-load plus first-inference pass took **10247.829 ms**. The warm comparison
therefore does not depend on its cold-start penalty. The median Jev pass was approximately
40.6% shorter than the median warm BGE pass on this host/suite; it was not faster in every case.

### Per-case quality and warm latency

Values are mean nDCG@5 over three runs, with median scoring latency in parentheses.

| Case | Task family | Deterministic | Jev | Warm BGE |
| --- | --- | ---: | ---: | ---: |
| `paper_ko` | Korean methods lookup in an English PDF | 1.000 (0.014 ms) | **1.000 (1632.300 ms)** | 0.412 (1562.028 ms) |
| `paper_en` | English version of the same fact request | 0.990 (0.013 ms) | **1.000 (1406.011 ms)** | **1.000 (1579.700 ms)** |
| `routing_ko` | Static/dynamic routing conflict and correction | 0.161 (0.021 ms) | **0.861 (1465.878 ms)** | 0.723 (3296.296 ms) |
| `typing_ko` | Three validation distinctions | 0.795 (0.022 ms) | **0.958 (1783.253 ms)** | 0.824 (3111.915 ms) |
| `eligibility_ko` | Distinguish two GPA policy thresholds | 0.128 (0.017 ms) | **0.966 (1890.890 ms)** | 0.386 (2062.438 ms) |
| `funding_ko` | Total funding plus one named funding source | 0.498 (0.022 ms) | **1.000 (1485.992 ms)** | 0.599 (3073.089 ms) |

The most useful improvements were complementary facts reaching the selected context:

- **Eligibility:** the undergraduate-progress passage started at rank 34. Jev placed it second
  in all repeats, alongside the exchange threshold. Deterministic and BGE packed only one of
  the two target facets. BGE ranked the athlete-specific GPA policy first; Jev still left that
  distractor fourth, so its ranking was improved rather than perfect.
- **Funding:** the named-agency amount started at rank 17. Jev placed it second in every repeat.
  Deterministic missed it in the top 12; BGE included it in the top 12 but not the top five.
- **Routing:** explicit conflict explanations started at ranks 15 and 22. Jev put them second
  and third; BGE also recovered them, but ranked the quoted original problem first.
- **Typing:** Jev had stronger average graded ordering, but one repeat lacked the Field-validation
  facet in its top five. That facet remained inside its packed 12 in every repeat. Reducing the
  context limit to five would therefore have introduced a measured coverage regression.
- **Language/phrasing sensitivity:** BGE tied Jev on the English methods case while ranking
  general preprocessing ahead of complete factual evidence on the Korean version. This is
  local query sensitivity evidence, not proof that BGE is generally weak at Korean.

Jev's top-five candidate IDs varied in the two paper cases and the typing case; paper variation
mostly swapped duplicate copies. All BGE and deterministic top-five orders were stable across
these three repeats. Jev's packed facet coverage remained complete despite its observed variation.

### Provider-reported cost

The 18 Jev reranking passes made **90 requests**, reporting **255783 input tokens**, **11700
output tokens**, and total `usage.cost` **$0.010742886**. That averages **$0.000596827 per
40-candidate pass**, or approximately **$0.597 per 1000 comparable passes** by linear extrapolation.
These are scoring-only API-reported costs, not an invoice or total chat cost; preparation
embeddings/intent calls, answer generation, fees, hosting, and local compute are excluded.

The recorded cost matches the currently listed $0.042/M input-token rate, with output free.
Pricing was checked on 2026-10-02 at [OpenRouter's Jev model page](https://openrouter.ai/typesafe/jev-1.13).
Future pricing, input lengths, request failure behavior, and network conditions can change costs.

### Verdict

**Keep Jev as the default reranker for the current application.** This small suite supports
that choice with stronger graded evidence ordering, complete packed fact coverage, low measured
API cost, and competitive warm latency, while avoiding local reranker-model provisioning.
Keep deterministic mode for offline operation and whole-pass fallback. Retain BGE as an
explicit alternative; its English result shows it remains useful, and this suite is too small
and deliberately selected to establish universal model superiority.

Do not add a mandatory Jev-plus-cross-encoder cascade based on these results. The measured
benefit does not require one, and a cascade would add local-model operations and latency that
this study has not justified. Prioritize a separately evaluated duplicate-content packing
change next, preserving authorized citation provenance. Do not reduce candidate/context
budgets merely to make the timings smaller: the typing case demonstrates the risk.

Remaining limits: five scenario families with a bilingual repeat, one account/corpus/host,
three trials, single-agent labels, no independently held-out query set, unpinned BGE weight
revision, no long-query/truncation stress, no new permission-negative provider test in this
live suite, and no production load/failure-rate measurement. Existing offline authorization
and fallback tests remain necessary. **No final-response-quality judgment was made.**

## Reproduce and audit

The reusable runner is [`scripts/benchmark_reranking.py`](../../scripts/benchmark_reranking.py).
Use private case/snapshot/label files outside the repository and already configured credentials.
The case JSON is a list of `{id, query, required_facets}`; IDs should be anonymous scenario
labels. Preparation writes private candidate text and provenance. Label JSON maps each case ID
and anonymous `c01`..`c40` candidate label to `{grade, facets}`. Fix labels before running.
Output files must be new; the writer uses mode 0600 and refuses existing paths/symlinks.

```bash
uv run python -m scripts.benchmark_reranking prepare \
  --email "$RERANK_BENCH_ACCOUNT_EMAIL" \
  --cases "$RERANK_BENCH_CASES_PATH" \
  --output "$RERANK_BENCH_SNAPSHOT_PATH"
uv run python -m scripts.benchmark_reranking run \
  --snapshot "$RERANK_BENCH_SNAPSHOT_PATH" \
  --labels "$RERANK_BENCH_LABELS_PATH" \
  --output "$RERANK_BENCH_RESULTS_PATH" --repeats 3
```

The exact private inputs and snapshot remain local, not versioned public fixtures. The checked-in
redacted artifact contains all 54 orders, grades, facet labels, anonymous duplicate groups,
latencies, and numeric provider usage, so reported metrics can be recomputed without provider
calls. Live replay requires the private snapshot and credentials; new preparation can differ
if the corpus or upstream providers change. No frontend/browser server was changed for these runs.

## Revision history

- 2026-10-02: Recorded the owner run, completed 54 live component comparisons, audited full
  source-derived labels and per-case coverage, captured numeric API usage, and added the verdict.
