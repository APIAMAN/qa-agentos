"""Duplicate-bug matching rules (pure; shared by the bug store and the eval harness).

1. Exact signature: a grounded primary signature already seen in the project → that cluster.
2. Embedding: the most similar earlier report of the same embedding model, if similarity is at
   or above the threshold AND the bugs are structurally compatible (same exception type, and
   overlapping stack frames when both have frames; same category for frameless error lines).
   Similar wording alone never merges bugs.
3. Otherwise a new cluster.

Every decision carries the evidence that explains it (method, similarity, threshold, reasons and
the rejected near-misses).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class BugFingerprint:
    signature: Optional[str]
    exception_type: Optional[str]
    top_frames: Sequence[str]
    category: str
    title: str
    message: str = ""


@dataclass(frozen=True)
class Candidate:
    bug_id: uuid.UUID
    cluster_id: uuid.UUID
    similarity: float
    fingerprint: BugFingerprint


@dataclass
class MatchDecision:
    method: str  # "exact_signature" | "embedding" | "new"
    cluster_id: Optional[uuid.UUID] = None
    matched_bug_id: Optional[uuid.UUID] = None
    similarity: Optional[float] = None
    threshold: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.method != "new"

    def as_evidence(self, *, embedding_model: Optional[str], embedding_status: str) -> Dict[str, Any]:
        return {
            "method": self.method,
            "matched_bug_id": str(self.matched_bug_id) if self.matched_bug_id else None,
            "similarity": round(self.similarity, 4) if self.similarity is not None else None,
            "threshold": self.threshold,
            "reasons": self.reasons,
            "rejected": self.rejected[:3],
            "embedding_model": embedding_model,
            "embedding_status": embedding_status,
        }


def _frame_keys(frames: Sequence[str]) -> set[str]:
    # Compare the function/method part; files can move while the function stays the same.
    return {f.split(" (")[0].split("(")[0].strip() for f in frames if f}


def structural_mismatch(a: BugFingerprint, b: BugFingerprint) -> Optional[str]:
    """Why ``a`` and ``b`` cannot be the same bug regardless of text similarity (None = compatible)."""
    if (a.exception_type or None) != (b.exception_type or None):
        return f"different exception type ({a.exception_type} vs {b.exception_type})"
    if a.top_frames and b.top_frames:
        if not (_frame_keys(a.top_frames) & _frame_keys(b.top_frames)):
            return "no shared stack frames"
    elif a.top_frames or b.top_frames:
        return "only one side has a stack trace"
    elif a.category != b.category:
        return f"different category ({a.category} vs {b.category})"
    return None


def decide_match(
    new: BugFingerprint,
    *,
    exact: Optional[Candidate],
    neighbours: Sequence[Candidate],
    threshold: float,
) -> MatchDecision:
    if exact is not None and new.signature:
        return MatchDecision(
            method="exact_signature",
            cluster_id=exact.cluster_id,
            matched_bug_id=exact.bug_id,
            similarity=1.0,
            reasons=[f"same error signature {new.signature}"],
        )

    rejected: List[Dict[str, Any]] = []
    for cand in sorted(neighbours, key=lambda c: c.similarity, reverse=True):
        if cand.similarity < threshold:
            rejected.append({"bug_id": str(cand.bug_id), "similarity": round(cand.similarity, 4), "reason": "below threshold"})
            break  # sorted: nothing further can pass
        why_not = structural_mismatch(new, cand.fingerprint)
        if why_not:
            rejected.append({"bug_id": str(cand.bug_id), "similarity": round(cand.similarity, 4), "reason": why_not})
            continue
        reasons = [f"embedding similarity {cand.similarity:.3f} >= {threshold}"]
        reasons.append(f"same exception type ({new.exception_type})" if new.exception_type else f"same category ({new.category})")
        if new.top_frames:
            shared = sorted(_frame_keys(new.top_frames) & _frame_keys(cand.fingerprint.top_frames))
            reasons.append(f"shared frames: {', '.join(shared[:3])}")
        return MatchDecision(
            method="embedding",
            cluster_id=cand.cluster_id,
            matched_bug_id=cand.bug_id,
            similarity=cand.similarity,
            threshold=threshold,
            reasons=reasons,
            rejected=rejected,
        )
    return MatchDecision(
        method="new",
        threshold=threshold,
        reasons=["no earlier report with the same signature or a compatible, similar enough bug"],
        rejected=rejected,
    )


def embedding_text(fp: BugFingerprint) -> str:
    """Text embedded for similarity: what failed and where, not incidental wording."""
    parts = [fp.exception_type or "", fp.message, " ".join(fp.top_frames[:3]), fp.title, fp.category]
    return "\n".join(p for p in parts if p).strip() or fp.title
