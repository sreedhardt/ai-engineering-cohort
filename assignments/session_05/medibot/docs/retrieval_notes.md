# Retrieval quality notes

Reproduce everything here with:

```bash
uv run python -m evaluation.compare_retrieval
```

## What was measured

For each probe query we know which chunk actually answers it (identified by its
section title, verified against the index). We report that chunk's rank under
three regimes:

| Stage | What it is |
|---|---|
| `dense@10` | dense vectors only (`BAAI/bge-small-en-v1.5`), top 10 |
| `fusion@10` | dense + BM25 fused with Reciprocal Rank Fusion, top 10, pre-rerank |
| `rerank@3` | after the cross-encoder narrows to the final 3 passed to the LLM |

All three run behind the same RBAC filter.

## Results

```
query                                         dense@10  fusion@10  rerank@3
---------------------------------------------------------------------------
What is the standard adult dose of Amoxici           1          1         1
Which diagnosis code is used for I21.4?              1          1         1
DriveFlow IP-200 occlusion pressure alarm            1          1         1
SterilPro 3000 Bowie-Dick test frequency             1          1         2
Metformin renal dose adjustment                      1          1         1
What does fault code F-05 mean?                      1          1         1
---------------------------------------------------------------------------
mean reciprocal rank                             1.000      1.000     0.917
```

Bare identifiers, where BM25 classically earns its keep:

```
query                role                  dense@10  fusion@10
--------------------------------------------------------------
F-09                 technician                   2          1
N17.9                billing_executive            1          1
J44.9                billing_executive            1          1
I21.4                billing_executive            1          1
```

## Honest reading of these numbers

**Hybrid does not dramatically beat dense-only on this corpus, and it would be
misleading to claim otherwise.** The reason is corpus size. There are 256 chunks
in total, and the RBAC filter narrows each query to somewhere between 31 chunks
(equipment) and 120 (general + nursing). `bge-small` is comfortably strong enough
to rank the right chunk first in a pool that small — dense-only scores a perfect
MRR of 1.000, leaving fusion no headroom to improve on.

The one place hybrid does win is the bare fault code `F-09`, which dense ranks
2nd and fusion ranks 1st. That is the expected shape of the benefit: an opaque
alphanumeric token carries almost no semantic signal, so exact-match retrieval
is what finds it. On a corpus of tens of thousands of chunks, with many
near-duplicate protocol sections competing, this effect would be far more
pronounced. At this scale it is visible but small.

Hybrid never *hurt* a query in this probe set, so it is kept: it costs one extra
sparse vector per chunk and no extra round trip (both vectors are queried in a
single Qdrant request), and it insures against exactly the terminology lookups
this domain is full of.

## What reranking actually fixed

The more interesting finding was a bug in our own pipeline, not a ranking win.

Initially the reranker made things **worse** — MRR@3 of 0.583, and it pushed the
correct chunk for the occlusion-alarm query out of the top 3 entirely. Logging
the scores (as the assignment tips suggest) showed every candidate scoring
between −3 and −11, i.e. the cross-encoder considered nothing relevant.

The cause: we embedded the *contextualized* text (heading path prepended) but
passed the reranker the *raw chunk body*. The chunk that answers "DriveFlow
IP-200 occlusion pressure alarm settings" has this body:

```
- Default - 300 mmHg.
- Venous lines - reduce the threshold to 200 mmHg.
```

It never contains the words "occlusion", "DriveFlow" or "IP-200" — those live in
the heading. The cross-encoder was being asked to judge a contextless fragment,
which is precisely the failure the assignment warns about. Scoring the same
heading-qualified text that was embedded took MRR@3 from 0.583 to 0.917 and put
the occlusion chunk back at rank 1.

Model choice mattered too, measured on the same probes:

| Cross-encoder | MRR@3 |
|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | 0.583 |
| `BAAI/bge-reranker-base` | 0.639 |
| **`jinaai/jina-reranker-v1-turbo-en`** | **0.750** |

(measured before the context fix; jina-turbo was kept and now scores 0.917 with it)

MS MARCO models are trained on web passage ranking. Much of this corpus is
serialised tables — `"Amoxicillin, Class = Penicillin. Amoxicillin, Route = Oral."`
— which looks nothing like their training distribution.

## Where the remaining rerank@3 loss comes from

The one probe that still ranks 2nd rather than 1st is the Bowie-Dick test
frequency. Both the "Routine testing" section and the "Preventive Maintenance
Calendar" table legitimately answer it; the reranker prefers the calendar. Since
both reach the LLM, the cited answer is correct either way.
