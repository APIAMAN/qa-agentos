"use client";

import React, { useCallback, useEffect, useState, useMemo } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle,
  Clock,
  Code2,
  Copy,
  Cpu,
  Eye,
  FileCode2,
  Layers,
  Lock,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Sliders,
  Terminal,
  X,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

interface AgentListItem {
  id: string;
  slug: string;
  name: string;
  description?: string;
  agent_type: string;
  is_active: boolean;
  version?: string;
  status?: string;
  input_schema?: Record<string, any>;
  output_schema?: Record<string, any>;
  parameters?: Record<string, any>;
  permissions: string[];
  allowed_tools: string[];
  model_requirements?: Record<string, any>;
  available_versions: string[];
}

interface AgentDetailResponse extends AgentListItem {
  active_version?: any;
  versions: any[];
}

export default function AgentCatalogPage() {
  const { token, isAuthenticated, apiFetch, login } = useAuth();
  const [agents, setAgents] = useState<AgentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(null);
  const [agentDetail, setAgentDetail] = useState<AgentDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"input" | "output" | "models" | "versions">("input");
  const [copied, setCopied] = useState(false);
  const [demoLoggingIn, setDemoLoggingIn] = useState(false);

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/v1/agents?include_inactive=true");
      if (!res.ok) {
        if (res.status === 401) {
          throw new Error("Authentication required to view catalog.");
        }
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.error?.message || err?.detail || `API error (${res.status})`);
      }
      const data = await res.json();
      setAgents(data);
    } catch (err: any) {
      setError(err.message || "Failed to load agent catalog");
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    fetchAgents();
  }, [token, fetchAgents]);

  const handleQuickLogin = async () => {
    setDemoLoggingIn(true);
    try {
      await login("admin@qa-agentos.local", "admin123456");
      await fetchAgents();
    } catch (err: any) {
      setError(`Auto-login failed: ${err.message}`);
    } finally {
      setDemoLoggingIn(false);
    }
  };

  const handleOpenDetail = async (slug: string) => {
    setSelectedAgentSlug(slug);
    setDetailLoading(true);
    try {
      const res = await apiFetch(`/v1/agents/${slug}?include_inactive=true`);
      if (res.ok) {
        const data = await res.json();
        setAgentDetail(data);
      }
    } catch (err) {
      console.error("Failed to load agent detail", err);
    } finally {
      setDetailLoading(false);
    }
  };

  const handleCopySchema = (schema: any) => {
    navigator.clipboard.writeText(JSON.stringify(schema, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Filtered agents
  const filteredAgents = useMemo(() => {
    return agents.filter((agent) => {
      const matchesSearch =
        agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        agent.slug.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (agent.description && agent.description.toLowerCase().includes(searchQuery.toLowerCase()));

      const matchesStatus =
        statusFilter === "all"
          ? true
          : statusFilter === "active"
          ? agent.is_active
          : !agent.is_active;

      return matchesSearch && matchesStatus;
    });
  }, [agents, searchQuery, statusFilter]);

  const totalActive = agents.filter((a) => a.is_active).length;

  return (
    <ConsoleShell>
      <div className="space-y-6">
        {/* Page Title & Stats Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="p-1.5 bg-indigo-600/30 border border-indigo-500/30 rounded-lg text-indigo-400">
                <Bot className="h-5 w-5" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-100">Agent Catalog</h1>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Registered QA agents, specifications, JSON schemas, parameters, and version histories.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={fetchAgents}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-medium text-slate-300 rounded-lg transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Stats Metrics Bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Total Registered</p>
            <p className="text-2xl font-bold text-slate-100 mt-1">{agents.length}</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Active in Runtime</p>
            <p className="text-2xl font-bold text-emerald-400 mt-1">{totalActive}</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Execution Kind</p>
            <p className="text-sm font-semibold text-indigo-300 font-mono mt-2">ReAct Autonomous</p>
          </div>
          <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-xl">
            <p className="text-[11px] font-medium text-slate-400">Schema Standard</p>
            <p className="text-sm font-semibold text-cyan-300 font-mono mt-2">Draft 2020-12</p>
          </div>
        </div>

        {/* Unauthenticated Notification */}
        {!isAuthenticated && (
          <div className="p-4 bg-amber-950/30 border border-amber-800/60 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-amber-200">
            <div className="flex items-center gap-2">
              <Lock className="h-4 w-4 text-amber-400 shrink-0" />
              <span>
                You are currently in guest mode. Sign in to view live authenticated agents and runtime properties.
              </span>
            </div>
            <button
              onClick={handleQuickLogin}
              disabled={demoLoggingIn}
              className="px-3 py-1.5 bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold rounded-lg shrink-0 transition-colors disabled:opacity-50"
            >
              {demoLoggingIn ? "Signing in..." : "Demo Admin Sign In"}
            </button>
          </div>
        )}

        {/* Search & Filters */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-slate-900/50 p-3 rounded-xl border border-slate-800">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
            <input
              type="text"
              placeholder="Search by agent name, slug, or capability..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="flex items-center gap-2">
            <div className="flex bg-slate-950 p-0.5 rounded-lg border border-slate-800 text-xs">
              <button
                onClick={() => setStatusFilter("all")}
                className={`px-3 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  statusFilter === "all" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                All
              </button>
              <button
                onClick={() => setStatusFilter("active")}
                className={`px-3 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  statusFilter === "active" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Active
              </button>
              <button
                onClick={() => setStatusFilter("inactive")}
                className={`px-3 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  statusFilter === "inactive" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Inactive
              </button>
            </div>
          </div>
        </div>

        {/* Catalog Grid State Handling */}
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="p-6 bg-slate-900/40 border border-slate-800/60 rounded-xl space-y-4 animate-pulse"
              >
                <div className="h-6 bg-slate-800 rounded w-2/3" />
                <div className="h-16 bg-slate-800/60 rounded" />
                <div className="h-8 bg-slate-800/40 rounded" />
                <div className="h-10 bg-slate-800/50 rounded" />
              </div>
            ))}
          </div>
        ) : error && agents.length === 0 ? (
          <div className="p-8 bg-slate-900/30 border border-slate-800 rounded-xl text-center space-y-3">
            <AlertTriangle className="h-8 w-8 text-amber-400 mx-auto" />
            <h3 className="text-sm font-semibold text-slate-200">Unable to load catalog</h3>
            <p className="text-xs text-slate-400 max-w-md mx-auto">{error}</p>
            <div className="pt-2">
              <button
                onClick={fetchAgents}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium rounded-lg transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        ) : filteredAgents.length === 0 ? (
          <div className="p-12 bg-slate-900/20 border border-slate-800/60 rounded-xl text-center space-y-3">
            <Bot className="h-10 w-10 text-slate-600 mx-auto" />
            <h3 className="text-sm font-semibold text-slate-300">No agents match your criteria</h3>
            <p className="text-xs text-slate-500">
              Try adjusting your search query or reset the active/inactive filter.
            </p>
            <button
              onClick={() => {
                setSearchQuery("");
                setStatusFilter("all");
              }}
              className="text-xs text-indigo-400 hover:underline pt-2 inline-block"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredAgents.map((agent) => {
              const properties = agent.input_schema?.properties || {};
              const requiredKeys = agent.input_schema?.required || [];
              const propKeys = Object.keys(properties);

              return (
                <div
                  key={agent.id}
                  className="bg-slate-900/60 border border-slate-800 hover:border-slate-700/80 rounded-xl p-5 flex flex-col justify-between transition-all hover:shadow-lg hover:shadow-indigo-950/20 group"
                >
                  <div className="space-y-4">
                    {/* Header */}
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-base font-bold text-slate-100 group-hover:text-indigo-300 transition-colors">
                          {agent.name}
                        </h3>
                        <p className="text-xs font-mono text-slate-400 mt-0.5">{agent.slug}</p>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {agent.is_active ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800/80">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-400 border border-slate-700">
                            Inactive
                          </span>
                        )}
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-950/80 text-indigo-300 border border-indigo-800/80">
                          v{agent.version || "1.0.0"}
                        </span>
                      </div>
                    </div>

                    {/* Description */}
                    <p className="text-xs text-slate-300 line-clamp-3 leading-relaxed">
                      {agent.description || "No description provided."}
                    </p>

                    {/* Supported Input Parameters */}
                    <div className="space-y-2 pt-1 border-t border-slate-800/60">
                      <div className="flex items-center justify-between text-[11px] text-slate-400 font-medium">
                        <span className="flex items-center gap-1">
                          <Sliders className="h-3 w-3 text-indigo-400" />
                          Supported Input Parameters:
                        </span>
                        <span className="font-mono text-[10px] text-slate-400">
                          {propKeys.length} {propKeys.length === 1 ? "field" : "fields"}
                        </span>
                      </div>

                      <div className="flex flex-wrap gap-1.5">
                        {propKeys.length > 0 ? (
                          propKeys.map((key) => {
                            const isReq = requiredKeys.includes(key);
                            const propType = properties[key]?.type || "any";
                            return (
                              <span
                                key={key}
                                title={properties[key]?.description || `${key} (${propType})`}
                                className={`px-2 py-0.5 rounded text-[11px] font-mono border ${
                                  isReq
                                    ? "bg-indigo-950/80 text-indigo-300 border-indigo-800/80 font-semibold"
                                    : "bg-slate-950 text-slate-400 border-slate-800"
                                }`}
                              >
                                {key}
                                {isReq && <span className="text-indigo-400 ml-1">*</span>}
                              </span>
                            );
                          })
                        ) : (
                          <span className="text-[11px] text-slate-400 italic">No input parameters declared</span>
                        )}
                      </div>
                    </div>

                    {/* Runtime Parameters & Badges */}
                    <div className="grid grid-cols-2 gap-2 text-[11px] pt-1">
                      <div className="p-2 bg-slate-950/60 rounded border border-slate-800/60">
                        <span className="text-slate-400 block text-[10px]">Type</span>
                        <span className="font-mono text-slate-300 capitalize">{agent.agent_type}</span>
                      </div>
                      <div className="p-2 bg-slate-950/60 rounded border border-slate-800/60">
                        <span className="text-slate-400 block text-[10px]">Permissions</span>
                        <span className="font-mono text-slate-300">
                          {agent.permissions.length > 0 ? agent.permissions.join(", ") : "None"}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Actions Footer */}
                  <div className="pt-4 mt-4 border-t border-slate-800/80 flex items-center justify-between">
                    <span className="text-[11px] text-slate-400 font-mono">
                      Versions: {agent.available_versions.join(", ") || "1.0.0"}
                    </span>
                    <button
                      onClick={() => handleOpenDetail(agent.slug)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 hover:text-white rounded-lg text-xs font-medium border border-indigo-500/30 transition-colors"
                    >
                      <Eye className="h-3.5 w-3.5" />
                      Specification
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Specification Detail Modal */}
        {selectedAgentSlug && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-150">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden">
              {/* Modal Header */}
              <div className="p-5 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-600/30 border border-indigo-500/30 rounded-lg text-indigo-400">
                    <Bot className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold text-slate-100">
                      {agentDetail?.name || selectedAgentSlug}
                    </h2>
                    <p className="text-xs font-mono text-slate-400">
                      slug: {selectedAgentSlug} &bull; version: {agentDetail?.version || "1.0.0"}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => {
                    setSelectedAgentSlug(null);
                    setAgentDetail(null);
                  }}
                  className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              {/* Modal Tabs */}
              <div className="flex border-b border-slate-800 bg-slate-950/60 px-5 text-xs font-medium">
                <button
                  onClick={() => setActiveTab("input")}
                  className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === "input"
                      ? "border-indigo-500 text-indigo-300 font-semibold"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Sliders className="h-3.5 w-3.5" />
                  Input Schema
                </button>
                <button
                  onClick={() => setActiveTab("output")}
                  className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === "output"
                      ? "border-indigo-500 text-indigo-300 font-semibold"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <FileCode2 className="h-3.5 w-3.5" />
                  Output Schema
                </button>
                <button
                  onClick={() => setActiveTab("models")}
                  className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === "models"
                      ? "border-indigo-500 text-indigo-300 font-semibold"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Cpu className="h-3.5 w-3.5" />
                  Model Requirements
                </button>
                <button
                  onClick={() => setActiveTab("versions")}
                  className={`py-3 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
                    activeTab === "versions"
                      ? "border-indigo-500 text-indigo-300 font-semibold"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Clock className="h-3.5 w-3.5" />
                  Versions ({agentDetail?.versions.length || 1})
                </button>
              </div>

              {/* Modal Body */}
              <div className="p-5 flex-1 overflow-y-auto space-y-4">
                {detailLoading ? (
                  <div className="py-12 text-center text-xs text-slate-400 animate-pulse">
                    Loading agent specification...
                  </div>
                ) : (
                  <>
                    {activeTab === "input" && (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <p className="text-xs text-slate-300 font-medium">
                            JSON Schema validated during run intake:
                          </p>
                          <button
                            onClick={() => handleCopySchema(agentDetail?.input_schema)}
                            className="inline-flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded text-[11px] text-slate-300 transition-colors"
                          >
                            <Copy className="h-3 w-3" />
                            {copied ? "Copied!" : "Copy JSON"}
                          </button>
                        </div>
                        <pre className="p-4 bg-slate-950 border border-slate-800 rounded-xl font-mono text-xs text-indigo-200 overflow-x-auto leading-relaxed">
                          {JSON.stringify(agentDetail?.input_schema, null, 2)}
                        </pre>
                      </div>
                    )}

                    {activeTab === "output" && (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <p className="text-xs text-slate-300 font-medium">
                            Expected structured output JSON schema:
                          </p>
                          <button
                            onClick={() => handleCopySchema(agentDetail?.output_schema)}
                            className="inline-flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded text-[11px] text-slate-300 transition-colors"
                          >
                            <Copy className="h-3 w-3" />
                            {copied ? "Copied!" : "Copy JSON"}
                          </button>
                        </div>
                        <pre className="p-4 bg-slate-950 border border-slate-800 rounded-xl font-mono text-xs text-cyan-200 overflow-x-auto leading-relaxed">
                          {JSON.stringify(agentDetail?.output_schema, null, 2)}
                        </pre>
                      </div>
                    )}

                    {activeTab === "models" && (
                      <div className="space-y-4">
                        <p className="text-xs text-slate-300">
                          Planner and worker model resolution requirements for this agent:
                        </p>
                        <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl space-y-3">
                          {agentDetail?.model_requirements ? (
                            <pre className="font-mono text-xs text-emerald-300 overflow-x-auto">
                              {JSON.stringify(agentDetail.model_requirements, null, 2)}
                            </pre>
                          ) : (
                            <p className="text-xs text-slate-400">No specific model constraints declared.</p>
                          )}
                        </div>
                      </div>
                    )}

                    {activeTab === "versions" && (
                      <div className="space-y-3">
                        <p className="text-xs text-slate-300">
                          Immutable version snapshots in QA AgentOS registry:
                        </p>
                        <div className="space-y-2">
                          {agentDetail?.versions.map((ver) => (
                            <div
                              key={ver.id}
                              className="p-3 bg-slate-950 border border-slate-800 rounded-xl flex items-center justify-between text-xs font-mono"
                            >
                              <div className="space-y-0.5">
                                <div className="flex items-center gap-2">
                                  <span className="font-bold text-slate-200">v{ver.version}</span>
                                  <span
                                    className={`px-1.5 py-0.2 rounded text-[10px] ${
                                      ver.status === "active"
                                        ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                                        : "bg-slate-800 text-slate-400"
                                    }`}
                                  >
                                    {ver.status}
                                  </span>
                                </div>
                                <p className="text-[10px] text-slate-400">
                                  hash: {ver.prompt_hash ? ver.prompt_hash.substring(0, 16) : "n/a"}...
                                </p>
                              </div>
                              <span className="text-[10px] text-slate-400">
                                {new Date(ver.created_at).toLocaleDateString()}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Modal Footer */}
              <div className="p-4 border-t border-slate-800 bg-slate-950/40 flex items-center justify-end">
                <button
                  onClick={() => {
                    setSelectedAgentSlug(null);
                    setAgentDetail(null);
                  }}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-200 rounded-lg transition-colors"
                >
                  Close Specification
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ConsoleShell>
  );
}
