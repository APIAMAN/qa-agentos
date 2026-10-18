"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Copy,
  Database,
  Download,
  Eye,
  FileCode,
  FileJson,
  FileText,
  Filter,
  HardDrive,
  Package,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { ConsoleShell } from "@/components/layout/ConsoleShell";
import { useAuth } from "@/components/auth/AuthContext";

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

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export default function ArtifactsPage() {
  const { token, selectedProject, apiFetch } = useAuth();

  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [copiedHash, setCopiedHash] = useState<string | null>(null);

  // Preview modal
  const [previewArtifact, setPreviewArtifact] = useState<ArtifactItem | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const fetchArtifacts = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/v1/artifacts");
      if (!res.ok) {
        throw new Error(`Failed to load artifacts (${res.status})`);
      }
      const data = await res.json();
      const items = Array.isArray(data) ? data : data?.items || [];
      setArtifacts(items);
    } catch (err: any) {
      setError(err.message || "Failed to load artifact gallery");
    } finally {
      setLoading(false);
    }
  }, [token, apiFetch]);

  useEffect(() => {
    fetchArtifacts();
  }, [fetchArtifacts, selectedProject]);

  const handleCopyHash = (hash: string) => {
    navigator.clipboard.writeText(hash);
    setCopiedHash(hash);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  const handleOpenPreview = async (art: ArtifactItem) => {
    setPreviewArtifact(art);
    setPreviewLoading(true);
    setPreviewContent(null);
    try {
      const res = await apiFetch(`/v1/artifacts/${art.id}/download`);
      if (!res.ok) throw new Error("Could not fetch artifact contents");
      const text = await res.text();
      setPreviewContent(text);
    } catch (err: any) {
      setPreviewContent(`Failed to load preview: ${err.message}`);
    } finally {
      setPreviewLoading(false);
    }
  };

  const filteredArtifacts = artifacts.filter((art) => {
    if (filterType !== "all" && art.artifact_type !== filterType) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const matchName = art.name.toLowerCase().includes(q);
      const matchRun = art.run_id?.toLowerCase().includes(q);
      const matchType = art.artifact_type.toLowerCase().includes(q);
      if (!matchName && !matchRun && !matchType) return false;
    }
    return true;
  });

  const getFileIcon = (name: string, type: string) => {
    if (name.endsWith(".json") || type === "report" || type === "testcases") {
      return <FileJson className="h-6 w-6 text-indigo-400" />;
    }
    if (name.endsWith(".txt") || name.endsWith(".log") || type === "log") {
      return <FileText className="h-6 w-6 text-amber-400" />;
    }
    return <FileCode className="h-6 w-6 text-emerald-400" />;
  };

  return (
    <ConsoleShell>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="p-2 bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 rounded-lg">
                <Package className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                  Artifact Gallery
                  <span className="text-xs px-2 py-0.5 rounded font-mono bg-indigo-950 text-indigo-300 border border-indigo-800/60">
                    Slice 5
                  </span>
                </h1>
                <p className="text-xs text-slate-400 mt-0.5">
                  Secure S3/MinIO storage pipeline for test suites, reports, execution logs, and validation evidence.
                </p>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-900 border border-slate-800 rounded-lg text-xs font-mono text-slate-300">
              <HardDrive className="h-3.5 w-3.5 text-emerald-400" />
              <span>MinIO S3 Ready</span>
            </div>
            <button
              onClick={() => fetchArtifacts()}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs text-slate-300 rounded-lg transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Filters and Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-4">
          <div className="flex items-center bg-slate-900 p-1 rounded-lg border border-slate-800 text-xs">
            {["all", "report", "testcases", "log"].map((t) => (
              <button
                key={t}
                onClick={() => setFilterType(t)}
                className={`px-3 py-1.5 rounded-md font-medium capitalize transition-colors ${
                  filterType === t
                    ? "bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 shadow-sm"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                {t === "all" ? `All (${artifacts.length})` : t}
              </button>
            ))}
          </div>

          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              placeholder="Search filename or run..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full sm:w-64 bg-slate-900 border border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-400 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        {/* Gallery Content */}
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div
                key={i}
                className="h-44 bg-slate-900/60 rounded-xl border border-slate-800 animate-pulse"
              />
            ))}
          </div>
        ) : error ? (
          <div className="p-6 bg-rose-950/20 border border-rose-800/60 rounded-xl text-center space-y-2">
            <AlertTriangle className="h-6 w-6 text-rose-400 mx-auto" />
            <p className="text-sm font-medium text-rose-300">{error}</p>
            <button
              onClick={() => fetchArtifacts()}
              className="text-xs text-rose-400 hover:underline"
            >
              Try again
            </button>
          </div>
        ) : filteredArtifacts.length === 0 ? (
          <div className="p-12 bg-slate-900/40 border border-slate-800 rounded-xl text-center space-y-3">
            <div className="h-10 w-10 bg-slate-800/80 rounded-full flex items-center justify-center mx-auto text-slate-400">
              <Package className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-medium text-slate-200">No artifacts found</p>
              <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                Completed runs automatically persist generated test suites, reports, and logs to the MinIO artifact pipeline.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredArtifacts.map((art) => (
              <div
                key={art.id}
                className="bg-slate-900/60 border border-slate-800 hover:border-slate-700/80 rounded-xl p-4 flex flex-col justify-between space-y-3 transition-all hover:shadow-md"
              >
                {/* Header info */}
                <div className="space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2.5 overflow-hidden">
                      <div className="p-2 bg-slate-950 rounded-lg border border-slate-800 shrink-0">
                        {getFileIcon(art.name, art.artifact_type)}
                      </div>
                      <div className="overflow-hidden">
                        <h3 className="text-xs font-semibold text-slate-200 truncate" title={art.name}>
                          {art.name}
                        </h3>
                        <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/60">
                          {art.artifact_type}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Run association */}
                  {art.run_id && (
                    <div className="text-[11px] text-slate-400 font-mono flex items-center gap-1.5">
                      <span>Run:</span>
                      <Link
                        href={`/runs/${art.run_id}`}
                        className="text-indigo-400 hover:underline flex items-center gap-1 truncate"
                      >
                        <span>{art.run_id.slice(0, 8)}...</span>
                        <ArrowRight className="h-3 w-3" />
                      </Link>
                    </div>
                  )}

                  {/* Size & Created */}
                  <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono pt-1 border-t border-slate-800/60">
                    <span>{formatBytes(art.file_size_bytes)}</span>
                    <span>{new Date(art.created_at).toLocaleDateString()}</span>
                  </div>

                  {/* Content Hash */}
                  <div className="flex items-center justify-between bg-slate-950 px-2 py-1 rounded text-[10px] font-mono text-slate-400 border border-slate-800/80">
                    <span className="truncate mr-2">sha256: {art.content_hash.slice(0, 12)}...</span>
                    <button
                      onClick={() => handleCopyHash(art.content_hash)}
                      className="text-slate-400 hover:text-white shrink-0"
                      title="Copy full SHA-256 hash"
                    >
                      {copiedHash === art.content_hash ? (
                        <Check className="h-3 w-3 text-emerald-400" />
                      ) : (
                        <Copy className="h-3 w-3" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Card Actions */}
                <div className="flex items-center gap-2 pt-2 border-t border-slate-800/80">
                  <button
                    onClick={() => handleOpenPreview(art)}
                    className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition-colors"
                  >
                    <Eye className="h-3.5 w-3.5" />
                    <span>Preview</span>
                  </button>

                  <a
                    href={`/v1/artifacts/${art.id}/download`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <Download className="h-3.5 w-3.5" />
                    <span>Download</span>
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Preview Modal */}
        {previewArtifact && (
          <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full p-5 space-y-4 shadow-xl max-h-[85vh] flex flex-col">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2 overflow-hidden">
                  <FileText className="h-5 w-5 text-indigo-400 shrink-0" />
                  <h3 className="text-sm font-semibold text-white truncate">
                    {previewArtifact.name}
                  </h3>
                </div>
                <button
                  onClick={() => setPreviewArtifact(null)}
                  className="text-slate-400 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex-1 overflow-auto bg-slate-950 p-4 rounded-lg border border-slate-800 font-mono text-xs text-slate-300">
                {previewLoading ? (
                  <div className="flex items-center justify-center py-12 gap-2 text-slate-400">
                    <RefreshCw className="h-4 w-4 animate-spin" />
                    <span>Loading artifact stream...</span>
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap">{previewContent}</pre>
                )}
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-800 text-xs">
                <div className="text-slate-400 font-mono">
                  {formatBytes(previewArtifact.file_size_bytes)} • {previewArtifact.artifact_type}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPreviewArtifact(null)}
                    className="px-3 py-1.5 text-slate-400 hover:text-white rounded-lg transition-colors"
                  >
                    Close
                  </button>
                  <a
                    href={`/v1/artifacts/${previewArtifact.id}/download`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors"
                  >
                    <Download className="h-3.5 w-3.5" />
                    <span>Download</span>
                  </a>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </ConsoleShell>
  );
}
