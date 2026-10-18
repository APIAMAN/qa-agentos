"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Rocket, AlertCircle, RefreshCw } from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { RunLauncherForm, AgentItem } from "@/components/runs/RunLauncherForm";
import { useAuth } from "@/components/auth/AuthContext";

function RunLauncherInner() {
  const searchParams = useSearchParams();
  const initialAgentSlug = searchParams.get("agent") || undefined;
  const { token, selectedProject } = useAuth();

  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    async function loadAgents() {
      try {
        setLoading(true);
        setError(null);
        const headers: Record<string, string> = {};
        if (token) headers["Authorization"] = `Bearer ${token}`;
        if (selectedProject?.id) headers["X-Project-Id"] = selectedProject.id;

        const res = await fetch("/v1/agents", { headers });
        if (!res.ok) {
          throw new Error(`Failed to load agents: ${res.statusText} (${res.status})`);
        }
        const data = await res.json();
        if (isMounted) {
          const items: AgentItem[] = Array.isArray(data) ? data : (data.items || []);
          setAgents(items);
        }
      } catch (err: unknown) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Failed to load available agents");
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    }
    loadAgents();
    return () => {
      isMounted = false;
    };
  }, [token, selectedProject]);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Breadcrumb / Back Link */}
      <div className="flex items-center justify-between">
        <Link
          href="/runs"
          className="inline-flex items-center gap-1.5 text-xs text-neutral-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Run History
        </Link>
        <span className="text-xs text-neutral-500 font-mono">
          Context: {selectedProject?.name || "No Project"}
        </span>
      </div>

      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-white flex items-center gap-2">
          <Rocket className="w-6 h-6 text-indigo-400" />
          Run Launcher
        </h1>
        <p className="text-sm text-neutral-400 mt-1">
          Configure execution parameters and trigger an agent&apos;s workflow.
        </p>
      </div>

      {loading ? (
        <div className="bg-neutral-900/60 border border-neutral-800 rounded-xl p-12 text-center">
          <RefreshCw className="w-8 h-8 text-neutral-500 animate-spin mx-auto mb-3" />
          <p className="text-sm text-neutral-400">Loading available agents and schemas...</p>
        </div>
      ) : error ? (
        <div className="bg-red-950/30 border border-red-800/60 rounded-xl p-6 text-red-300 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 flex-shrink-0 text-red-400 mt-0.5" />
          <div>
            <h3 className="font-medium text-sm text-red-200">Unable to load agents</h3>
            <p className="text-xs mt-1 text-red-300/80">{error}</p>
          </div>
        </div>
      ) : agents.length === 0 ? (
        <div className="bg-neutral-900/60 border border-neutral-800 rounded-xl p-12 text-center">
          <AlertCircle className="w-10 h-10 text-neutral-500 mx-auto mb-3" />
          <h3 className="text-base font-medium text-white mb-1">No Agents Found</h3>
          <p className="text-xs text-neutral-400 max-w-sm mx-auto mb-4">
            There are currently no agents registered in the system to launch.
          </p>
          <Link
            href="/agents"
            className="inline-flex items-center gap-2 text-xs bg-neutral-800 hover:bg-neutral-700 text-white px-3 py-1.5 rounded-lg border border-neutral-700 transition-colors"
          >
            View Agent Catalog
          </Link>
        </div>
      ) : (
        <RunLauncherForm
          agents={agents}
          preselectedSlug={initialAgentSlug}
        />
      )}
    </div>
  );
}

export default function RunLauncherPage() {
  return (
    <ConsoleShell>
      <Suspense
        fallback={
          <div className="max-w-4xl mx-auto p-12 text-center text-neutral-500">
            <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-neutral-600" />
            <p className="text-xs">Loading Run Launcher...</p>
          </div>
        }
      >
        <RunLauncherInner />
      </Suspense>
    </ConsoleShell>
  );
}
