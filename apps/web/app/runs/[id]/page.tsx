"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  CheckCircle,
  CheckCircle2,
  Clock,
  Copy,
  Cpu,
  Download,
  FileCode,
  FileJson,
  FileText,
  Layers,
  ListTree,
  Package,
  Radio,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  StopCircle,
  Terminal,
  WifiOff,
  X,
  XCircle,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

interface RunDetail {
  id: string;
  project_id: string;
  agent_version_id: string;
  status: "queued" | "preparing" | "running" | "waiting_for_approval" | "succeeded" | "failed" | "cancelled" | string;
  input_payload: Record<string, any>;
  output_payload?: Record<string, any>;
  error_message?: string;
  error?: Record<string, any>;
  resolved_agent?: { slug: string; version: string; prompt_hash?: string };
  resolved_models?: Record<string, any>;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  idempotency_key?: string;
  attempt: number;
  replayed_from?: string;
}

interface RunStepItem {
  id: string;
  step_number: number;
  step_type: string;
  node: string;
  attempt: number;
  input_data: Record<string, any>;
  output_data?: Record<string, any>;
  status: string;
  latency_ms?: number;
  tokens_input: number;
  tokens_output: number;
  error?: Record<string, any>;
  created_at: string;
  finished_at?: string;
}

interface ApprovalItem {
  id: string;
  run_id: string;
  step_id?: string;
  action_type: string;
  prompt_text: string;
  status: "pending" | "approved" | "rejected";
  decision_note?: string;
  decided_by?: string;
  decided_at?: string;
  created_at: string;
}

interface ArtifactItem {
  id: string;
  project_id: string;
  run_id?: string;
  step_id?: string;
  name: string;
  artifact_type: string;
  minio_key: string;
  content_hash: string;
  file_size_bytes: number;
  metadata?: Record<string, any>;
  download_url?: string;
  created_at: string;
}

type StreamConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected" | "closed";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export default function RunDetailPage() {
  const params = useParams();
  const router = useRouter();
  const runId = params.id as string;
  const { token, apiFetch } = useAuth();

  const [run, setRun] = useState<RunDetail | null>(null);
  const [steps, setSteps] = useState<RunStepItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"steps" | "input" | "output" | "error" | "artifacts">("steps");
  const [cancelling, setCancelling] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<StreamConnectionStatus>("disconnected");

  // Approval decision state on the run page
  const [decisionNote, setDecisionNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [decisionFeedback, setDecisionFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const isMountedRef = useRef(true);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const fetchRunDetail = useCallback(async () => {
    if (!runId || !isMountedRef.current) return;
    try {
      const [resRun, resSteps, resApprovals, resArtifacts] = await Promise.all([
        apiFetch(`/v1/runs/${runId}`),
        apiFetch(`/v1/runs/${runId}/steps`),
        apiFetch(`/v1/approvals?run_id=${runId}`),
        apiFetch(`/v1/artifacts?run_id=${runId}`),
      ]);

      if (!isMountedRef.current) return;

      if (!resRun.ok) {
        throw new Error(`Run lookup failed (${resRun.status})`);
      }

      const runData = await resRun.json();
      setRun(runData);

      if (resSteps.ok) {
        const stepsData = await resSteps.json();
        setSteps(stepsData || []);
      }

      if (resApprovals.ok) {
        const approvalsData = await resApprovals.json();
        setApprovals(Array.isArray(approvalsData) ? approvalsData : approvalsData?.items || []);
      }

      if (resArtifacts.ok) {
        const artifactsData = await resArtifacts.json();
        setArtifacts(Array.isArray(artifactsData) ? artifactsData : artifactsData?.items || []);
      }
    } catch (err: any) {
      if (isMountedRef.current) {
        setError(err.message || "Failed to load run details");
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  }, [runId, apiFetch]);

  // Initial data fetch
  useEffect(() => {
    isMountedRef.current = true;
    if (token) {
      fetchRunDetail();
    }
    return () => {
      isMountedRef.current = false;
    };
  }, [fetchRunDetail, token]);

  // Only terminal-ness drives the stream lifecycle. Re-subscribing on every status change
  // (queued -> preparing -> running) dropped events published during each reconnect gap.
  const isRunTerminal = run ? TERMINAL_STATUSES.has(run.status) : false;

  // Server-Sent Events (SSE) streaming with ticket authentication
  useEffect(() => {
    if (!token || !runId) return;

    if (isRunTerminal) {
      setStreamStatus("closed");
      return;
    }

    let isSubscribed = true;

    const handleLiveEvent = (payload: any) => {
      const eventType: string = payload.event || "";
      const data: Record<string, any> = payload.data || {};

      // 1. Run lifecycle events
      if (eventType.startsWith("run.")) {
        const nextStatus = data.to_status || data.status || eventType.replace("run.", "");
        setRun((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            status: nextStatus,
            started_at: data.started_at || prev.started_at,
            finished_at: data.finished_at || prev.finished_at,
            error_message: data.error_message || prev.error_message,
            tokens_input: data.tokens_input !== undefined ? data.tokens_input : prev.tokens_input,
            tokens_output: data.tokens_output !== undefined ? data.tokens_output : prev.tokens_output,
            cost_usd: data.cost_usd !== undefined ? Number(data.cost_usd) : prev.cost_usd,
            output_payload: data.summary ? { summary: data.summary } : prev.output_payload,
          };
        });

        if (TERMINAL_STATUSES.has(nextStatus)) {
          setStreamStatus("closed");
          if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
          }
          fetchRunDetail();
        }
      }

      // 2. Step lifecycle events
      if (eventType.startsWith("step.")) {
        const stepNum = data.step_number;
        if (stepNum !== undefined) {
          setSteps((prevSteps) => {
            const existingIdx = prevSteps.findIndex((s) => s.step_number === stepNum);
            const updatedItem: RunStepItem = {
              id: data.step_id || (existingIdx >= 0 ? prevSteps[existingIdx].id : `step-${stepNum}`),
              step_number: stepNum,
              step_type: data.step_type || "planner",
              node: data.node || "generate",
              attempt: data.attempt || 1,
              input_data: data.input_data || {},
              output_data: data.output_data || undefined,
              status: data.status || "completed",
              tokens_input: data.tokens_input || 0,
              tokens_output: data.tokens_output || 0,
              latency_ms: data.latency_ms,
              created_at: payload.timestamp || new Date().toISOString(),
              finished_at: payload.timestamp,
            };

            if (existingIdx >= 0) {
              const copy = [...prevSteps];
              copy[existingIdx] = updatedItem;
              return copy;
            } else {
              const copy = [...prevSteps, updatedItem];
              copy.sort((a, b) => a.step_number - b.step_number);
              return copy;
            }
          });
        }
      }

      // 3. Approval lifecycle events
      if (eventType.startsWith("approval.")) {
        apiFetch(`/v1/approvals?run_id=${runId}`)
          .then((res) => res.json())
          .then((data) => setApprovals(Array.isArray(data) ? data : data?.items || []))
          .catch(() => {});

        if (eventType === "approval.approved") {
          setRun((prev) => (prev ? { ...prev, status: "running" } : prev));
        } else if (eventType === "approval.rejected") {
          setRun((prev) => (prev ? { ...prev, status: "cancelled" } : prev));
        } else if (eventType === "approval.created") {
          setRun((prev) => (prev ? { ...prev, status: "waiting_for_approval" } : prev));
        }
      }

      // 4. Artifact lifecycle events
      if (eventType.startsWith("artifact.")) {
        apiFetch(`/v1/artifacts?run_id=${runId}`)
          .then((res) => res.json())
          .then((data) => setArtifacts(Array.isArray(data) ? data : data?.items || []))
          .catch(() => {});
      }
    };

    const connectSSE = async () => {
      if (!isSubscribed) return;

      try {
        setStreamStatus((prev) => (prev === "disconnected" ? "connecting" : "reconnecting"));

        const ticketRes = await apiFetch(`/v1/runs/${runId}/events/ticket`, {
          method: "POST",
        });

        if (!ticketRes.ok) {
          throw new Error(`Ticket acquisition failed (${ticketRes.status})`);
        }

        const { ticket } = await ticketRes.json();
        if (!ticket || !isSubscribed) return;

        if (eventSourceRef.current) {
          eventSourceRef.current.close();
        }

        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
        const es = new EventSource(`${apiBase}/v1/runs/${runId}/events?ticket=${ticket}`);
        eventSourceRef.current = es;

        es.onopen = () => {
          if (!isSubscribed) {
            es.close();
            return;
          }
          setStreamStatus("connected");
        };

        const onEvent = (e: MessageEvent) => {
          if (!isSubscribed) return;
          try {
            const payload = JSON.parse(e.data);
            handleLiveEvent(payload);
          } catch (err) {
            console.warn("[SSE Parse Error]", err);
          }
        };

        es.onmessage = onEvent;
        const RUN_EVENT_NAMES = [
          "run.queued",
          "run.running",
          "run.waiting_for_approval",
          "run.succeeded",
          "run.failed",
          "run.cancelled",
          "run.interrupted",
          "step.started",
          "step.completed",
          "step.failed",
          "approval.created",
          "approval.approved",
          "approval.rejected",
          "artifact.created",
        ];
        for (const evtName of RUN_EVENT_NAMES) {
          es.addEventListener(evtName, onEvent as EventListener);
        }

        es.onerror = () => {
          if (!isSubscribed) return;
          es.close();
          setStreamStatus("reconnecting");
          fetchRunDetail();

          reconnectTimeoutRef.current = setTimeout(() => {
            if (isSubscribed) {
              connectSSE();
            }
          }, 3000);
        };
      } catch {
        if (!isSubscribed) return;
        setStreamStatus("reconnecting");
        reconnectTimeoutRef.current = setTimeout(() => {
          if (isSubscribed) {
            connectSSE();
          }
        }, 4000);
      }
    };

    connectSSE();

    return () => {
      isSubscribed = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, token, isRunTerminal, apiFetch, fetchRunDetail]);

  const handleCancel = async () => {
    if (!runId || cancelling) return;
    setCancelling(true);
    try {
      const res = await apiFetch(`/v1/runs/${runId}/cancel`, { method: "POST" });
      if (res.ok) {
        const updated = await res.json();
        setRun(updated);
      }
    } catch (err: any) {
      alert(`Cancel failed: ${err.message}`);
    } finally {
      setCancelling(false);
    }
  };

  const handleReplay = async () => {
    if (!runId || replaying) return;
    setReplaying(true);
    try {
      const res = await apiFetch(`/v1/runs/${runId}/replay`, { method: "POST" });
      if (res.ok) {
        const newRun = await res.json();
        router.push(`/runs/${newRun.id}`);
      } else {
        const errData = await res.json().catch(() => ({}));
        alert(`Replay failed: ${errData.detail || errData.message || "Conflict"}`);
      }
    } catch (err: any) {
      alert(`Replay failed: ${err.message}`);
    } finally {
      setReplaying(false);
    }
  };

  const handleApprovalDecision = async (approvalId: string, action: "approve" | "reject") => {
    setDeciding(true);
    setDecisionFeedback(null);
    try {
      const res = await apiFetch(`/v1/approvals/${approvalId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision_note: decisionNote.trim() || undefined }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || err.message || `Decision failed (${res.status})`);
      }

      setDecisionFeedback({
        type: "success",
        message: `Action successfully ${action === "approve" ? "approved" : "rejected"}. Execution updated.`,
      });
      setDecisionNote("");
      fetchRunDetail();
    } catch (err: any) {
      setDecisionFeedback({
        type: "error",
        message: err.message || "Decision submission failed",
      });
    } finally {
      setDeciding(false);
    }
  };

  const handleCopy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "waiting_for_approval":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-950 text-amber-300 border border-amber-600 animate-pulse shadow-sm">
            <Clock className="h-3.5 w-3.5 text-amber-400" />
            Waiting for Approval
          </span>
        );
      case "succeeded":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800">
            <CheckCircle className="h-3.5 w-3.5" />
            Succeeded
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-950 text-rose-400 border border-rose-800">
            <XCircle className="h-3.5 w-3.5" />
            Failed
          </span>
        );
      case "running":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-950 text-blue-400 border border-blue-800 animate-pulse">
            <Activity className="h-3.5 w-3.5" />
            Running
          </span>
        );
      case "preparing":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-950 text-indigo-400 border border-indigo-800 animate-pulse">
            <Layers className="h-3.5 w-3.5" />
            Preparing
          </span>
        );
      case "queued":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-950 text-amber-400 border border-amber-800">
            <Clock className="h-3.5 w-3.5" />
            Queued
          </span>
        );
      case "cancelled":
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700">
            <StopCircle className="h-3.5 w-3.5" />
            Cancelled
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-400">
            {status}
          </span>
        );
    }
  };

  const getStreamBadge = () => {
    switch (streamStatus) {
      case "connected":
        return (
          <span
            id="sse-live-badge"
            className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-emerald-950/80 text-emerald-300 border border-emerald-700/60"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            Live (SSE)
          </span>
        );
      case "connecting":
        return (
          <span
            id="sse-live-badge"
            className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-indigo-950/80 text-indigo-300 border border-indigo-700/60"
          >
            <Radio className="h-3 w-3 animate-spin" />
            Connecting...
          </span>
        );
      case "reconnecting":
        return (
          <span
            id="sse-live-badge"
            className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-amber-950/80 text-amber-300 border border-amber-700/60"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
            </span>
            Reconnecting...
          </span>
        );
      case "closed":
        return (
          <span
            id="sse-live-badge"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-900 text-slate-400 border border-slate-800"
          >
            <CheckCircle className="h-3 w-3 text-slate-500" />
            Completed
          </span>
        );
      case "disconnected":
      default:
        return (
          <span
            id="sse-live-badge"
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-900 text-slate-500 border border-slate-800"
          >
            <WifiOff className="h-3 w-3 text-slate-600" />
            Offline
          </span>
        );
    }
  };

  const pendingApproval = approvals.find((a) => a.status === "pending");

  return (
    <ConsoleShell>
      <div className="space-y-6 max-w-7xl mx-auto pb-12">
        {/* Navigation & Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
          <div className="space-y-1">
            <Link
              href="/runs"
              className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors mb-1"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to Run History
            </Link>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-bold tracking-tight text-slate-100 font-mono">
                {run?.id || runId}
              </h1>
              {run && getStatusBadge(run.status)}
              {getStreamBadge()}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Link
              href={`/runs/${runId}/report`}
              className="p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors text-xs flex items-center gap-1.5"
              title="Run report"
              data-testid="run-report-link"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Report</span>
            </Link>
            <button
              onClick={() => fetchRunDetail()}
              className="p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors text-xs flex items-center gap-1.5"
              title="Manual Refresh"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Refresh</span>
            </button>

            {run && (run.status === "running" || run.status === "queued" || run.status === "preparing") && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="px-3 py-2 rounded-lg bg-rose-950 border border-rose-800 text-rose-300 hover:bg-rose-900 transition-colors text-xs flex items-center gap-1.5 disabled:opacity-50 font-medium"
              >
                <StopCircle className="h-3.5 w-3.5" />
                {cancelling ? "Cancelling..." : "Cancel Run"}
              </button>
            )}

            {run && TERMINAL_STATUSES.has(run.status) && (
              <button
                onClick={handleReplay}
                disabled={replaying}
                className="px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors text-xs flex items-center gap-1.5 disabled:opacity-50 font-medium shadow-sm"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                {replaying ? "Replaying..." : "Replay Run"}
              </button>
            )}
          </div>
        </div>

        {/* Approval Decision Banner if paused waiting for approval */}
        {(run?.status === "waiting_for_approval" || pendingApproval) && pendingApproval && (
          <div className="p-5 bg-gradient-to-r from-amber-950/60 via-slate-900/90 to-amber-950/60 border border-amber-600/70 rounded-2xl shadow-lg space-y-3 animate-in fade-in">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <div className="p-2 bg-amber-500/20 text-amber-400 rounded-xl border border-amber-500/40">
                  <ShieldAlert className="h-5 w-5 animate-pulse" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-amber-200 flex items-center gap-2">
                    Execution Paused: Human Approval Required
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-900/60 text-amber-300 border border-amber-700/60">
                      action: {pendingApproval.action_type}
                    </span>
                  </h3>
                  <p className="text-xs text-amber-300/80 mt-0.5">
                    This step requires human verification before proceeding.
                  </p>
                </div>
              </div>
            </div>

            <div className="p-3 bg-slate-950/80 rounded-xl border border-amber-900/60 font-mono text-xs text-slate-200">
              {pendingApproval.prompt_text}
            </div>

            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 pt-1">
              <input
                type="text"
                placeholder="Optional decision note / rationale..."
                value={decisionNote}
                onChange={(e) => setDecisionNote(e.target.value)}
                disabled={deciding}
                className="flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500"
              />

              <div className="flex items-center gap-2 shrink-0">
                <button
                  data-testid="inline-approve-btn" onClick={() => handleApprovalDecision(pendingApproval.id, "approve")}
                  disabled={deciding}
                  className="flex-1 sm:flex-initial flex items-center justify-center gap-1.5 px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium shadow-sm transition-colors"
                >
                  <Check className="h-3.5 w-3.5" />
                  <span>Approve & Resume</span>
                </button>

                <button
                  data-testid="inline-reject-btn" onClick={() => handleApprovalDecision(pendingApproval.id, "reject")}
                  disabled={deciding}
                  className="flex-1 sm:flex-initial flex items-center justify-center gap-1.5 px-4 py-1.5 bg-rose-900/80 hover:bg-rose-800 border border-rose-700/80 text-rose-200 rounded-lg text-xs font-medium transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                  <span>Reject</span>
                </button>
              </div>
            </div>

            {decisionFeedback && (
              <p
                className={`text-xs ${
                  decisionFeedback.type === "success" ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {decisionFeedback.message}
              </p>
            )}
          </div>
        )}

        {/* Top Metric Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl space-y-1">
            <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Bot className="h-3.5 w-3.5 text-indigo-400" />
              Resolved Agent
            </span>
            <p className="text-sm font-semibold text-slate-200 font-mono truncate">
              {run?.resolved_agent?.slug || "—"}
              {run?.resolved_agent?.version && (
                <span className="text-xs text-slate-500 ml-1 font-normal">
                  v{run.resolved_agent.version}
                </span>
              )}
            </p>
          </div>

          <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl space-y-1">
            <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-amber-400" />
              Execution Duration
            </span>
            <p className="text-sm font-semibold text-slate-200 font-mono">
              {run?.started_at && run?.finished_at
                ? `${Math.max(
                    0,
                    Math.round(
                      (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
                    )
                  )}s`
                : run?.started_at
                ? "Active..."
                : "Queued"}
            </p>
          </div>

          <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl space-y-1">
            <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Cpu className="h-3.5 w-3.5 text-emerald-400" />
              Tokens (In / Out)
            </span>
            <p className="text-sm font-semibold text-slate-200 font-mono">
              {run ? `${run.tokens_input.toLocaleString()} / ${run.tokens_output.toLocaleString()}` : "—"}
            </p>
          </div>

          <div className="p-4 bg-slate-900/60 border border-slate-800/80 rounded-xl space-y-1">
            <span className="text-[11px] font-medium text-slate-400 flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5 text-violet-400" />
              Total Cost
            </span>
            <p className="text-sm font-semibold text-slate-200 font-mono">
              {run ? `$${Number(run.cost_usd).toFixed(5)}` : "—"}
            </p>
          </div>
        </div>

        {/* Tabbed Content Container */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl overflow-hidden">
          <div className="flex border-b border-slate-800 px-4 text-xs font-medium">
            <button
              onClick={() => setActiveTab("steps")}
              className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "steps"
                  ? "border-indigo-500 text-indigo-300 font-semibold"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <ListTree className="h-3.5 w-3.5" />
              Execution Steps ({steps.length})
            </button>
            <button
              onClick={() => setActiveTab("artifacts")}
              className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "artifacts"
                  ? "border-indigo-500 text-indigo-300 font-semibold"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <Package className="h-3.5 w-3.5" />
              Artifacts ({artifacts.length})
            </button>
            <button
              onClick={() => setActiveTab("input")}
              className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "input"
                  ? "border-indigo-500 text-indigo-300 font-semibold"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <Terminal className="h-3.5 w-3.5" />
              Input Payload
            </button>
            <button
              onClick={() => setActiveTab("output")}
              className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "output"
                  ? "border-indigo-500 text-indigo-300 font-semibold"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <CheckCircle className="h-3.5 w-3.5" />
              Output Results
            </button>
            {run?.error_message && (
              <button
                onClick={() => setActiveTab("error")}
                className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                  activeTab === "error"
                    ? "border-rose-500 text-rose-300 font-semibold"
                    : "border-transparent text-rose-400 hover:text-rose-200"
                }`}
              >
                <AlertCircle className="h-3.5 w-3.5" />
                Error Details
              </button>
            )}
          </div>

          <div className="p-5">
            {loading ? (
              <div className="py-12 text-center text-xs text-slate-500 animate-pulse">
                Loading run details...
              </div>
            ) : error ? (
              <div className="p-6 text-center text-xs text-rose-400">{error}</div>
            ) : (
              <>
                {/* Steps Tab */}
                {activeTab === "steps" && (
                  <div className="space-y-4">
                    {steps.length === 0 ? (
                      <div className="py-12 text-center text-xs text-slate-500 space-y-2">
                        <p>
                          {run?.status === "queued" || run?.status === "preparing"
                            ? "Run is active. Realtime steps will stream live as nodes execute..."
                            : "No recorded steps for this execution."}
                        </p>
                        {streamStatus === "connected" && (
                          <p className="text-[11px] text-emerald-400/80 flex items-center justify-center gap-1">
                            <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping"></span>
                            Listening for live step events over Redis Pub/Sub...
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {steps.map((step) => (
                          <div
                            key={step.id}
                            className="p-4 bg-slate-950 border border-slate-800 rounded-xl space-y-2 font-mono text-xs"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-bold">
                                  #{step.step_number}
                                </span>
                                <span className="font-semibold text-slate-100">{step.node}</span>
                                <span className="text-[10px] text-slate-500">({step.step_type})</span>
                              </div>
                              <div className="flex items-center gap-3">
                                {step.latency_ms !== undefined && (
                                  <span className="text-slate-400 text-[11px]">{step.latency_ms}ms</span>
                                )}
                                <span
                                  className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                                    step.status === "succeeded" || step.status === "completed"
                                      ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                                      : "bg-slate-800 text-slate-400"
                                  }`}
                                >
                                  {step.status}
                                </span>
                              </div>
                            </div>

                            {step.output_data && (
                              <details className="mt-2 text-[11px]">
                                <summary className="cursor-pointer text-indigo-400 hover:underline">
                                  Inspect step output data
                                </summary>
                                <pre className="p-3 bg-slate-900 border border-slate-800 rounded-lg mt-1 text-slate-300 overflow-x-auto">
                                  {JSON.stringify(step.output_data, null, 2)}
                                </pre>
                              </details>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Artifacts Tab */}
                {activeTab === "artifacts" && (
                  <div className="space-y-4">
                    {artifacts.length === 0 ? (
                      <div className="py-12 text-center text-xs text-slate-500 space-y-2">
                        <div className="h-8 w-8 bg-slate-800/80 rounded-full flex items-center justify-center mx-auto text-slate-400">
                          <Package className="h-4 w-4" />
                        </div>
                        <p className="font-medium text-slate-300">No artifacts produced yet</p>
                        <p className="text-[11px] text-slate-500">
                          Artifacts generated by agent tools, test synthesizers, and runners will appear here automatically upon completion.
                        </p>
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {artifacts.map((art) => (
                          <div
                            key={art.id}
                            className="p-4 bg-slate-950 border border-slate-800 rounded-xl space-y-3"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="flex items-center gap-2.5 overflow-hidden">
                                <div className="p-2 bg-slate-900 rounded-lg border border-slate-800 shrink-0">
                                  {art.name.endsWith(".json") ? (
                                    <FileJson className="h-5 w-5 text-indigo-400" />
                                  ) : (
                                    <FileText className="h-5 w-5 text-emerald-400" />
                                  )}
                                </div>
                                <div className="overflow-hidden">
                                  <h4 className="text-xs font-semibold text-slate-200 truncate" title={art.name}>
                                    {art.name}
                                  </h4>
                                  <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 border border-slate-800">
                                    {art.artifact_type}
                                  </span>
                                </div>
                              </div>

                              <a
                                href={`/v1/artifacts/${art.id}/download`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors shrink-0"
                              >
                                <Download className="h-3.5 w-3.5" />
                                <span>Download</span>
                              </a>
                            </div>

                            <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono pt-2 border-t border-slate-900">
                              <span>{formatBytes(art.file_size_bytes)}</span>
                              <span>sha256: {art.content_hash.slice(0, 10)}...</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Input Tab */}
                {activeTab === "input" && (
                  <div className="space-y-2">
                    <div className="flex justify-end">
                      <button
                        onClick={() => handleCopy(JSON.stringify(run?.input_payload, null, 2), "input")}
                        className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center gap-1 font-mono"
                      >
                        <Copy className="h-3 w-3" />
                        {copied === "input" ? "Copied" : "Copy JSON"}
                      </button>
                    </div>
                    <pre className="p-4 bg-slate-950 border border-slate-800 rounded-xl font-mono text-xs text-indigo-300 overflow-x-auto leading-relaxed">
                      {JSON.stringify(run?.input_payload, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Output Tab */}
                {activeTab === "output" && (
                  <div className="space-y-2">
                    {run?.output_payload ? (
                      <>
                        <div className="flex justify-end">
                          <button
                            onClick={() =>
                              handleCopy(JSON.stringify(run?.output_payload, null, 2), "output")
                            }
                            className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center gap-1 font-mono"
                          >
                            <Copy className="h-3 w-3" />
                            {copied === "output" ? "Copied" : "Copy JSON"}
                          </button>
                        </div>
                        <pre className="p-4 bg-slate-950 border border-slate-800 rounded-xl font-mono text-xs text-emerald-300 overflow-x-auto leading-relaxed">
                          {JSON.stringify(run.output_payload, null, 2)}
                        </pre>
                      </>
                    ) : (
                      <div className="py-12 text-center text-xs text-slate-500">
                        {run?.status === "succeeded"
                          ? "Run completed without output payload."
                          : "Run has not completed execution yet. Output payload will be available upon completion."}
                      </div>
                    )}
                  </div>
                )}

                {/* Error Tab */}
                {activeTab === "error" && run?.error_message && (
                  <div className="p-4 bg-rose-950/40 border border-rose-800/80 rounded-xl space-y-2">
                    <div className="flex items-center gap-2 text-rose-300 font-semibold text-xs">
                      <AlertCircle className="h-4 w-4" />
                      <span>{run.error_message}</span>
                    </div>
                    {run.error && (
                      <pre className="p-3 bg-slate-950 border border-rose-900 rounded-lg text-rose-300 text-[11px] font-mono overflow-x-auto">
                        {JSON.stringify(run.error, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </ConsoleShell>
  );
}
