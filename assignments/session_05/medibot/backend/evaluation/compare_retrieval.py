"""Dense-only vs hybrid vs hybrid+rerank, measured per pipeline stage.

Evidence for the "retrieval quality demonstrably better than dense-only" claim —
and an honest account of where the difference does and doesn't show up.

Each probe names the chunk that actually answers it (by section title, verified
against the index). We report the rank of that chunk under three regimes:

    dense    - dense vectors only, top 10
    fusion   - dense + BM25 fused with RRF, top 10, before reranking
    rerank   - after the cross-encoder narrows to the final top 3

Queries carry exact clinical terminology — drug names, ICD-10 codes, equipment
model numbers and fault codes — which is where BM25 earns its place.

    uv run python -m evaluation.compare_retrieval
"""

from __future__ import annotations

from app.config import settings
from app.retrieval import retrieve, retrieve_dense_only
from app.vectorstore import hybrid_search
from app.rbac import qdrant_filter
from app.retrieval import _to_chunks

CANDIDATES = 10
FINAL_K = 3

# (query, role, section title of the chunk that answers it)
PROBES = [
    ("What is the standard adult dose of Amoxicillin?", "doctor", "1. Antimicrobials"),
    ("Which diagnosis code is used for I21.4?", "billing_executive",
     "1. Top 30 Diagnosis Codes Used at MediAssist"),
    ("DriveFlow IP-200 occlusion pressure alarm settings", "technician",
     "Occlusion pressure alarm settings"),
    ("SterilPro 3000 Bowie-Dick test frequency", "technician", "Routine testing"),
    ("Metformin renal dose adjustment", "doctor",
     "6. Renal Dose Adjustment (selected drugs)"),
    ("What does fault code F-05 mean?", "technician", "Fault codes"),
]


def _rank_of(chunks, section: str) -> int | None:
    for i, chunk in enumerate(chunks, start=1):
        if chunk.section_title == section:
            return i
    return None


def _rr(rank: int | None) -> float:
    """Reciprocal rank — 0 when the target was never retrieved."""
    return 1.0 / rank if rank else 0.0


# Bare identifiers with no natural-language context — the classic BM25 case,
# where a dense model has little semantic signal to work with.
CODE_PROBES = [
    ("F-09", "technician", "Fault codes"),
    ("N17.9", "billing_executive", "1. Top 30 Diagnosis Codes Used at MediAssist"),
    ("J44.9", "billing_executive", "1. Top 30 Diagnosis Codes Used at MediAssist"),
    ("I21.4", "billing_executive", "1. Top 30 Diagnosis Codes Used at MediAssist"),
]


def _code_comparison() -> None:
    print("\n\nBare-identifier queries (dense vs fusion, top 10)\n")
    header = f"{'query':20} {'role':20} {'dense@10':>9} {'fusion@10':>10}"
    print(header)
    print("-" * len(header))
    for query, role, section in CODE_PROBES:
        dense = retrieve_dense_only(query, role, CANDIDATES)
        fusion = _to_chunks(hybrid_search(query, qdrant_filter(role), CANDIDATES))
        show = lambda r: str(r) if r else "miss"  # noqa: E731
        print(
            f"{query:20} {role:20} {show(_rank_of(dense, section)):>9} "
            f"{show(_rank_of(fusion, section)):>10}"
        )


def main() -> None:
    print(f"Target = the chunk that answers the query, by section title.")
    print(f"Numbers are its rank; 'miss' means it was never retrieved.\n")
    header = f"{'query':44} {'dense@10':>9} {'fusion@10':>10} {'rerank@3':>9}"
    print(header)
    print("-" * len(header))

    totals = {"dense": 0.0, "fusion": 0.0, "rerank": 0.0}

    for query, role, section in PROBES:
        dense = retrieve_dense_only(query, role, CANDIDATES)
        fusion = _to_chunks(hybrid_search(query, qdrant_filter(role), CANDIDATES))
        reranked, _ = retrieve(query, role, candidates=CANDIDATES, top_k=FINAL_K)

        ranks = {
            "dense": _rank_of(dense, section),
            "fusion": _rank_of(fusion, section),
            "rerank": _rank_of(reranked, section),
        }
        for stage, rank in ranks.items():
            totals[stage] += _rr(rank)

        show = lambda r: str(r) if r else "miss"  # noqa: E731
        print(
            f"{query[:42]:44} {show(ranks['dense']):>9} "
            f"{show(ranks['fusion']):>10} {show(ranks['rerank']):>9}"
        )

    print("-" * len(header))
    n = len(PROBES)
    print(
        f"{'mean reciprocal rank':44} "
        f"{totals['dense'] / n:>9.3f} {totals['fusion'] / n:>10.3f} "
        f"{totals['rerank'] / n:>9.3f}"
    )
    _code_comparison()
    print(
        "\nNote: rerank@3 is scored over 3 slots vs 10 for the other two, so a "
        "\nlower MRR there is expected; what matters is whether the answering "
        "\nchunk survives the cut at all."
    )


if __name__ == "__main__":
    main()
