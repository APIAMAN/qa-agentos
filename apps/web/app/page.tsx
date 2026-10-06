"use client";

import React, { useState, useEffect } from "react";
import { Activity, Server, ShieldCheck, Database, Key, ArrowRight, CheckCircle, XCircle } from "lucide-react";

export default function ConsoleDashboard() {
  const [email, setEmail] = useState("admin@qa-agentos.local");
  const [password, setPassword] = useState("admin123456");
  const [token, setToken] = useState<string | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<any[]>([]);

  useEffect(() => {
    fetch("/health")
      .then((res) => res.json())
      .then((data) => setHealth(data))
      .catch((err) => setError("Backend API unreachable"));
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = await fetch("/v1/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.error?.message || "Login failed");
      }
      const data = await res.json();
      setToken(data.access_token);
      fetchProjects(data.access_token);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const fetchProjects = async (authToken: string) => {
    try {
      const res = await fetch("/v1/projects", {
        headers: { Authorization: `Bearer ${authToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setProjects(data);
      }
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-between p-8 bg-slate-950 text-slate-100">
      <div className="w-full max-w-5xl">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-slate-800 pb-6 mb-8">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-600 rounded-lg">
              <Activity className="h-6 w-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">QA AgentOS Console Shell</h1>
              <p className="text-xs text-slate-400">Milestone 0 Foundation Dashboard</p>
            </div>
          </div>
          <div className="flex items-center space-x-2 bg-slate-900 px-3 py-1.5 rounded-full border border-slate-800">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs font-mono text-slate-300">M0 Foundation Active</span>
          </div>
        </header>

        {/* Grid Status */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
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
              {token ? "Authenticated (RS256)" : "Unauthenticated"}
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
            {token ? (
              <div className="space-y-4">
                <div className="p-3 bg-emerald-950/40 border border-emerald-800 rounded-lg text-xs font-mono text-emerald-300 break-all">
                  Token: {token.substring(0, 40)}...
                </div>
                <button
                  onClick={() => setToken(null)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-medium rounded-lg text-slate-300"
                >
                  Sign Out
                </button>
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
                  className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  Authenticate <ArrowRight className="h-4 w-4" />
                </button>
              </form>
            )}
          </div>

          {/* Projects View */}
          <div className="p-6 bg-slate-900 border border-slate-800 rounded-xl">
            <h2 className="text-lg font-semibold mb-4">Seeded Projects</h2>
            {token ? (
              <div className="space-y-3">
                {projects.length > 0 ? (
                  projects.map((p) => (
                    <div key={p.id} className="p-3 bg-slate-950 border border-slate-800 rounded-lg">
                      <p className="text-sm font-medium text-slate-200">{p.name}</p>
                      <p className="text-xs text-slate-500 font-mono">slug: {p.slug}</p>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-400">Loading projects...</p>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-500">Authenticate to view backend projects list.</p>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
