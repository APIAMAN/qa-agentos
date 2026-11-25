"""Evaluation runner and gate.

Modes
  reference  hand-written reference outputs; keyless self-test of the harness and gate (CI)
  replay     outputs recorded by earlier live runs (``--outputs``, repeatable to merge subsets)
  preflight  one minimal call to the agent and judge aliases (access / free-tier quota check)
  live       real agent + G-Eval judge on the dataset (AC1)
  ac4        live twice: baseline prompt vs deliberately weakened prompt (AC4)

Real model calls happen only in preflight/live/ac4, only with ``--allow-live``, never under CI, and
always inside a shared budget (``--max-calls``, ``--max-cost-usd`` at operator-confirmed prices,
``--min-interval-seconds`` pacing; a quota/429 response stops the run). ``--dry-run`` checks the
configuration and prints the plan without any network call.

Exit codes: 0 all mandatory gates passed · 1 a gate below its threshold · 2 a mandatory gate could
not be computed (e.g. no judge, budget exhausted, failed runs). Never 0 for an unrun gate.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx
import yaml
from qa_agentos.core.config import Settings
from qa_agentos.registry.loader import load_registry_catalog
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.embeddings import Embedder, HashingEmbedder, embedder_from_settings
from qa_agentos.runtime.tools.builtin.logparse import PARSER_VERSION, parse_log_text

from qa_evals.budget import BudgetExceeded, BudgetTransport, CallBudget, prices_from_env
from qa_evals.dataset import EVALS_DIR, REPO_ROOT, Dataset, load_dataset, load_outputs
from qa_evals.judge import JudgeUnavailable, faithfulness_scores, judge_from_env
from qa_evals.scoring import cluster_cases, deterministic_scores, duplicate_metrics

EXIT_PASS, EXIT_BELOW_THRESHOLD, EXIT_NOT_RUN = 0, 1, 2


def _git_sha() -> Optional[str]:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True).stdout.strip()
    except Exception:
        return None


def _rate(values: List[bool]) -> Optional[float]:
    return sum(values) / len(values) if values else None


async def evaluate(
    *,
    mode: str,
    dataset: Dataset,
    outputs: Mapping[str, Optional[Mapping[str, Any]]],
    thresholds: Mapping[str, Any],
    embedder: Optional[Embedder],
    duplicate_threshold: float,
    judge: Any = None,
    embedder_unavailable: Optional[str] = None,
    run_errors: Optional[Mapping[str, str]] = None,
    extra_meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    present = {k: v for k, v in outputs.items() if v is not None}
    case_scores = deterministic_scores(dataset, present)
    metrics: Dict[str, Optional[float]] = {
        "severity_accuracy": _rate([s.severity_ok for s in case_scores]),
        "category_accuracy": _rate([s.category_ok for s in case_scores]),
        "signature_accuracy": _rate([s.signature_ok for s in case_scores]),
        "grounding_rate": _rate([s.grounded for s in case_scores]),
    }
    dup = duplicate_metrics(dataset, await cluster_cases(dataset, present, embedder, duplicate_threshold))
    metrics["duplicate_precision"] = dup["precision"]
    metrics["duplicate_recall"] = dup["recall"]

    not_run_reasons: Dict[str, str] = {}
    if embedder_unavailable:
        # Without the requested embedder the near-duplicate pairs cannot be judged: not run, not failed.
        metrics["duplicate_precision"] = metrics["duplicate_recall"] = None
        not_run_reasons["duplicate_precision"] = not_run_reasons["duplicate_recall"] = embedder_unavailable
    geval: List[Dict[str, Any]] = []
    geval_min = float(thresholds["metrics"]["geval_faithfulness"]["min"])
    if judge is None:
        metrics["geval_faithfulness"] = None
        not_run_reasons["geval_faithfulness"] = "no judge configured (EVAL_JUDGE_MODEL_KEY unset; decision D2)"
    else:
        try:
            geval = faithfulness_scores(
                [
                    {"id": c.id, "input": f"Analyze log bundle {c.log_path.name}", "output": present[c.id], "context": parse_log_text(c.log_text)}
                    for c in dataset.cases
                    if c.id in present
                ],
                judge,
                geval_min,
            )
            metrics["geval_faithfulness"] = sum(g["score"] for g in geval) / len(geval) if geval else None
        except JudgeUnavailable as exc:
            metrics["geval_faithfulness"] = None
            not_run_reasons["geval_faithfulness"] = str(exc)

    missing = [c.id for c in dataset.cases if outputs.get(c.id) is None]
    gates: Dict[str, Dict[str, Any]] = {}
    for name, spec in thresholds["metrics"].items():
        mandatory = mode in spec.get("modes", [])
        value = metrics.get(name)
        if value is None:
            status = "not_run"
        else:
            status = "passed" if value >= float(spec["min"]) else "failed"
        if mandatory and missing and name != "geval_faithfulness":
            # Scores over a partial set are not evidence: every case must produce an output.
            status = "not_run" if status == "passed" else status
            not_run_reasons.setdefault(name, f"{len(missing)} case(s) produced no output: {missing}")
        gates[name] = {"value": value, "min": spec["min"], "mandatory": mandatory, "status": status, "reason": not_run_reasons.get(name)}
    mandatory_status = [g["status"] for g in gates.values() if g["mandatory"]]
    if "failed" in mandatory_status:
        exit_code = EXIT_BELOW_THRESHOLD
    elif "not_run" in mandatory_status:
        exit_code = EXIT_NOT_RUN
    else:
        exit_code = EXIT_PASS

    catalog_agent = next(a for a in load_registry_catalog(REPO_ROOT).agents if a.slug == "bug-analyzer")
    return {
        "meta": {
            "mode": mode,
            "dataset": f"{dataset.name}/{dataset.version}",
            "dataset_parser_version": dataset.parser_version,
            "parser_version": PARSER_VERSION,
            "agent": {"slug": catalog_agent.slug, "version": catalog_agent.version, "prompt_hash": catalog_agent.prompt_hash},
            "embedder": getattr(embedder, "model_identity", None),
            "duplicate_threshold": duplicate_threshold,
            "judge": judge.get_model_name() if judge is not None else None,
            "git_sha": _git_sha(),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "outputs_are_model_generated": mode != "reference",
            **(extra_meta or {}),
        },
        "gates": gates,
        "passed": exit_code == EXIT_PASS,
        "exit_code": exit_code,
        "cases": [
            {"id": s.case_id, "expected": s.expected, "actual": s.actual, "problems": s.problems, "run_error": (run_errors or {}).get(s.case_id)}
            for s in case_scores
        ]
        + [{"id": m, "problems": ["no output"], "run_error": (run_errors or {}).get(m)} for m in missing],
        "duplicates": dup,
        "geval": geval,
    }


LIVE_MODES = ("live", "ac4", "preflight")
WEAKENED_PROMPT = EVALS_DIR / "prompts" / "bug-analyzer-weakened.md"
AC4_GATES = ("severity_accuracy", "signature_accuracy", "grounding_rate", "geval_faithfulness")
CALLS_PER_CASE = 3  # agent: log.parse request + report; judge: one G-Eval call (fixed steps). Repairs add more.


def subset(dataset: Dataset, case_ids: Optional[List[str]]) -> Dataset:
    if not case_ids:
        return dataset
    unknown = sorted(set(case_ids) - {c.id for c in dataset.cases})
    if unknown:
        raise SystemExit(f"unknown case ids: {unknown}")
    keep = set(case_ids)
    return replace(
        dataset,
        cases=[c for c in dataset.cases if c.id in keep],
        duplicates=[d for d in dataset.duplicates if d["case"] in keep and d["of"] in keep],
        distinct=[d for d in dataset.distinct if d["a"] in keep and d["b"] in keep],
    )


def ac1_verdict(report: Mapping[str, Any]) -> Dict[str, Any]:
    """AC1: correct severity on >= 9/10 golden cases (needs at least 10 evaluated, model-generated cases)."""
    scored = [c for c in report["cases"] if "actual" in c]
    correct = sum(1 for c in scored if c["actual"].get("severity") == c["expected"]["severity"])
    enough = len(scored) >= 10
    rate = correct / len(scored) if scored else 0.0
    return {
        "cases_evaluated": len(scored),
        "correct": correct,
        "accuracy": round(rate, 4),
        "model_generated": bool(report["meta"].get("outputs_are_model_generated")),
        "verdict": "PASS" if enough and rate >= 0.9 and report["meta"].get("outputs_are_model_generated") else ("NOT VERIFIED" if not enough else "FAIL"),
    }


def live_problems(env: Mapping[str, str], mode: str) -> List[str]:
    """Why a live evaluation must not start (empty list = allowed). Never touches the network."""
    problems: List[str] = []
    if env.get("GITHUB_ACTIONS") == "true" or env.get("CI", "").lower() == "true":
        problems.append("live model evaluation is local-only and refused under CI")
    if not env.get("LITELLM_MASTER_KEY"):
        problems.append("LITELLM_MASTER_KEY is not set (local LiteLLM gateway with GEMINI_API_KEY)")
    if mode != "preflight" and not env.get("EVAL_JUDGE_MODEL_KEY"):
        problems.append("EVAL_JUDGE_MODEL_KEY is not set (e.g. judge -> gemini/gemini-2.5-flash-lite)")
    try:
        prices_from_env(env)
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def make_budget(args: argparse.Namespace, env: Mapping[str, str]) -> CallBudget:
    return CallBudget(
        max_calls=args.max_calls,
        max_cost_usd=args.max_cost_usd,
        prices=prices_from_env(env),
        judge_model_key=env.get("EVAL_JUDGE_MODEL_KEY"),
        min_interval_s=args.min_interval_seconds,
    )


def budgeted_client(settings: Settings, budget: CallBudget) -> LiteLLMClient:
    return LiteLLMClient(
        base_url=settings.LITELLM_ENDPOINT,
        api_key=settings.LITELLM_MASTER_KEY.get_secret_value(),
        timeout_seconds=settings.LITELLM_TIMEOUT_SECONDS,
        transport=BudgetTransport(budget),
    )


async def preflight(settings: Settings, budget: CallBudget, env: Mapping[str, str]) -> Dict[str, Any]:
    """One minimal call per alias: proves access (and shows quota errors) before a real run."""
    client = budgeted_client(settings, budget)
    keys = [env.get("EVAL_AGENT_MODEL_KEY", "classifier")] + ([env["EVAL_JUDGE_MODEL_KEY"]] if env.get("EVAL_JUDGE_MODEL_KEY") else [])
    results: Dict[str, Any] = {}
    try:
        for key in keys:
            try:
                res = await client._http.post(  # minimal raw call; same transport and budget as the agent
                    f"{settings.LITELLM_ENDPOINT.rstrip('/')}/v1/chat/completions",
                    json={"model": key, "messages": [{"role": "user", "content": "Reply with the single word OK."}], "max_tokens": 5},
                    headers={"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY.get_secret_value()}"},
                )
                body = res.json() if res.content else {}
                err = body.get("error", {}) if isinstance(body, dict) else {}
                results[key] = {
                    "status": res.status_code,
                    "model": body.get("model") if isinstance(body, dict) else None,
                    "usage": body.get("usage") if isinstance(body, dict) else None,
                    "error": redact(str(err.get("message", "")))[:300] if err else None,
                }
            except BudgetExceeded as exc:
                results[key] = {"status": None, "error": str(exc)}
            except httpx.HTTPError as exc:  # timeout / connection error: report, never retry
                results[key] = {"status": None, "error": f"{type(exc).__name__}: {redact(str(exc))[:200]}"}
    finally:
        await client.aclose()
    return results


def redact(text: str) -> str:
    return re.sub(r"(AIza|AQ\.|sk-)[A-Za-z0-9_\-.]+", "<redacted>", text)


def print_gates(report: Mapping[str, Any]) -> None:
    for name, gate in report["gates"].items():
        flag = "" if gate["mandatory"] else " (informational)"
        value = "-" if gate["value"] is None else f"{gate['value']:.3f}"
        print(f"{gate['status'].upper():8} {name:22} {value:>6} (min {gate['min']}){flag}{' - ' + gate['reason'] if gate['reason'] else ''}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["reference", "replay", *LIVE_MODES], default="reference")
    ap.add_argument("--outputs", type=pathlib.Path, action="append", help="recorded outputs JSON (replay; repeat to merge)")
    ap.add_argument("--thresholds", type=pathlib.Path, default=EVALS_DIR / "thresholds.yaml")
    ap.add_argument("--out", type=pathlib.Path, default=EVALS_DIR / "results" / "bug-analyzer-v1.json")
    ap.add_argument("--embedder", choices=["fake", "gateway"], default="fake", help="duplicate-gate embedder (default fake: no embedding calls)")
    ap.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    ap.add_argument("--max-cases", type=int, default=None)
    ap.add_argument("--prompt-file", type=pathlib.Path, default=None, help="live: run the agent with this system prompt instead")
    ap.add_argument("--weakened-prompt", type=pathlib.Path, default=WEAKENED_PROMPT, help="ac4: the deliberately weakened prompt")
    ap.add_argument("--allow-live", action="store_true", help="required for any real model call (live, ac4, preflight)")
    ap.add_argument("--dry-run", action="store_true", help="check configuration and print the plan; no network calls")
    ap.add_argument("--max-calls", type=int, default=int(os.environ.get("EVAL_MAX_CALLS", "60")))
    ap.add_argument("--max-cost-usd", type=float, default=float(os.environ.get("EVAL_MAX_COST_USD", "0.50")))
    ap.add_argument("--min-interval-seconds", type=float, default=float(os.environ.get("EVAL_MIN_INTERVAL_SECONDS", "6.5")))
    args = ap.parse_args(argv)
    env = os.environ
    case_ids = [c.strip() for c in args.cases.split(",") if c.strip()] if args.cases else None
    dataset = subset(load_dataset(), case_ids)
    if args.max_cases:
        dataset = subset(dataset, [c.id for c in dataset.cases[: args.max_cases]])
    thresholds = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))
    settings = Settings()

    if args.mode in LIVE_MODES:
        problems = live_problems(env, args.mode)
        runs = 2 if args.mode == "ac4" else 1
        planned = 2 if args.mode == "preflight" else runs * len(dataset.cases) * CALLS_PER_CASE
        if args.dry_run or problems or not args.allow_live:
            plan = {
                "mode": args.mode,
                "cases": [c.id for c in dataset.cases],
                "planned_calls_without_repairs": planned,
                "max_calls": args.max_calls,
                "max_cost_usd": args.max_cost_usd,
                "min_interval_seconds": args.min_interval_seconds,
                "judge_model_key": env.get("EVAL_JUDGE_MODEL_KEY"),
                "allow_live": args.allow_live,
                "problems": problems
                + ([] if args.allow_live or args.dry_run else ["--allow-live not given"])
                + ([f"planned calls ({planned}) exceed --max-calls ({args.max_calls}); use --cases or raise the limit"] if planned > args.max_calls else []),
            }
            print(json.dumps(plan, indent=2))
            ok = not plan["problems"]
            print("dry run: no model was called" if args.dry_run else "refused: no model was called")
            return EXIT_PASS if (args.dry_run and ok) else EXIT_NOT_RUN

    if args.embedder == "fake":
        embedder: Optional[Embedder] = HashingEmbedder(256)
        dup_threshold = float(thresholds["duplicate_threshold"]["fake"])
    else:
        embedder = embedder_from_settings(settings)
        dup_threshold = settings.DUPLICATE_SIMILARITY_THRESHOLD
    embedder_unavailable = "gateway embedder requested but EMBEDDINGS_ENABLED is false" if embedder is None else None
    budget = make_budget(args, env) if args.mode in LIVE_MODES else None
    judge = judge_from_env(env, budget) if args.mode in LIVE_MODES and args.allow_live else None
    args.out.parent.mkdir(parents=True, exist_ok=True)

    async def evaluate_outputs(mode: str, outputs: Dict[str, Optional[Dict[str, Any]]], errors: Dict[str, str], extra: Dict[str, Any]) -> Dict[str, Any]:
        return await evaluate(
            mode=mode,
            dataset=dataset,
            outputs={c.id: outputs.get(c.id) for c in dataset.cases},
            thresholds=thresholds,
            embedder=embedder,
            duplicate_threshold=dup_threshold,
            judge=judge,
            embedder_unavailable=embedder_unavailable,
            run_errors=errors,
            extra_meta=extra,
        )

    async def live_outputs(prompt: Optional[str], label: str) -> Tuple[Dict[str, Optional[Dict[str, Any]]], Dict[str, str], Dict[str, Any]]:
        from qa_evals.live import run_live

        assert budget is not None
        client = budgeted_client(settings, budget)
        try:
            live = await run_live(
                dataset, settings=settings, client=client, max_cost_usd=args.max_cost_usd, prompt_override=prompt, budget=budget
            )
        finally:
            await client.aclose()
        outputs, errors = dict(live.outputs), dict(live.run_errors)
        for case_id in live.not_run:
            outputs[case_id] = None
            errors[case_id] = f"not run: {budget.stop_reason or 'catalog spend cap reached'}"
        extra = {"prompt": label, "agent": live.agent, "models": live.models, "tool_calls": live.tool_calls, "catalog_cost_usd": round(live.cost_usd, 6)}
        recorded = args.out.with_name(f"{args.out.stem}-{label}-outputs.json")
        recorded.write_text(json.dumps({"dataset": "bug-analyzer/v1", "meta": extra, "outputs": outputs}, indent=2), encoding="utf-8")
        return outputs, errors, extra

    async def run() -> Tuple[int, Dict[str, Any]]:
        try:
            if args.mode == "preflight":
                assert budget is not None
                result = {"preflight": await preflight(settings, budget, env), "budget": budget.summary()}
                ok = all(r.get("status") == 200 for r in result["preflight"].values())
                return (EXIT_PASS if ok else EXIT_NOT_RUN), result
            if args.mode == "reference":
                report = await evaluate_outputs("reference", dict(load_outputs(dataset.root / "reference_outputs.json")), {}, {})
                return report["exit_code"], report
            if args.mode == "replay":
                if not args.outputs:
                    raise SystemExit("--outputs is required in replay mode")
                merged: Dict[str, Optional[Dict[str, Any]]] = {}
                for path in args.outputs:
                    merged.update(load_outputs(path))
                report = await evaluate_outputs("replay", merged, {}, {"merged_from": [str(p) for p in args.outputs]})
                report["ac1"] = ac1_verdict(report)
                return report["exit_code"], report
            prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else None
            outputs, errors, extra = await live_outputs(prompt, "custom-prompt" if prompt else "baseline")
            report = await evaluate_outputs("live", outputs, errors, extra)
            if args.mode == "live":
                report["ac1"] = ac1_verdict(report)
                report["budget"] = budget.summary() if budget else None
                return report["exit_code"], report
            weak_out, weak_err, weak_extra = await live_outputs(args.weakened_prompt.read_text(encoding="utf-8"), "weakened")
            weak = await evaluate_outputs("live", weak_out, weak_err, weak_extra)
            statuses = [r["gates"][g]["status"] for r in (report, weak) for g in AC4_GATES]
            if "not_run" in statuses:
                code, verdict = EXIT_NOT_RUN, "NOT VERIFIED (a required gate did not run)"
            elif report["exit_code"] == EXIT_PASS and weak["exit_code"] == EXIT_BELOW_THRESHOLD:
                code, verdict = EXIT_PASS, "PASS: baseline passes, weakened prompt fails the gate"
            else:
                code, verdict = EXIT_BELOW_THRESHOLD, "FAIL: the gate did not separate baseline from weakened prompt"
            return code, {
                "ac4": {"verdict": verdict, "baseline_exit": report["exit_code"], "weakened_exit": weak["exit_code"]},
                "ac1_baseline": ac1_verdict(report),
                "baseline": report,
                "weakened": weak,
                "budget": budget.summary() if budget else None,
            }
        finally:
            if embedder is not None:
                await embedder.aclose()

    code, result = asyncio.run(run())
    args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if "gates" in result:
        print_gates(result)
    for key in ("ac1", "ac4", "ac1_baseline"):
        if key in result:
            print(f"{key}: {json.dumps(result[key])}")
    if args.mode == "ac4":
        for label in ("baseline", "weakened"):
            print(f"--- {label}")
            print_gates(result[label])
    if args.mode == "preflight":
        print(json.dumps(result["preflight"], indent=2))
    if result.get("budget"):
        b = result["budget"]
        print(f"budget: calls={b['calls']} estimated_usd={b['estimated_cost_usd']} total={b['estimated_total_usd']} stop={b['stop_reason']}")
    print(f"exit {code} -> {args.out}")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
