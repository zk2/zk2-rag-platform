# ADR-0014: Eval metrics implemented in-project rather than Ragas

- Status: Accepted
- Date: 2026-09-05

## Context

The eval slice needed faithfulness, answer relevancy and correctness. Ragas is
the obvious library: those metrics are its subject, and its implementations
have been read by more people than ours ever will be.

Ragas runs judge calls through its own model wrappers. This project already has
a provider layer with per-organization keys, a catalog that prices every call,
metrics, tracing, and an allowance that decides whether a shared key may be
used at all. A second path to the same providers would sit outside all of it.

## Decision

Metrics are defined in-project behind a small protocol - a name, whether it
needs a judge, and a `score` returning a float in [0, 1]:

- deterministic metrics (retrieval recall against expected sources, citation
  rate, answered) compute from what the pipeline already produced
- judge metrics ask a model through the project's own provider interface, with
  a strict JSON contract and defensive parsing

An eval run therefore costs money the same way a chat turn does: priced by the
catalog, recorded in `usage_events`, counted against the shared-key allowance.

## Consequences

- One accounting path. A team can see what its evals cost next to what its
  chat costs, in the same table
- Judge prompts are ours to tune, and the parser tolerates a chatty model
  rather than trusting it to emit bare JSON
- We carry the correctness of these metric definitions. They are simpler than
  Ragas's - no statement decomposition, no embedding-based relevancy - and a
  score here is comparable across runs, not comparable to a Ragas number
- Ragas can be added later as another metric source: the protocol is the seam,
  and nothing in the runner assumes where a score came from

## Alternatives considered

- **Ragas now.** Better-known metric definitions, at the price of a second
  provider path that the catalog, the allowance and the usage table cannot see
- **Both from the start.** Two implementations of the same metric names invites
  the question of which number is on the dashboard
