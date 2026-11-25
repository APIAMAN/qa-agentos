"""Self-contained HTML rendering of a run report.

Every dynamic value passes through ``html.escape``; the page has no scripts and a CSP that
forbids loading anything, so a hostile string in run input, model output or a log cannot run.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any, Iterable, List, Mapping, Optional, Sequence

_CSS = """
body{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px;color:#1f2328;background:#fff}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:4px 0}th,td{border:1px solid #d0d7de;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600}pre{background:#f6f8fa;padding:8px;overflow:auto;white-space:pre-wrap;word-break:break-word;max-height:480px}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-weight:600;font-size:12px}
.succeeded{background:#dafbe1;color:#116329}.failed{background:#ffebe9;color:#a40e26}.cancelled{background:#eaeef2;color:#424a53}
.incomplete{background:#fff8c5;color:#7d4e00}.muted{color:#656d76}.sev-critical{background:#a40e26;color:#fff}
.sev-high{background:#ffebe9;color:#a40e26}.sev-medium{background:#fff8c5;color:#7d4e00}.sev-low{background:#eaeef2;color:#424a53}
"""


def _e(value: Any) -> str:
    if value is None:
        return '<span class="muted">—</span>'
    return escape(str(value), quote=True)


def _json_block(value: Any) -> str:
    return f"<pre>{escape(json.dumps(value, indent=2, sort_keys=True, default=str))}</pre>"


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *, raw_cols: Sequence[int] = ()) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{cell if i in raw_cols else _e(cell)}</td>" for i, cell in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    if not body:
        return '<p class="muted">None.</p>'
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _kv(pairs: Sequence[tuple[str, Any]]) -> str:
    return _table(["Field", "Value"], pairs)


def _bug_section(bug: Mapping[str, Any]) -> str:
    sev = str(bug.get("severity") or "")
    sev_badge = f'<span class="badge sev-{escape(sev)}">{_e(sev)}</span>'
    cluster = bug.get("cluster") or {}
    match = bug.get("match") or {}
    parts = [
        "<h2>Bug analysis</h2>",
        _table(
            ["Field", "Value"],
            [
                ("Title", _e(bug.get("title"))),
                ("Severity", sev_badge),
                ("Category", _e(bug.get("category"))),
                ("Signature", _e(bug.get("signature"))),
                ("Summary", _e(bug.get("summary"))),
                ("Likely root cause", _e(bug.get("root_cause"))),
            ],
            raw_cols=(1,),
        ),
        "<h3>Duplicate detection</h3>",
        _kv(
            [
                ("Duplicate of existing cluster", "yes" if bug.get("is_duplicate") else "no"),
                ("Cluster", cluster.get("id")),
                ("Cluster reports", cluster.get("report_count")),
                ("Match method", match.get("method")),
                ("Similarity", match.get("similarity")),
                ("Threshold", match.get("threshold")),
                ("Matched bug", match.get("matched_bug_id")),
            ]
        ),
    ]
    related = bug.get("related_bugs") or []
    parts.append("<h3>Related bugs</h3>")
    parts.append(
        _table(
            ["Bug", "Run", "Title", "Severity", "Created"],
            [(r.get("id"), r.get("run_id"), r.get("title"), r.get("severity"), r.get("created_at")) for r in related],
        )
    )
    evidence = bug.get("evidence") or []
    parts.append("<h3>Evidence</h3>")
    parts.append(
        _table(
            ["Signature", "Detail"],
            [(ev.get("signature"), ev.get("detail")) for ev in evidence if isinstance(ev, Mapping)],
        )
    )
    repro = bug.get("repro_steps") or []
    if repro:
        items = "".join(f"<li>{_e(step)}</li>" for step in repro)
        parts.append(f"<h3>Reproduction steps</h3><ol>{items}</ol>")
    return "".join(parts)


def render_report_html(
    report: Mapping[str, Any],
    *,
    version: Optional[int] = None,
    embedded: Optional[Mapping[str, Optional[str]]] = None,
) -> str:
    """Render ``report`` (a schema-valid run report) as one standalone HTML document.

    ``embedded`` maps artifact id → text content to show inline (``None`` = not embeddable).
    """
    run = report["run"]
    agent = run.get("agent") or {}
    usage = report["model_usage"]
    totals = usage["totals"]
    verdict = str(run.get("verdict"))
    embedded = embedded or {}
    title = f"Run report — {agent.get('name') or agent.get('slug') or 'agent'} — {run['id']}"

    sections: List[str] = [
        f"<h1>{_e(title)}</h1>",
        f'<p><span class="badge {escape(verdict)}">{_e(verdict)}</span> '
        f'<span class="muted">report v{_e(version) if version else "(preview)"} · schema {_e(report["schema_version"])}'
        f" · generated {_e(report['generated_at'])}</span></p>",
        "<h2>Summary</h2>",
        _kv(
            [
                ("Run", run["id"]),
                ("Project", run["project_id"]),
                ("Agent", f"{agent.get('name') or ''} ({agent.get('slug') or '—'} v{agent.get('version') or '—'})"),
                ("Prompt hash", agent.get("prompt_hash")),
                ("Status", run["status"]),
                ("Attempts", run["attempt_count"]),
                ("Queued", run.get("queued_at")),
                ("Started", run.get("started_at")),
                ("Finished", run.get("finished_at")),
                ("Duration (ms)", run.get("duration_ms")),
                ("Replayed from", run.get("replayed_from")),
            ]
        ),
    ]

    error = report.get("error")
    if error:
        sections.append("<h2>Error</h2>")
        sections.append(_kv([("Code", error.get("code")), ("Message", error.get("message"))]))
        if error.get("details"):
            sections.append(_json_block(error["details"]))

    if report.get("bug"):
        sections.append(_bug_section(report["bug"]))

    sections.append("<h2>Attempt history</h2>")
    sections.append(_table(["Attempt", "Kind", "At"], [(a["attempt"], a["kind"], a["at"]) for a in report["attempts"]]))

    sections.append("<h2>Status timeline</h2>")
    sections.append(
        _table(
            ["#", "From", "To", "Event", "At"],
            [(e["seq"], e["from_status"], e["to_status"], e["event"], e["at"]) for e in report["status_timeline"]],
        )
    )

    sections.append("<h2>Steps</h2>")
    sections.append(
        _table(
            ["Step", "Attempt", "Node", "Type", "Status", "Latency (ms)", "Tokens in/out", "Error"],
            [
                (
                    s["step_number"],
                    s["attempt"],
                    s["node"],
                    s["step_type"],
                    s["status"],
                    s["latency_ms"],
                    f"{s['tokens_input']}/{s['tokens_output']}",
                    (s.get("error") or {}).get("code") if isinstance(s.get("error"), dict) else s.get("error"),
                )
                for s in report["steps"]
            ],
        )
    )

    sections.append("<h2>Model usage</h2>")
    sections.append(
        _table(
            ["Capability", "Gateway model", "Calls", "Failed", "Tokens in", "Tokens out", "Cost (USD)", "Latency (ms)"],
            [
                (
                    m["capability"],
                    m["gateway_model_key"],
                    m["usage"]["calls"],
                    m["usage"]["failed_calls"],
                    m["usage"]["tokens_input"],
                    m["usage"]["tokens_output"],
                    f"{m['usage']['cost_usd']:.6f}",
                    m["usage"]["latency_ms"],
                )
                for m in usage["by_model"]
            ]
            + [
                (
                    "TOTAL",
                    "",
                    totals["calls"],
                    totals["failed_calls"],
                    totals["tokens_input"],
                    totals["tokens_output"],
                    f"{totals['cost_usd']:.6f}",
                    totals["latency_ms"],
                )
            ],
        )
    )

    sections.append("<h2>Approvals</h2>")
    sections.append(
        _table(
            ["Action", "Prompt", "Status", "Decided by", "Note", "Requested", "Decided"],
            [
                (a["action_type"], a["prompt_text"], a["status"], a["decision_by"], a["decision_note"], a["created_at"], a["decided_at"])
                for a in report["approvals"]
            ],
        )
    )

    sections.append("<h2>Artifacts</h2>")
    sections.append(
        _table(
            ["Name", "Type", "Size (bytes)", "SHA-256", "Reference"],
            [(a["name"], a["artifact_type"], a["file_size_bytes"], a["content_hash"], a["href"]) for a in report["artifacts"]],
        )
    )
    for art in report["artifacts"]:
        content = embedded.get(art["id"])
        if content is not None:
            sections.append(f"<h3>{_e(art['name'])}</h3><pre>{escape(content)}</pre>")

    if run.get("output") is not None:
        sections.append("<h2>Output</h2>")
        sections.append(_json_block(run["output"]))
    sections.append("<h2>Input</h2>")
    sections.append(_json_block(run.get("input") or {}))

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>{''.join(sections)}</body></html>\n"
    )
