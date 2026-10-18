"use client";

import React, { useCallback, useEffect, useState, useMemo } from "react";
import Link from "next/link";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle,
  Clock,
  Copy,
  ExternalLink,
  Filter,
  Play,
  RefreshCw,
  Search,
  XCircle,
  Zap,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

interface RunItem {
  id: string;
  project_id: string;
  agent_version_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;
  input_payload: Record<string, any>;
  output_payload?: Record<string, any>;
  error_message?: string;
  resolved_agent?: { slug: string; version: string; prompt_hash?: string };
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

export default function RunsHistoryPage() {
  const { token, isAuthenticated, apiFetch, selectedProject } = useAuth();
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url =
        statusFilter === "all"
          ? "/v1/runs?limit=50"
          : `/v1/runs?status=${statusFilter}&limit=50`;

      const res = await apiFetch(url);
      if (!res.ok) {
        if (res.status === 401) {
          throw new Error("Authentication required to view runs.");
        }
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.error?.message || err?.detail || `API error (${res.status})`);
      }
      const data = await res.json();
      setRuns(data.items || []);
      setTotal(data.total || 0);
    } catch (err: any) {
      setError(err.message || "Failed to load runs history");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, statusFilter]);

  useEffect(() => {
    if (token) {
      fetchRuns();
    }
  }, [fetchRuns, token, selectedProject]);

  const handleCopyId = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const filteredRuns = useMemo(() => {
    return runs.filter((r) => {
      const agentSlug = r.resolved_agent?.slug || "";
      const matchesSearch =
        r.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        agentSlug.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (r.error_message && r.error_message.toLowerCase().includes(searchQuery.toLowerCase()));
      return matchesSearch;
    });
  }, [runs, searchQuery]);

  const stats = useMemo(() => {
    const succeeded = runs.filter((r) => r.status === "succeeded").length;
    const failed = runs.filter((r) => r.status === "failed").length;
    const inProgress = runs.filter((r) => r.status === "running" || r.status === "queued").length;
    const totalTokens = runs.reduce((acc, r) => acc + (r.tokens_input + r.tokens_output), 0);
    return { succeeded, failed, inProgress, totalTokens };
  }, [runs]);

  const formatDuration = (start?: string, end?: string) => {
    if (!start) return "-";
    const startTime = new Date(start).getTime();
    const endTime = end ? new Date(end).getTime() : Date.now();
    const diff = Math.max(0, endTime - startTime);
    if (diff < 1000) return `${diff}ms`;
    if (diff < 60000) return `${(diff / 1000).toFixed(1)}s`;
    return `${Math.floor(diff / 60000)}m ${Math.floor((diff % 60000) / 1000)}s`;
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "succeeded":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800/80">
            <CheckCircle className="h-3 w-3" /> Succeeded
          </span>
        );
      case "running":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-cyan-950 text-cyan-300 border border-cyan-800/80">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-pulse" /> Running
          </span>
        );
      case "queued":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80">
            <Clock className="h-3 w-3" /> Queued
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-rose-950 text-rose-300 border border-rose-800/80">
            <XCircle className="h-3 w-3" /> Failed
          </span>
        );
      case "cancelled":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-900 text-slate-400 border border-slate-700">
            Cancelled
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-slate-800 text-slate-300">
            {status}
          </span>
        );
    }
  };

  return (
    <ConsoleShell>
      <div className="space-y-6">
        {/* Page Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="p-1.5 bg-indigo-600/30 border border-indigo-500/30 rounded-lg text-indigo-400">
                <Activity className="h-5 w-5" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-100">Runs & Workflows</h1>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Deterministic agent execution history, token consumption, and step inspection.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={fetchRuns}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-medium text-slate-300 rounded-lg transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <Link
              href="/runs/new"
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-colors"
            >
              <Play className="h-3 w-3 fill-current" />
              Launch Run
            </Link>
          </div>
        </div>

        {/* Metrics Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Total Runs</p>
            <p className="text-2xl font-bold text-slate-100 mt-1">{total}</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">In Progress</p>
            <p className="text-2xl font-bold text-cyan-400 mt-1">{stats.inProgress}</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Succeeded</p>
            <p className="text-2xl font-bold text-emerald-400 mt-1">{stats.succeeded}</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Total Tokens</p>
            <p className="text-2xl font-bold text-indigo-300 font-mono mt-1">
              {stats.totalTokens.toLocaleString()}
            </p>
          </div>
        </div>

        {/* Search & Status Filters */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-slate-900/50 p-3 rounded-xl border border-slate-800">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input
              type="text"
              placeholder="Search by Run ID or agent slug..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="flex items-center gap-2 overflow-x-auto pb-1 sm:pb-0">
            <div className="flex bg-slate-950 p-0.5 rounded-lg border border-slate-800 text-xs">
              {["all", "queued", "running", "succeeded", "failed"].map((status) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  className={`px-3 py-1 rounded-md text-[11px] font-medium capitalize transition-colors ${
                    statusFilter === status
                      ? "bg-indigo-600 text-white"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {status}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Run History List Content */}
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="p-4 bg-slate-900/40 border border-slate-800 rounded-xl animate-pulse flex items-center justify-between"
              >
                <div className="space-y-2 w-1/3">
                  <div className="h-4 bg-slate-800 rounded w-full" />
                  <div className="h-3 bg-slate-800/60 rounded w-1/2" />
                </div>
                <div className="h-6 bg-slate-800 rounded w-20" />
              </div>
            ))}
          </div>
        ) : error && runs.length === 0 ? (
          <div className="p-8 bg-slate-900/30 border border-slate-800 rounded-xl text-center space-y-3">
            <AlertCircle className="h-8 w-8 text-amber-400 mx-auto" />
            <h3 className="text-sm font-semibold text-slate-200">Unable to load runs</h3>
            <p className="text-xs text-slate-400 max-w-md mx-auto">{error}</p>
            <button
              onClick={fetchRuns}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium rounded-lg transition-colors"
            >
              Retry
            </button>
          </div>
        ) : filteredRuns.length === 0 ? (
          <div className="p-12 bg-slate-900/20 border border-slate-800/60 rounded-xl text-center space-y-4">
            <Activity className="h-10 w-10 text-slate-600 mx-auto" />
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-slate-200">No runs recorded</h3>
              <p className="text-xs text-slate-400 max-w-md mx-auto">
                No runs match your current status or filter criteria. Launch your first automated test agent run.
              </p>
            </div>
            <Link
              href="/runs/new"
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors"
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              Launch Run
            </Link>
          </div>
        ) : (
          <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm divide-y divide-slate-800/80">
            {filteredRuns.map((run) => {
              const agentSlug = run.resolved_agent?.slug || "agent";
              const agentVer = run.resolved_agent?.version || "1.0.0";
              const reqText =
                typeof run.input_payload?.requirements === "string"
                  ? run.input_payload.requirements
                  : JSON.stringify(run.input_payload);

              return (
                <Link
                  key={run.id}
                  href={`/runs/${run.id}`}
                  className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-800/40 transition-colors group block"
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="p-2 bg-slate-950 rounded-lg border border-slate-800 text-slate-400 group-hover:text-indigo-400 transition-colors shrink-0">
                      <Bot className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-slate-200 text-xs hover:underline">
                          {agentSlug}
                        </span>
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800/80">
                          v{agentVer}
                        </span>
                        <button
                          onClick={(e) => handleCopyId(run.id, e)}
                          title="Copy Run ID"
                          className="font-mono text-[11px] text-slate-500 hover:text-slate-300 flex items-center gap-1"
                        >
                          <span>{run.id.substring(0, 8)}...</span>
                          <Copy className="h-3 w-3" />
                          {copiedId === run.id && <span className="text-emerald-400 text-[10px]">Copied</span>}
                        </button>
                      </div>

                      <p className="text-xs text-slate-400 truncate max-w-xl">
                        {reqText}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-4 shrink-0 self-end md:self-center">
                    <div className="text-right text-[11px] font-mono space-y-0.5">
                      <div className="text-slate-300">
                        {formatDuration(run.started_at, run.finished_at)}
                      </div>
                      <div className="text-slate-500 text-[10px]">
                        {run.tokens_input + run.tokens_output} tok
                      </div>
                    </div>

                    <div className="w-24 flex justify-end">
                      {getStatusBadge(run.status)}
                    </div>

                    <ArrowRight className="h-4 w-4 text-slate-600 group-hover:text-slate-300 transition-colors" />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </ConsoleShell>
  );
}
