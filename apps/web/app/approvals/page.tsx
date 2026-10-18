"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  Filter,
  Inbox,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

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

export default function ApprovalsPage() {
  const { token, selectedProject, apiFetch } = useAuth();

  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<"all" | "pending" | "approved" | "rejected">("pending");
  const [searchQuery, setSearchQuery] = useState("");
  
  // Decision modal / inline state
  const [activeDecision, setActiveDecision] = useState<{
    id: string;
    action: "approve" | "reject";
    run_id: string;
    prompt_text: string;
  } | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const fetchApprovals = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/v1/approvals");
      if (!res.ok) {
        throw new Error(`Failed to load approvals (${res.status})`);
      }
      const data = await res.json();
      const items = Array.isArray(data) ? data : data?.items || [];
      setApprovals(items);
    } catch (err: any) {
      setError(err.message || "Failed to load approval requests");
    } finally {
      setLoading(false);
    }
  }, [token, apiFetch]);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals, selectedProject]);

  const handleDecision = async () => {
    if (!activeDecision) return;
    setSubmitting(true);
    setActionFeedback(null);
    try {
      const endpoint = `/v1/approvals/${activeDecision.id}/${activeDecision.action}`;
      const res = await apiFetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision_note: decisionNote.trim() || undefined }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || errorData.message || `Decision failed (${res.status})`);
      }

      setActionFeedback({
        type: "success",
        message: `Approval request was successfully ${activeDecision.action === "approve" ? "approved" : "rejected"}.`,
      });
      setActiveDecision(null);
      setDecisionNote("");
      fetchApprovals();
    } catch (err: any) {
      setActionFeedback({
        type: "error",
        message: err.message || "Failed to submit decision",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const filteredApprovals = approvals.filter((app) => {
    if (filterStatus !== "all" && app.status !== filterStatus) return false;
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      const matchType = app.action_type.toLowerCase().includes(query);
      const matchPrompt = app.prompt_text.toLowerCase().includes(query);
      const matchRun = app.run_id.toLowerCase().includes(query);
      if (!matchType && !matchPrompt && !matchRun) return false;
    }
    return true;
  });

  const pendingCount = approvals.filter((a) => a.status === "pending").length;
  const approvedCount = approvals.filter((a) => a.status === "approved").length;
  const rejectedCount = approvals.filter((a) => a.status === "rejected").length;

  return (
    <ConsoleShell>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="p-2 bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 rounded-lg">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                  Approval Inbox
                  <span className="text-xs px-2 py-0.5 rounded font-mono bg-indigo-950 text-indigo-300 border border-indigo-800/60">
                    Slice 4
                  </span>
                </h1>
                <p className="text-xs text-slate-400 mt-0.5">
                  Authorize sensitive tool invocations, destructive operations, and human-in-the-loop gates.
                </p>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchApprovals()}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs text-slate-300 rounded-lg transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Action feedback toast */}
        {actionFeedback && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-center justify-between ${
              actionFeedback.type === "success"
                ? "bg-emerald-950/40 border-emerald-800/80 text-emerald-300"
                : "bg-rose-950/40 border-rose-800/80 text-rose-300"
            }`}
          >
            <div className="flex items-center gap-2">
              {actionFeedback.type === "success" ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
              ) : (
                <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
              )}
              <span>{actionFeedback.message}</span>
            </div>
            <button
              onClick={() => setActionFeedback(null)}
              className="text-slate-400 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Filter Bar & Counters */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4">
          {/* Status Tabs */}
          <div className="flex items-center bg-slate-900 p-1 rounded-lg border border-slate-800 text-xs">
            <button
              onClick={() => setFilterStatus("pending")}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors flex items-center gap-1.5 ${
                filterStatus === "pending"
                  ? "bg-amber-600/30 text-amber-300 border border-amber-500/40 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Clock className="h-3.5 w-3.5" />
              <span>Pending</span>
              {pendingCount > 0 && (
                <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40">
                  {pendingCount}
                </span>
              )}
            </button>

            <button
              onClick={() => setFilterStatus("all")}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                filterStatus === "all"
                  ? "bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              All ({approvals.length})
            </button>

            <button
              onClick={() => setFilterStatus("approved")}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors flex items-center gap-1.5 ${
                filterStatus === "approved"
                  ? "bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Check className="h-3.5 w-3.5" />
              <span>Approved ({approvedCount})</span>
            </button>

            <button
              onClick={() => setFilterStatus("rejected")}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors flex items-center gap-1.5 ${
                filterStatus === "rejected"
                  ? "bg-rose-600/30 text-rose-300 border border-rose-500/40 shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <X className="h-3.5 w-3.5" />
              <span>Rejected ({rejectedCount})</span>
            </button>
          </div>

          {/* Search Box */}
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Search action or prompt..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full sm:w-64 bg-slate-900 border border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        {/* Approvals List */}
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-28 bg-slate-900/60 rounded-xl border border-slate-800 animate-pulse"
              />
            ))}
          </div>
        ) : error ? (
          <div className="p-6 bg-rose-950/20 border border-rose-800/60 rounded-xl text-center space-y-2">
            <AlertTriangle className="h-6 w-6 text-rose-400 mx-auto" />
            <p className="text-sm font-medium text-rose-300">{error}</p>
            <button
              onClick={() => fetchApprovals()}
              className="text-xs text-rose-400 hover:underline"
            >
              Try again
            </button>
          </div>
        ) : filteredApprovals.length === 0 ? (
          <div className="p-12 bg-slate-900/40 border border-slate-800 rounded-xl text-center space-y-3">
            <div className="h-10 w-10 bg-slate-800/80 rounded-full flex items-center justify-center mx-auto text-slate-400">
              <Inbox className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-medium text-slate-200">
                {filterStatus === "pending"
                  ? "No pending approvals"
                  : "No approval requests found"}
              </p>
              <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                {filterStatus === "pending"
                  ? "All runs are proceeding smoothly without any human-in-the-loop pauses."
                  : "No approval history matches the selected filter criteria."}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredApprovals.map((app) => {
              const isPending = app.status === "pending";
              const isApproved = app.status === "approved";
              const isRejected = app.status === "rejected";

              return (
                <div
                  key={app.id}
                  className={`p-4 rounded-xl border transition-all ${
                    isPending
                      ? "bg-slate-900/80 border-amber-800/50 hover:border-amber-700/80 shadow-sm"
                      : "bg-slate-900/40 border-slate-800 hover:border-slate-700/80"
                  }`}
                >
                  <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                    <div className="space-y-2 flex-1 min-w-0">
                      {/* Top Badges */}
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        {/* Status Badge */}
                        <span
                          className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full font-mono text-[11px] font-medium uppercase tracking-wider ${
                            isPending
                              ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                              : isApproved
                              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                              : "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                          }`}
                        >
                          {isPending && <Clock className="h-3 w-3" />}
                          {isApproved && <CheckCircle2 className="h-3 w-3" />}
                          {isRejected && <XCircle className="h-3 w-3" />}
                          {app.status}
                        </span>

                        {/* Action Type */}
                        <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700/80 text-slate-300 font-mono text-[11px]">
                          action: {app.action_type}
                        </span>

                        {/* Associated Run Link */}
                        <Link
                          href={`/runs/${app.run_id}`}
                          className="inline-flex items-center gap-1 text-slate-400 hover:text-indigo-400 font-mono text-[11px] hover:underline"
                        >
                          <span>run: {app.run_id.slice(0, 8)}...</span>
                          <ArrowRight className="h-3 w-3" />
                        </Link>
                      </div>

                      {/* Prompt description */}
                      <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800/80">
                        <p className="text-xs font-mono text-slate-200 leading-relaxed">
                          {app.prompt_text}
                        </p>
                      </div>

                      {/* Decision Details if not pending */}
                      {!isPending && (
                        <div className="text-[11px] text-slate-400 space-y-1 pt-1">
                          {app.decision_note && (
                            <p className="italic text-slate-300">
                              Note: &ldquo;{app.decision_note}&rdquo;
                            </p>
                          )}
                          <div className="flex items-center gap-3 text-slate-400 font-mono">
                            {app.decided_at && (
                              <span>Decided: {new Date(app.decided_at).toLocaleString()}</span>
                            )}
                            {app.decided_by && <span>By: {app.decided_by}</span>}
                          </div>
                        </div>
                      )}

                      {/* Created timestamp */}
                      <div className="text-[10px] text-slate-400 font-mono">
                        Requested: {new Date(app.created_at).toLocaleString()}
                      </div>
                    </div>

                    {/* Pending Action Buttons */}
                    {isPending && (
                      <div className="flex items-center gap-2 shrink-0 md:self-center">
                        <button
                          onClick={() => {
                            setActiveDecision({
                              id: app.id,
                              action: "approve",
                              run_id: app.run_id,
                              prompt_text: app.prompt_text,
                            });
                            setDecisionNote("");
                          }}
                          data-testid="approve-action-btn" className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium shadow-sm transition-colors"
                        >
                          <Check className="h-3.5 w-3.5" />
                          <span>Approve</span>
                        </button>
                        <button
                          onClick={() => {
                            setActiveDecision({
                              id: app.id,
                              action: "reject",
                              run_id: app.run_id,
                              prompt_text: app.prompt_text,
                            });
                            setDecisionNote("");
                          }}
                          data-testid="reject-action-btn" className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-rose-900/60 border border-slate-700 hover:border-rose-700/80 text-rose-300 rounded-lg text-xs font-medium transition-colors"
                        >
                          <X className="h-3.5 w-3.5" />
                          <span>Reject</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Decision Modal */}
        {activeDecision && (
          <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-5 space-y-4 shadow-xl animate-in fade-in zoom-in-95 duration-150">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  {activeDecision.action === "approve" ? (
                    <div className="p-1.5 bg-emerald-600/20 text-emerald-400 rounded-lg border border-emerald-500/30">
                      <ShieldCheck className="h-5 w-5" />
                    </div>
                  ) : (
                    <div className="p-1.5 bg-rose-600/20 text-rose-400 rounded-lg border border-rose-500/30">
                      <ShieldAlert className="h-5 w-5" />
                    </div>
                  )}
                  <h3 className="text-sm font-semibold text-white capitalize">
                    {activeDecision.action} Request
                  </h3>
                </div>
                <button
                  onClick={() => setActiveDecision(null)}
                  disabled={submitting}
                  className="text-slate-400 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3">
                <div className="p-3 bg-slate-950 rounded-lg border border-slate-800/80 text-xs font-mono text-slate-300">
                  {activeDecision.prompt_text}
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300">
                    Decision Note (Optional)
                  </label>
                  <textarea
                    rows={3}
                    placeholder="Enter audit rationale, ticket link, or remarks..."
                    value={decisionNote}
                    onChange={(e) => setDecisionNote(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
                <button
                  onClick={() => setActiveDecision(null)}
                  disabled={submitting}
                  className="px-3 py-1.5 text-xs text-slate-400 hover:text-white rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDecision}
                  disabled={submitting}
                  className={`flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white rounded-lg shadow-sm transition-colors ${
                    activeDecision.action === "approve"
                      ? "bg-emerald-600 hover:bg-emerald-500"
                      : "bg-rose-600 hover:bg-rose-500"
                  }`}
                >
                  {submitting && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
                  <span>Confirm {activeDecision.action === "approve" ? "Approval" : "Rejection"}</span>
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ConsoleShell>
  );
}
