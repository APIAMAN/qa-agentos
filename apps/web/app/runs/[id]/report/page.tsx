"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  Bug,
  Download,
  FileJson,
  FileText,
  Loader2,
  RefreshCw,
  Share2,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

/* ---------- Types mirror apps/api/src/qa_agentos/modules/reports/report.schema.json ---------- */

interface Usage {
  calls: number;
  failed_calls: number;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  latency_ms: number;
}

interface BugSection {
  id: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low" | string;
  category: string;
  summary: string;
  root_cause: string | null;
  signature: string | null;
  evidence: { signature?: string | null; detail?: string }[];
  repro_steps: string[];
  is_duplicate: boolean;
  grounded: boolean;
  match: { method: string; similarity: number | null; threshold: number | null; matched_bug_id: string | null } | null;
  cluster: { id: string; title: string; severity: string; report_count: number } | null;
  related_bugs: { id: string; run_id: string; title: string; severity: string; created_at: string }[];
}

interface RunReport {
  schema_version: string;
  generated_at: string;
  run: {
    id: string;
    project_id: string;
    status: string;
    verdict: "succeeded" | "failed" | "cancelled" | "incomplete";
    is_final: boolean;
    agent: { agent_version_id: string; slug: string | null; name: string | null; version: string | null; prompt_hash: string | null };
    attempt_count: number;
    replayed_from: string | null;
    queued_at: string | null;
    started_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    input: Record<string, unknown>;
    output: Record<string, unknown> | null;
  };
  error: { code: string | null; message: string | null; details: Record<string, unknown> | null } | null;
  attempts: { attempt: number | null; kind: string; at: string | null }[];
  status_timeline: { seq: number; from_status: string | null; to_status: string; event: string | null; at: string | null }[];
  steps: {
    step_number: number;
    attempt: number;
    node: string;
    step_type: string;
    status: string;
    latency_ms: number | null;
    tokens_input: number;
    tokens_output: number;
    error: Record<string, unknown> | null;
  }[];
  model_usage: { totals: Usage; by_model: { capability: string; gateway_model_key: string; model_id: string | null; usage: Usage }[] };
  approvals: { id: string; action_type: string; prompt_text: string; status: string; decision_note: string | null; created_at: string | null; decided_at: string | null }[];
  artifacts: { id: string; name: string; artifact_type: string; content_hash: string; file_size_bytes: number; created_at: string | null; href: string }[];
  bug?: BugSection | null;
}

interface ReportVersion {
  id: string;
  version: number;
  verdict: string;
  content_hash: string;
  created_at: string;
  report_url: string;
  json_artifact: { id: string; href: string } | null;
  html_artifact: { id: string; href: string } | null;
}

/* ---------- Small presentational helpers ---------- */

const VERDICT_STYLE: Record<string, string> = {
  succeeded: "bg-emerald-950 text-emerald-300 border-emerald-800",
  failed: "bg-rose-950 text-rose-300 border-rose-800",
  cancelled: "bg-slate-900 text-slate-300 border-slate-700",
  incomplete: "bg-amber-950 text-amber-300 border-amber-800",
};

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-rose-700 text-white border-rose-500",
  high: "bg-rose-950 text-rose-300 border-rose-800",
  medium: "bg-amber-950 text-amber-300 border-amber-800",
  low: "bg-slate-900 text-slate-300 border-slate-700",
};

function Badge({ text, style }: { text: string; style?: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[11px] font-semibold uppercase tracking-wide ${style || "bg-slate-900 text-slate-300 border-slate-700"}`}>
      {text}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-3">
      <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      {children}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-xs text-slate-500">{text}</p>;
}

function Table({ headers, rows }: { headers: string[]; rows: React.ReactNode[][] }) {
  if (rows.length === 0) return <Empty text="None." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-left border-collapse">
        <thead>
          <tr className="text-slate-400 border-b border-slate-800">
            {headers.map((h) => (
              <th key={h} className="py-2 pr-4 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i} className="border-b border-slate-900 text-slate-300">
              {cells.map((c, j) => (
                <td key={j} className="py-1.5 pr-4 align-top font-mono">
                  {c ?? <span className="text-slate-600">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtTime(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleString() : "—";
}

function fmtDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/* ---------- Page ---------- */

function RunReportView() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const runId = params.id as string;
  const versionParam = searchParams.get("version");
  const { apiFetch, selectedProject } = useAuth();

  const [report, setReport] = useState<RunReport | null>(null);
  const [versions, setVersions] = useState<ReportVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const downloadArtifact = useCallback(
    async (artifactId: string): Promise<Blob> => {
      const res = await apiFetch(`/v1/artifacts/${artifactId}/download`);
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      return res.blob();
    },
    [apiFetch]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resVersions = await apiFetch(`/v1/runs/${runId}/reports`);
      if (resVersions.status === 404) throw new Error("Run not found in the selected project.");
      if (!resVersions.ok) throw new Error(`Could not load report versions (${resVersions.status})`);
      const list: ReportVersion[] = await resVersions.json();
      setVersions(list);

      if (versionParam) {
        // A version link renders exactly the exported JSON of that version.
        const selected = list.find((v) => String(v.version) === versionParam);
        if (!selected || !selected.json_artifact) throw new Error(`Report version ${versionParam} not found.`);
        const blob = await downloadArtifact(selected.json_artifact.id);
        setReport(JSON.parse(await blob.text()) as RunReport);
      } else {
        const res = await apiFetch(`/v1/runs/${runId}/report`);
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.error?.message || `Could not load report (${res.status})`);
        }
        setReport((await res.json()) as RunReport);
      }
    } catch (err: unknown) {
      setReport(null);
      setError(err instanceof Error ? err.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, runId, versionParam, downloadArtifact]);

  useEffect(() => {
    load();
  }, [load, selectedProject?.id]);

  const handleExport = async () => {
    setExporting(true);
    setNotice(null);
    try {
      const res = await apiFetch(`/v1/runs/${runId}/report:export`, { method: "POST" });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(body?.error?.message || `Export failed (${res.status})`);
      setNotice(res.status === 201 ? `Exported report v${body.version}.` : `No changes since v${body.version}; existing version kept.`);
      await load();
    } catch (err: unknown) {
      setNotice(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const saveFile = async (artifactId: string, filename: string) => {
    try {
      const blob = await downloadArtifact(artifactId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setNotice(err instanceof Error ? err.message : "Download failed");
    }
  };

  const copyLink = async () => {
    const latest = versions[versions.length - 1];
    const path = versionParam ? `/runs/${runId}/report?version=${versionParam}` : latest ? latest.report_url : `/runs/${runId}/report`;
    await navigator.clipboard.writeText(`${window.location.origin}${path}`);
    setNotice("Report link copied.");
  };

  const run = report?.run;
  const totals = report?.model_usage.totals;

  return (
    <ConsoleShell>
      <div className="space-y-6 max-w-7xl mx-auto pb-12" data-testid="run-report-page">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
          <div className="space-y-1">
            <Link href={`/runs/${runId}`} className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors mb-1">
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to Run
            </Link>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-bold tracking-tight text-slate-100">Run Report</h1>
              {run && <Badge text={run.verdict} style={VERDICT_STYLE[run.verdict]} />}
              {versionParam && <Badge text={`version ${versionParam}`} />}
            </div>
            <p className="text-xs font-mono text-slate-500">{runId}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {versions.length > 0 && (
              <select
                aria-label="Report version"
                value={versionParam || ""}
                onChange={(e) => router.push(e.target.value ? `/runs/${runId}/report?version=${e.target.value}` : `/runs/${runId}/report`)}
                className="bg-slate-900 border border-slate-800 rounded-lg px-2 py-2 text-xs text-slate-200"
              >
                <option value="">Live report</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version} · {fmtTime(v.created_at)}
                  </option>
                ))}
              </select>
            )}
            <button onClick={() => load()} className="p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 text-xs flex items-center gap-1.5">
              <RefreshCw className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Refresh</span>
            </button>
            <button onClick={copyLink} className="p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 text-xs flex items-center gap-1.5">
              <Share2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Copy link</span>
            </button>
            {run?.is_final && (
              <button
                onClick={handleExport}
                disabled={exporting}
                className="px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs flex items-center gap-1.5 disabled:opacity-50 font-medium"
              >
                {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
                {exporting ? "Exporting..." : "Export version"}
              </button>
            )}
          </div>
        </div>

        {notice && <div className="text-xs text-slate-300 bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">{notice}</div>}

        {loading && (
          <div className="flex items-center gap-2 text-sm text-slate-400 py-12 justify-center" data-testid="report-loading">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading report...
          </div>
        )}

        {!loading && error && (
          <div className="flex items-start gap-3 p-4 bg-rose-950/40 border border-rose-900 rounded-xl text-sm text-rose-200" data-testid="report-error">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Report unavailable</p>
              <p className="text-xs text-rose-300/80 mt-1">{error}</p>
            </div>
          </div>
        )}

        {!loading && !error && report && run && totals && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                ["Agent", `${run.agent.name || run.agent.slug || "—"} v${run.agent.version || "—"}`],
                ["Status", run.status],
                ["Duration", fmtDuration(run.duration_ms)],
                ["Attempts", String(run.attempt_count)],
                ["Model calls", `${totals.calls} (${totals.failed_calls} failed)`],
                ["Tokens in / out", `${totals.tokens_input} / ${totals.tokens_output}`],
                ["Cost", `$${totals.cost_usd.toFixed(6)}`],
                ["Finished", fmtTime(run.finished_at)],
              ].map(([label, value]) => (
                <div key={label} className="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                  <p className="text-[11px] text-slate-500">{label}</p>
                  <p className="text-sm text-slate-200 font-mono truncate" title={value}>
                    {value}
                  </p>
                </div>
              ))}
            </div>

            {report.error && (
              <Section title="Error">
                <p className="text-xs font-mono text-rose-300">{report.error.code}</p>
                <p className="text-xs text-slate-300">{report.error.message}</p>
                {report.error.details && <pre className="text-[11px] bg-slate-950 border border-slate-800 rounded-lg p-3 overflow-auto">{JSON.stringify(report.error.details, null, 2)}</pre>}
              </Section>
            )}

            {report.bug && (
              <Section title="Bug analysis">
                <div className="flex flex-wrap items-center gap-2">
                  <Bug className="h-4 w-4 text-rose-300" />
                  <span className="text-sm font-semibold text-slate-100">{report.bug.title}</span>
                  <Badge text={report.bug.severity} style={SEVERITY_STYLE[report.bug.severity]} />
                  <Badge text={report.bug.category} />
                  {report.bug.is_duplicate && <Badge text="duplicate" style="bg-indigo-950 text-indigo-300 border-indigo-800" />}
                  {!report.bug.grounded && <Badge text="ungrounded" style="bg-amber-950 text-amber-300 border-amber-800" />}
                </div>
                <p className="text-xs text-slate-300">{report.bug.summary}</p>
                {report.bug.root_cause && (
                  <p className="text-xs text-slate-400">
                    <span className="text-slate-500">Likely root cause: </span>
                    {report.bug.root_cause}
                  </p>
                )}
                <p className="text-[11px] font-mono text-slate-500">signature: {report.bug.signature || "—"}</p>
                <Table
                  headers={["Cluster", "Cluster reports", "Match", "Similarity", "Threshold"]}
                  rows={[
                    [
                      report.bug.cluster?.id ?? null,
                      report.bug.cluster?.report_count ?? null,
                      report.bug.match?.method ?? null,
                      report.bug.match?.similarity ?? null,
                      report.bug.match?.threshold ?? null,
                    ],
                  ]}
                />
                <h3 className="text-xs font-semibold text-slate-300 pt-2">Evidence</h3>
                <Table headers={["Signature", "Detail"]} rows={report.bug.evidence.map((e) => [e.signature ?? null, e.detail ?? null])} />
                {report.bug.repro_steps.length > 0 && (
                  <>
                    <h3 className="text-xs font-semibold text-slate-300 pt-2">Reproduction steps</h3>
                    <ol className="list-decimal list-inside text-xs text-slate-300 space-y-0.5">
                      {report.bug.repro_steps.map((s, i) => (
                        <li key={i}>{s}</li>
                      ))}
                    </ol>
                  </>
                )}
                <h3 className="text-xs font-semibold text-slate-300 pt-2">Related bugs</h3>
                <Table
                  headers={["Title", "Severity", "Run", "Created"]}
                  rows={report.bug.related_bugs.map((r) => [
                    r.title,
                    r.severity,
                    <Link key={r.run_id} href={`/runs/${r.run_id}/report`} className="text-indigo-300 hover:underline">
                      {r.run_id.slice(0, 8)}
                    </Link>,
                    fmtTime(r.created_at),
                  ])}
                />
              </Section>
            )}

            <Section title="Attempt history">
              {report.attempts.length === 0 ? (
                <Empty text="Single attempt; no retries or resumes." />
              ) : (
                <Table headers={["Attempt", "Kind", "At"]} rows={report.attempts.map((a) => [a.attempt, a.kind, fmtTime(a.at)])} />
              )}
            </Section>

            <Section title="Status timeline">
              <Table
                headers={["#", "From", "To", "Event", "At"]}
                rows={report.status_timeline.map((e) => [e.seq, e.from_status, e.to_status, e.event, fmtTime(e.at)])}
              />
            </Section>

            <Section title="Steps">
              <Table
                headers={["Step", "Attempt", "Node", "Type", "Status", "Latency", "Tokens in/out", "Error"]}
                rows={report.steps.map((s) => [
                  s.step_number,
                  s.attempt,
                  s.node,
                  s.step_type,
                  s.status,
                  s.latency_ms === null ? null : `${s.latency_ms} ms`,
                  `${s.tokens_input}/${s.tokens_output}`,
                  s.error ? String((s.error as Record<string, unknown>).code ?? "error") : null,
                ])}
              />
            </Section>

            <Section title="Model usage">
              <Table
                headers={["Capability", "Gateway model", "Calls", "Failed", "Tokens in", "Tokens out", "Cost (USD)", "Latency"]}
                rows={[
                  ...report.model_usage.by_model.map((m) => [
                    m.capability,
                    m.gateway_model_key,
                    m.usage.calls,
                    m.usage.failed_calls,
                    m.usage.tokens_input,
                    m.usage.tokens_output,
                    m.usage.cost_usd.toFixed(6),
                    `${m.usage.latency_ms} ms`,
                  ]),
                  ...(report.model_usage.by_model.length
                    ? [["TOTAL", "", totals.calls, totals.failed_calls, totals.tokens_input, totals.tokens_output, totals.cost_usd.toFixed(6), `${totals.latency_ms} ms`]]
                    : []),
                ]}
              />
            </Section>

            <Section title="Approvals">
              <Table
                headers={["Action", "Prompt", "Status", "Note", "Requested", "Decided"]}
                rows={report.approvals.map((a) => [a.action_type, a.prompt_text, a.status, a.decision_note, fmtTime(a.created_at), fmtTime(a.decided_at)])}
              />
            </Section>

            <Section title="Artifacts">
              <Table
                headers={["Name", "Type", "Size", "SHA-256", ""]}
                rows={report.artifacts.map((a) => [
                  a.name,
                  a.artifact_type,
                  `${a.file_size_bytes} B`,
                  a.content_hash.slice(0, 12),
                  <button key={a.id} onClick={() => saveFile(a.id, a.name)} className="text-indigo-300 hover:underline flex items-center gap-1">
                    <Download className="h-3 w-3" /> Download
                  </button>,
                ])}
              />
            </Section>

            <Section title="Exported versions">
              {versions.length === 0 ? (
                <Empty text={run.is_final ? "Not exported yet. Use “Export version” to save an immutable JSON + HTML copy." : "Export is available once the run has finished."} />
              ) : (
                <Table
                  headers={["Version", "Verdict", "Created", "Content hash", "Downloads"]}
                  rows={versions.map((v) => [
                    <Link key={v.id} href={`/runs/${runId}/report?version=${v.version}`} className="text-indigo-300 hover:underline">
                      v{v.version}
                    </Link>,
                    v.verdict,
                    fmtTime(v.created_at),
                    v.content_hash.slice(0, 12),
                    <span key={`${v.id}-dl`} className="flex gap-3">
                      {v.json_artifact && (
                        <button onClick={() => saveFile(v.json_artifact!.id, `report-v${v.version}.json`)} className="text-indigo-300 hover:underline flex items-center gap-1">
                          <FileJson className="h-3 w-3" /> JSON
                        </button>
                      )}
                      {v.html_artifact && (
                        <button onClick={() => saveFile(v.html_artifact!.id, `report-v${v.version}.html`)} className="text-indigo-300 hover:underline flex items-center gap-1">
                          <FileText className="h-3 w-3" /> HTML
                        </button>
                      )}
                    </span>,
                  ])}
                />
              )}
            </Section>

            <p className="text-[11px] text-slate-600">
              Report schema {report.schema_version} · generated {fmtTime(report.generated_at)}
            </p>
          </>
        )}
      </div>
    </ConsoleShell>
  );
}

export default function RunReportPage() {
  return (
    <Suspense fallback={null}>
      <RunReportView />
    </Suspense>
  );
}
