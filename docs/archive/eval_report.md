# Phase one evaluation contract

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

The offline evaluation fixtures cover extra and unknown citations, missing evidence,
empty citation lists, absent reference labels, exceptions, nonterminal/failed reports,
context usage, repeated topics, and nullable JSON serialization. No model calls are
needed: the graph runner is replaced only at the external execution boundary.

`citation_accuracy` measures expected-source precision: the number of citation
records matching a labelled source ID or exact authoritative document title,
divided by all submitted citation records. Unknown source IDs cannot be credited;
arbitrary bibliography text cannot establish a title match. Extra and invalid
records remain in the denominator. This is not expected-source recall.

`citation_validity` measures structural provenance: citations whose nonempty
evidence IDs all exist and belong to their stated source, divided by all citation
records. This does not establish semantic support for the associated claim.

An empty citation list is unscorable (`null`). Expected-source precision is also
`null` when the dataset provides no expected sources. Aggregate means exclude
`null` and expose scored-case counts; an entirely unscorable aggregate stays
`null`. Failed/incomplete cases with reference labels score zero precision;
without reference labels they remain unscorable. Failed cases never acquire full
credit through the aggregation helper.

Completion requires `completed` (or legacy `done`) plus report content. Context
injection uses the actual node prompt flag `context_used`, not the presence of
recalled text. Each evaluation case receives a unique UUID session identifier;
the workflow creates a unique run/checkpoint identifier. Durations use a monotonic
clock. These fixtures assess evaluator correctness, not real research quality,
Postgres throughput, or model performance.

Validation command: `.venv/Scripts/python.exe -m pytest tests/evaluation/test_eval.py -q`.
