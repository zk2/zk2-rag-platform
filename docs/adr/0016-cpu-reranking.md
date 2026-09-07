# ADR-0016: Reranking is a small multilingual cross-encoder, on CPU

- Status: Accepted
- Date: 2026-09-06

## Context

The default pipeline has always had a `rerank` node, the constructor offers it,
and the README lists cross-encoder reranking as a feature. On the deployed
stand the node took between zero and two milliseconds: `RERANK_ENABLED` was
false and `sentence-transformers` was not in the image, so `rerank` silently
returned the first `top_k` of the fused list. The graph drew a box, the trace
showed a span, and nothing happened inside either.

That is worse than not having the feature. It also makes the pipeline
constructor and A/B untestable in the one comparison they most obviously
invite: with and without reranking.

Turning it on is not free. A cross-encoder reads query and passage together,
so it costs one forward pass per candidate, on the turn a person is waiting
for. Measured on twelve CPU cores against this corpus, thirty candidates:

| Model | Params | Thirty candidates |
|---|---|---|
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | 118M | 3.5 s |
| `BAAI/bge-reranker-base` | 278M | 18 s |
| `BAAI/bge-reranker-v2-m3` (the configured default) | 568M | 30 s |

A turn otherwise takes two to four seconds, most of it the model generating.
The configured default was therefore not a slow choice, it was an impossible
one: it would have multiplied every answer's latency by ten.

Quality, over the whole 386-chunk corpus rather than a truncated candidate
list, was not the differentiator: on four questions with known answers, both
the small and the base model put the right passage in the top two, 4 of 4.

## Decision

Ship reranking as something that runs:

- the `rerank` extra is installed in the image, with torch pinned to the
  CPU-only index - the default PyPI wheel drags in two and a half gigabytes of
  CUDA runtime that a CPU deployment never loads
- the model is baked into the image and seeds a named volume, so a fresh
  deployment reranks the first question instead of downloading a model minutes
  into someone's session
- the default becomes `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`:
  multilingual, because the corpus is, and the only size that fits a CPU turn
- `RERANK_CANDIDATES` is honoured - it existed as a setting and was ignored -
  and defaults to 15 of the 30 fusion produces. What is dropped keeps its fused
  rank; it would have had to beat fifteen better-ranked passages to be used
- `RERANK_MAX_TOKENS` caps the pair at 512, which covers a 400-token chunk plus
  the question
- the model loads at startup, in the background, so the first question does not
  pay the six seconds it takes to load

The larger models stay reachable through `RERANK_MODEL` for a deployment with a
GPU, which is where they belong.

## Consequences

- Reranking adds roughly two seconds to a turn at fifteen candidates. That is
  real, and the user notices it
- Whether those two seconds buy anything on this corpus is a question for the
  golden set and the A/B experiment, not for taste. The point of shipping it is
  that the comparison is now possible to run
- The image grows by torch and the model. On a stand with a 50 GB disk this is
  affordable; on a smaller one it is not, and `RERANK_ENABLED=false` still
  turns the whole thing off cleanly
- Reranking runs in the worker too, inside eval runs, so a hundred-question run
  pays it a hundred times. Worth remembering before running one on a laptop

## Measured, 2026-09-07

Run against a twelve-question golden set on the corpus described in ADR-0015,
with the graph pinned so the two differed only by the reranker and both put
five passages in the prompt:

| | with the reranker | without |
|---|---|---|
| retrieval_recall | 1.00 | 1.00 |
| context_precision | **0.73** | 0.63 |
| correctness, faithfulness, answer_relevancy, citation_rate | identical | identical |
| twelve questions | 37.7 s | 22.6 s |

The reranker never made the context worse and improved it on three questions of
twelve, twice by a lot: on "how much experience does the author have" the
prompt went from two passages out of five from the right document to five out
of five, and on "how would you design a production agent" from three to five.
Those are the questions where the corpus has plenty to say and the fused list
comes back noisy.

It bought no answer at all. Every metric that scores the answer came out the
same, because on these questions one correct passage is enough and it reached
the prompt either way. The price is 1.26 seconds per question, which is the
part a person waiting for an answer actually feels.

So on this corpus the reranker is switched off, and the measurement says when
to switch it back on: when a question needs more than one passage from the
right document, or when the corpus grows enough for the fused list to get
dirtier. That decision is now a number rather than an opinion, which is the
whole reason for building it.

A side observation from the same run: four questions scored 0.2 on context
precision in both configurations - one passage of five from the expected
document - and were answered correctly all the same. That looked like four
wasted passages per question, and it was not.

Cutting to three passages, without the reranker, lost a question outright:
retrieval_recall 1.00 to 0.92, correctness 0.98 to 0.91, answer_relevancy 0.92
to 0.83, all of it from the tool-comparison question whose one useful passage
sat in fifth place. The apparent waste was margin. Context precision *rose*,
0.63 to 0.69, because the hardest question fell out of the average along with
its low score - a reminder of what optimising a single metric buys.

Which sharpened what the reranker is for, and the last run settled it:

| | passages | cost | seconds | correctness | recall | precision |
|---|---|---|---|---|---|---|
| no rerank, five | 5 | $0.00339 | 56.8 | 0.98 | 1.00 | 0.63 |
| no rerank, three | 3 | $0.00248 | 55.3 | 0.91 | 0.92 | 0.69 |
| **rerank, three** | 3 | **$0.00247** | 78.1 | **0.98** | **1.00** | **0.78** |

The reranker is what makes three passages safe. Same answers as the five-passage
baseline on every metric, the cleanest context of any configuration, and 27 per
cent off the input of every question - because it puts the useful passage first
where fusion leaves it fifth. Three passages without it lose a question; three
with it lose nothing.

So the trade is explicit, and it has no single answer. It depends on who is
waiting and what the model charges:

- **A person waiting, on a cheap model.** 27 per cent of a 1240-token prompt on
  gpt-4o-mini is about $0.00005 a question. Paying a second and a half of
  someone's attention for that is a bad bargain: leave it off.
- **An expensive model, or nobody waiting.** The same 27 per cent on a model at
  $5 per million input tokens is $0.0017 a question - thirty times more - while
  the reranker's second and a half does not change. Batch work and large
  corpora both push the same way: a noisier fused list is exactly what a
  cross-encoder is for.

This deployment runs gpt-4o-mini with a person waiting, so the live bot serves
the five-passage graph without reranking. The configuration to switch to, and
the numbers that would justify switching, are written down above rather than
left to be rediscovered.

## Alternatives considered

- **A hosted reranker** (Cohere, Voyage, Jina). Better models, roughly 100 ms,
  no image weight. It needs another provider adapter, another key, and it puts
  the retrieved passages - the documents themselves - through a third party.
  Worth doing later as an alternative node, not as the only way to rerank
- **Quantised or ONNX inference.** A real answer to the latency, and the
  obvious next step if reranking proves its worth. Deferred rather than
  rejected: it adds a runtime and a conversion step to a decision that has not
  been justified by evals yet
- **Delete the node.** Honest, and rejected: hybrid retrieval without a reranker
  is a weaker product, and the platform exists to make exactly this kind of
  choice measurable
