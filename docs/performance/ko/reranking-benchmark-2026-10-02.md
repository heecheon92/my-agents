# Reranking benchmark — 2026-10-02

[English original](../en/reranking-benchmark-2026-10-02.md)

최종 답변의 품질 대신 검색 근거의 순서와 선택을 평가합니다. Prompt, 문서 본문,
account/email, document/chunk ID, credential은 문서에 기록하지 않습니다.

## Owner가 제공한 실제 실행

`FastAPI: uvicorn main:app (local pgvector)` profile에서 같은 질문을 새 대화로 실행했습니다.
논문의 실험 방법에 명시된 세 사실을 찾는 scenario이며 모든 mode가 같은 순서의 40개 후보를
받았습니다. Raw 100 → fused 79 → reranked 40 → packed 12입니다.

| Mode | 직접적인 방법론 근거의 순위 | Reranking ms | ContextForge total ms |
| --- | --- | ---: | ---: |
| Jev | **1, 2** | **1429.463** | 2970.709 |
| Cross-encoder | 11, 12 | 11057.830 | 12825.825 |
| Deterministic | 3, 4 | 0.062 | 1704.039 |

Jev는 직접 답하는 근거를 가장 먼저 배치하고 무관한 front matter를 context 밖으로 내렸습니다.
Cross-encoder는 일반 preprocessing 설명과 acknowledgment를 직접 근거보다 높게 배치했습니다.
다만 모든 mode에 답을 포함한 근거가 남으므로 최종 답변 품질 차이를 증명한 결과는 아닙니다.
Cross-encoder 시간에는 model loading이 포함되어 warm 성능으로 해석하면 안 됩니다.
개인/그룹 KB에 같은 문서가 있어 동일 내용이 여러 context slot을 차지하는 문제도 확인했습니다.

## Agent의 controlled suite

실제 계정의 권한 필터와 VS Code profile의 loopback pgvector/OpenAI embedding 설정을 사용해
여섯 scenario의 40개 후보를 고정했습니다. 같은 후보를 세 mode에 세 번씩 전달한 **54회**
component benchmark이며 최종 답변은 생성하거나 평가하지 않았습니다. DB는 repeatable-read,
read-only로 읽었으며 document/account/conversation record는 변경하지 않았습니다.

Python 3.14.5, Darwin/arm64, 12 physical cores, 48 GiB RAM, MPS available 환경입니다.
BGE는 `BAAI/bge-reranker-v2-m3`, batch 16, library-auto device입니다. Jev는
`typesafe/jev-1.13`, batch 8, 요청 24000/발췌 3000 UTF-8 bytes, shared timeout 10초입니다.
BGE를 한 번 load한 뒤 warm 반복을 측정하고 첫 load/inference **10247.829 ms**는 분리했습니다.

Model score나 답변 대신 전체 passage를 먼저 읽어 고정한 agent relevance label을 사용합니다.
`0..4` grade의 nDCG는 근거를 얼마나 앞에 배치했는지, facet coverage는 질문에 필요한 서로
다른 사실을 모두 담았는지 평가합니다. 이는 단일 agent annotation이며 독립 전문가 검증은 아닙니다.
240개 candidate label은 scoring 전에 고정했고 모든 shortlist에 필요한 facet이 존재했습니다.

| Metric | Deterministic | Jev | Warm BGE |
| --- | ---: | ---: | ---: |
| Mean nDCG@5 | 0.595 | **0.964** | 0.657 |
| Mean nDCG@12 | 0.638 | **0.957** | 0.714 |
| Mean facet coverage@5 | 66.7% | **98.1%** | 83.3% |
| 실제 packed facet coverage | 75.0% | **100.0%** | 91.7% |
| Top-12 unique content 비율 | 79.2% | **85.2%** | 72.2% |
| Median scoring latency | 0.018 ms | **1518.340 ms** | 2557.392 ms |

영어 논문 질문에서는 BGE와 Jev가 nDCG@5=1.000으로 같았습니다. 한국어 GPA policy 질문에서는
Jev가 초기 34위의 일반 학업 기준을 2위로 올려 교환학생 기준과 함께 담았습니다. BGE는 athlete의
GPA 기준을 1위로 배치하고 packed 12개에서도 일반 학업 기준을 놓쳤습니다. Jev도 athlete 근거를
4위에 남겨 완벽하지는 않았습니다. Funding 질문에서는 초기 17위의 named-agency 금액을
Jev가 계속 2위로 올렸습니다.

Typing 질문의 Jev 한 반복은 Field-validation facet을 top 5에서 놓쳤지만 packed 12에는
모두 담았습니다. Context limit을 5로 줄였다면 실제 coverage regression이 생기는 사례입니다.
반복 중 일부 순서가 바뀌므로 단일 호출의 작은 score 차이를 확정적 의미로 해석하지 않습니다.

18회 Jev pass의 90개 request는 모두 200/정상 score였고 fallback은 없었습니다.
API가 보고한 input 255783 tokens/output 11700 tokens의 scoring 비용은 **$0.010742886**,
40-candidate pass당 평균 **$0.000596827**입니다. Embedding/intent call, 최종 답변, 수수료,
hosting/local compute는 포함하지 않습니다. 2026-10-02 확인한
[OpenRouter 가격](https://openrouter.ai/typesafe/jev-1.13)과 일치하며 invoice는 아닙니다.

## Verdict와 한계

**현재 앱의 기본 reranker는 Jev를 유지하는 것이 타당합니다.** 이번 작은 corpus에서는 더 좋은
근거 순서, 완전한 packed fact coverage, 낮은 실제 API 비용과 경쟁력 있는 warm latency를
확인했습니다. Offline/fallback용 deterministic과 명시적 BGE 대안은 유지합니다. Mandatory
cascade를 추가할 근거는 없으며, 다음 개선은 권한 있는 citation provenance를 보존하면서
중복 내용을 packing에서 줄이는 별도 평가가 적절합니다.

다섯 scenario family와 bilingual 변형, 한 account/corpus/host, 세 반복, 단일 agent label만
사용했습니다. Held-out 질문, 긴 query/발췌 잘림, 새로운 permission-negative live case,
production load/failure rate는 측정하지 않았고 BGE weight revision도 앱에서 pin하지 않습니다.
Jev의 보편적 우월성이나 최종 답변 품질을 증명한 결과는 아닙니다.

[영어 원문](../en/reranking-benchmark-2026-10-02.md)에 mode별 case 표, protocol, 재실행 명령을
기록했습니다. [Redacted artifact](../data/reranking-2026-10-02.json)는 raw prompt/text/ID 없이
54개 order, anonymous gold label/facet/duplicate group, latency와 numeric usage를 보존합니다.
Runner는 `scripts/benchmark_reranking.py`이며 실제 private input/snapshot은 로컬에만 유지합니다.
Frontend/browser server는 변경하지 않았습니다.

## Revision history

- 2026-10-02: Owner 결과, 54회 실제 component 비교, agent label/facet 검증, API usage와 verdict 기록.
