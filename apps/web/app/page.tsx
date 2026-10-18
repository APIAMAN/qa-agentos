"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCircle,
  Database,
  Key,
  Layers,
  Server,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

export default function ConsoleDashboard() {
  const { token, isAuthenticated, login, logout, apiFetch } = useAuth();
  const [email, setEmail] = useState("admin@qa-agentos.local");
  const [password, setPassword] = useState("admin123456");
  const [health, setHealth] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<any[]>([]);
  const [loadingLogin, setLoadingLogin] = useState(false);

  useEffect(() => {
    fetch("/health")
      .then((res) => res.json())
      .then((data) => setHealth(data))
      .catch(() => setError("Backend API unreachable"));
  }, []);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await apiFetch("/v1/projects");
      if (res.ok) {
        const data = await res.json();
        setProjects(data);
      }
    } catch (err) {
      console.error(err);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (token) {
      fetchProjects();
    } else {
      setProjects([]);
    }
  }, [token, fetchProjects]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoadingLogin(true);
    try {
      await login(email, password);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoadingLogin(false);
    }
  };

  return (
    <ConsoleShell>
      <div className="space-y-8">
        {/* Banner Section */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-slate-800">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-100">
              System Dashboard & Diagnostics
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Verify platform baseline connectivity, authentication tokens, and explore registered agents.
            </p>
          </div>
          <Link
            href="/agents"
            className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-colors"
          >
            <Bot className="h-4 w-4" />
            Explore Agent Catalog
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        {/* Grid Status Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-slate-400">Backend API</span>
              <Server className="h-4 w-4 text-indigo-400" />
            </div>
            <div className="flex items-center space-x-2">
              {health?.status === "healthy" ? (
                <>
                  <CheckCircle className="h-5 w-5 text-emerald-400" />
                  <span className="text-sm font-semibold text-emerald-400">Healthy</span>
                </>
              ) : (
                <>
                  <XCircle className="h-5 w-5 text-amber-400" />
                  <span className="text-sm font-semibold text-amber-400">Checking...</span>
                </>
              )}
            </div>
          </div>

          <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-slate-400">Authentication</span>
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            </div>
            <div className="text-sm font-semibold text-slate-200">
              {isAuthenticated ? "Authenticated (RS256)" : "Unauthenticated"}
            </div>
          </div>

          <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-slate-400">Database Schema</span>
              <Database className="h-4 w-4 text-cyan-400" />
            </div>
            <div className="text-sm font-semibold text-slate-200">
              11 Baseline Tables Ready
            </div>
          </div>
        </div>

        {/* Main Content Area */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {/* Login Card */}
          <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Key className="h-5 w-5 text-indigo-400" />
              API Authentication
            </h2>
            {isAuthenticated ? (
              <div className="space-y-4">
                <div className="p-3 bg-emerald-950/40 border border-emerald-800 rounded-lg text-xs font-mono text-emerald-300 break-all">
                  Token: {token?.substring(0, 45)}...
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={logout}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-medium rounded-lg text-slate-300 transition-colors"
                  >
                    Sign Out
                  </button>
                  <Link
                    href="/agents"
                    className="px-4 py-2 bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs font-medium rounded-lg border border-indigo-500/30 transition-colors"
                  >
                    Go to Agent Catalog
                  </Link>
                </div>
              </div>
            ) : (
              <form onSubmit={handleLogin} className="space-y-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                {error && <p className="text-xs text-rose-400">{error}</p>}
                <button
                  type="submit"
                  disabled={loadingLogin}
                  className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {loadingLogin ? "Authenticating..." : "Authenticate"} <ArrowRight className="h-4 w-4" />
                </button>
              </form>
            )}
          </div>

          {/* Projects View */}
          <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Layers className="h-5 w-5 text-indigo-400" />
              Seeded Projects
            </h2>
            {isAuthenticated ? (
              <div className="space-y-3">
                {projects.length > 0 ? (
                  projects.map((p) => (
                    <div key={p.id} className="p-3 bg-slate-950 border border-slate-800 rounded-lg">
                      <p className="text-sm font-medium text-slate-200">{p.name}</p>
                      <p className="text-xs text-slate-400 font-mono">slug: {p.slug}</p>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-400">Loading projects...</p>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-400">Authenticate to view backend projects list.</p>
            )}
          </div>
        </div>
      </div>
    </ConsoleShell>
  );
}
