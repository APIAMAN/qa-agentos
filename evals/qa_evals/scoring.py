"""Deterministic scoring (no model, no network): same outputs → same scores.

* severity / category accuracy: exact label match (AC1: severity ≥ 9/10 golden cases)
* signature accuracy: the reported primary signature is the expected one
* grounding: every cited signature was produced by ``log.parse`` for that case (no invented
  evidence; a deterministic proxy for faithfulness)
* duplicate detection: replays the production matching rules (exact signature → embedding with
  structural guard → new) over the dataset in order; precision and recall over labeled pairs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from qa_agentos.modules.bugs.matching import BugFingerprint, Candidate, decide_match, embedding_text
from qa_agentos.runtime.gateway.embeddings import Embedder, cosine
from qa_agentos.runtime.tools.builtin.logparse import parse_log_text

from qa_evals.dataset import Dataset


@dataclass
class CaseScore:
    case_id: str
    severity_ok: bool
    category_ok: bool
    signature_ok: bool
    grounded: bool
    expected: Dict[str, Any]
    actual: Dict[str, Any]
    problems: List[str] = field(default_factory=list)


def score_case(case_id: str, expected: Mapping[str, Any], output: Optional[Mapping[str, Any]], parsed: Mapping[str, Any]) -> CaseScore:
    if output is None:
        return CaseScore(case_id, False, False, False, False, dict(expected), {}, ["no output (run failed or missing)"])
    parsed_sigs = {s["signature"] for s in parsed.get("signatures", [])}
    cited = [e.get("signature") for e in output.get("evidence") or [] if isinstance(e, Mapping) and e.get("signature")]
    primary = output.get("primary_signature")
    invented = [s for s in cited + ([primary] if primary else []) if s not in parsed_sigs]
    score = CaseScore(
        case_id=case_id,
        severity_ok=output.get("severity") == expected["severity"],
        category_ok=output.get("category") == expected["category"],
        signature_ok=primary == expected["primary_signature"],
        grounded=not invented,
        expected={k: expected[k] for k in ("severity", "category", "primary_signature")},
        actual={k: output.get(k) for k in ("severity", "category", "primary_signature")},
    )
    if not score.severity_ok:
        score.problems.append(f"severity {output.get('severity')} != {expected['severity']}")
    if not score.category_ok:
        score.problems.append(f"category {output.get('category')} != {expected['category']}")
    if not score.signature_ok:
        score.problems.append(f"primary_signature {primary} != {expected['primary_signature']}")
    if invented:
        score.problems.append(f"cites signatures not produced by log.parse: {sorted(set(invented))}")
    return score


def fingerprint(output: Mapping[str, Any], parsed: Mapping[str, Any]) -> BugFingerprint:
    by_sig = {s["signature"]: s for s in parsed.get("signatures", [])}
    entry = by_sig.get(output.get("primary_signature") or "")
    return BugFingerprint(
        signature=entry["signature"] if entry else None,
        exception_type=(entry or {}).get("exception_type"),
        top_frames=list((entry or {}).get("top_frames") or []),
        category=str(output.get("category") or "other"),
        title=str(output.get("title") or ""),
        message=str((entry or {}).get("message") or ""),
    )


async def cluster_cases(
    dataset: Dataset, outputs: Mapping[str, Mapping[str, Any]], embedder: Optional[Embedder], threshold: float
) -> Dict[str, Dict[str, Any]]:
    """Cluster assignment per case, using the production matching rules in dataset order."""
    stored: List[Dict[str, Any]] = []
    assigned: Dict[str, Dict[str, Any]] = {}
    for case in dataset.cases:
        out = outputs.get(case.id)
        if out is None:
            continue
        fp = fingerprint(out, parse_log_text(case.log_text))
        vec = (await embedder.embed([embedding_text(fp)])).vectors[0] if embedder else None
        exact = next((s for s in stored if fp.signature and s["fp"].signature == fp.signature), None)
        exact_cand = Candidate(exact["id"], exact["cluster"], 1.0, exact["fp"]) if exact else None
        neighbours = [
            Candidate(s["id"], s["cluster"], cosine(vec, s["vec"]), s["fp"]) for s in stored if vec is not None and s["vec"] is not None
        ]
        decision = decide_match(fp, exact=exact_cand, neighbours=neighbours, threshold=threshold)
        cluster = decision.cluster_id if decision.cluster_id is not None else case.id
        stored.append({"id": case.id, "cluster": cluster, "fp": fp, "vec": vec})
        assigned[case.id] = {"cluster": cluster, "method": decision.method, "similarity": decision.similarity}
    return assigned


def duplicate_metrics(dataset: Dataset, assigned: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    tp = fn = fp_ = 0
    details: List[Dict[str, Any]] = []
    for dup in dataset.duplicates:
        a, b = assigned.get(dup["case"]), assigned.get(dup["of"])
        linked = bool(a and b and a["cluster"] == b["cluster"])
        method_ok = bool(a and a["method"] == dup.get("expected_method", a["method"]))
        tp += int(linked)
        fn += int(not linked)
        details.append({"pair": [dup["case"], dup["of"]], "expected": "same", "linked": linked, "method": a and a["method"], "method_ok": method_ok})
    for pair in dataset.distinct:
        a, b = assigned.get(pair["a"]), assigned.get(pair["b"])
        merged = bool(a and b and a["cluster"] == b["cluster"])
        fp_ += int(merged)
        details.append({"pair": [pair["a"], pair["b"]], "expected": "distinct", "linked": merged})
    precision = tp / (tp + fp_) if (tp + fp_) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {"precision": precision, "recall": recall, "false_merges": fp_, "missed_duplicates": fn, "pairs": details}


def deterministic_scores(dataset: Dataset, outputs: Mapping[str, Mapping[str, Any]]) -> List[CaseScore]:
    """Scores for the cases that produced an output (missing outputs are handled by the gate)."""
    return [score_case(c.id, c.expected, outputs[c.id], parse_log_text(c.log_text)) for c in dataset.cases if c.id in outputs]
