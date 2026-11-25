"""Embedding model benchmark for duplicate detection (decision D1).

    uv run --project evals python evals/embeddings_benchmark.py --embedder gateway   # needs credentials
    uv run --project evals python evals/embeddings_benchmark.py --embedder fake      # offline baseline

Reports: actual dimension, whether vectors arrive normalized, latency, whether pgvector can
ANN-index the dimension, similarity of labeled duplicate pairs vs every other pair, and a suggested
DUPLICATE_SIMILARITY_THRESHOLD (midpoint of the gap). A model with no gap (some non-duplicate pair
scores above a duplicate pair) needs the structural guard to separate them; the suggestion says so.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import pathlib
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from qa_agentos.core.config import Settings  # noqa: E402
from qa_agentos.modules.bugs.matching import embedding_text, structural_mismatch  # noqa: E402
from qa_agentos.runtime.gateway.embeddings import Embedder, HashingEmbedder, cosine, embedder_from_settings  # noqa: E402
from qa_agentos.runtime.tools.builtin.logparse import parse_log_text  # noqa: E402
from qa_evals.dataset import load_dataset, load_outputs  # noqa: E402
from qa_evals.scoring import fingerprint  # noqa: E402

PGVECTOR_ANN_MAX_DIMS = {"vector": 2000, "halfvec": 4000}


async def benchmark(embedder: Embedder) -> Dict[str, Any]:
    ds = load_dataset()
    ref = load_outputs(ds.root / "reference_outputs.json")
    fps = {c.id: fingerprint(ref[c.id], parse_log_text(c.log_text)) for c in ds.cases}
    started = time.monotonic()
    res = await embedder.embed([embedding_text(fps[c.id]) for c in ds.cases])
    latency_ms = int((time.monotonic() - started) * 1000)
    vecs = dict(zip([c.id for c in ds.cases], res.vectors, strict=True))
    dims = len(res.vectors[0])
    dup_pairs = {frozenset((d["case"], d["of"])) for d in ds.duplicates}
    sims: List[Dict[str, Any]] = []
    for a, b in itertools.combinations(vecs, 2):
        sims.append(
            {
                "a": a,
                "b": b,
                "similarity": round(cosine(vecs[a], vecs[b]), 4),
                "duplicate": frozenset((a, b)) in dup_pairs,
                "structural_mismatch": structural_mismatch(fps[a], fps[b]),
            }
        )
    dup = [s["similarity"] for s in sims if s["duplicate"]]
    other = [s["similarity"] for s in sims if not s["duplicate"]]
    compatible_other = [s["similarity"] for s in sims if not s["duplicate"] and s["structural_mismatch"] is None]
    min_dup, max_other = min(dup), max(other)
    max_compatible_other = max(compatible_other) if compatible_other else 0.0
    return {
        "model_identity": res.model_identity,
        "dimensions": dims,
        "normalized": all(math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6) for v in res.vectors),
        "batch_latency_ms": latency_ms,
        "pgvector_ann_indexable": {k: dims <= v for k, v in PGVECTOR_ANN_MAX_DIMS.items()},
        "duplicate_similarity": {"min": min_dup, "values": dup},
        "non_duplicate_similarity": {"max": max_other, "max_structurally_compatible": max_compatible_other},
        "separable_by_similarity_alone": min_dup > max_other,
        "suggested_threshold": round((min_dup + max_compatible_other) / 2, 3) if min_dup > max_compatible_other else None,
        "top_pairs": sorted(sims, key=lambda s: s["similarity"], reverse=True)[:8],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--embedder", choices=["fake", "gateway"], default="fake")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()
    embedder = HashingEmbedder(256) if args.embedder == "fake" else embedder_from_settings(Settings())
    if embedder is None:
        print("Embeddings are disabled (EMBEDDINGS_ENABLED=false)")
        return 2
    async def run() -> Dict[str, Any]:
        try:
            return await benchmark(embedder)
        finally:
            await embedder.aclose()  # same event loop as the requests (the HTTP client is bound to it)

    report = asyncio.run(run())
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
