"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Code2,
  Copy,
  Layers,
  Play,
  RotateCcw,
  Sliders,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/components/auth/AuthContext";

export interface AgentItem {
  id: string;
  slug: string;
  name: string;
  description?: string;
  agent_type: string;
  version?: string;
  status?: string;
  input_schema?: Record<string, any>;
  available_versions: string[];
}

interface RunLauncherFormProps {
  agents: AgentItem[];
  preselectedSlug?: string;
}

export function RunLauncherForm({ agents, preselectedSlug }: RunLauncherFormProps) {
  const router = useRouter();
  const { apiFetch, selectedProject } = useAuth();

  const [selectedSlug, setSelectedSlug] = useState<string>(
    preselectedSlug || (agents.length > 0 ? agents[0].slug : "")
  );
  const [selectedVersion, setSelectedVersion] = useState<string>("");
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [idempotencyKey, setIdempotencyKey] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [showJsonPreview, setShowJsonPreview] = useState(false);
  const [copied, setCopied] = useState(false);

  // Current agent
  const currentAgent = agents.find((a) => a.slug === selectedSlug);

  // Initialize form defaults when agent changes
  useEffect(() => {
    if (!currentAgent) return;
    setSelectedVersion(currentAgent.version || "1.0.0");

    const props = currentAgent.input_schema?.properties || {};
    const initial: Record<string, any> = {};

    Object.keys(props).forEach((key) => {
      const field = props[key];
      if (field.default !== undefined) {
        initial[key] = field.default;
      } else if (field.type === "array") {
        initial[key] = [];
      } else if (field.type === "boolean") {
        initial[key] = false;
      } else if (field.type === "number" || field.type === "integer") {
        initial[key] = "";
      } else {
        // Sample starter text for requirements
        if (key === "requirements") {
          initial[key] = "User authentication and JWT token lifecycle validation suite.";
        } else {
          initial[key] = "";
        }
      }
    });

    setFormData(initial);
    setValidationErrors({});
    setApiError(null);
  }, [currentAgent]);

  const handleFieldChange = (key: string, value: any) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
    if (validationErrors[key]) {
      setValidationErrors((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  };

  const handleArrayToggle = (key: string, item: string) => {
    const currentList: string[] = Array.isArray(formData[key]) ? formData[key] : [];
    const nextList = currentList.includes(item)
      ? currentList.filter((x) => x !== item)
      : [...currentList, item];
    handleFieldChange(key, nextList);
  };

  const validate = (): boolean => {
    if (!currentAgent?.input_schema) return true;
    const errors: Record<string, string> = {};
    const requiredKeys: string[] = currentAgent.input_schema.required || [];
    const props = currentAgent.input_schema.properties || {};

    requiredKeys.forEach((key) => {
      const val = formData[key];
      if (val === undefined || val === null || val === "" || (Array.isArray(val) && val.length === 0)) {
        errors[key] = `${props[key]?.title || key} is required`;
      }
    });

    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setApiError(null);

    if (!validate()) {
      return;
    }

    setSubmitting(true);

    try {
      // Build clean input payload
      const cleanedInput: Record<string, any> = {};
      const props = currentAgent?.input_schema?.properties || {};

      Object.keys(props).forEach((key) => {
        const val = formData[key];
        const prop = props[key];
        if (val !== "" && val !== undefined && val !== null) {
          if (prop.type === "number" || prop.type === "integer") {
            const num = Number(val);
            if (!isNaN(num)) cleanedInput[key] = num;
          } else if (prop.type === "array") {
            if (Array.isArray(val) && val.length > 0) cleanedInput[key] = val;
          } else {
            cleanedInput[key] = val;
          }
        }
      });

      const payload: Record<string, any> = {
        agent: selectedSlug,
        input: cleanedInput,
      };
      if (selectedVersion) {
        payload.version = selectedVersion;
      }

      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (idempotencyKey.trim()) {
        headers["Idempotency-Key"] = idempotencyKey.trim();
      }

      const res = await apiFetch("/v1/runs", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        let msg = errData?.error?.message || errData?.detail || `Run dispatch failed (${res.status})`;
        if (errData?.error?.details && Array.isArray(errData.error.details)) {
          const detailMsgs = errData.error.details.map((d: any) => `${d.path}: ${d.message}`).join(", ");
          msg = `${msg} (${detailMsgs})`;
        }
        throw new Error(msg);
      }

      const createdRun = await res.json();
      router.push(`/runs/${createdRun.id}`);
    } catch (err: any) {
      setApiError(err.message || "Failed to dispatch run");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCopyPayload = () => {
    navigator.clipboard.writeText(JSON.stringify(formData, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const generateIdempotencyKey = () => {
    setIdempotencyKey(`run_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Agent Selection Card */}
      <div className="p-5 bg-slate-900 border border-slate-800 rounded-xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-indigo-400" />
            <h2 className="text-sm font-semibold text-slate-100">Select Target Agent</h2>
          </div>
          <span className="text-[11px] font-mono text-slate-400">
            {agents.length} available
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1 font-medium">Agent</label>
            <select
              value={selectedSlug}
              onChange={(e) => setSelectedSlug(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-medium"
            >
              {agents.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.name} ({a.slug})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1 font-medium">Version</label>
            <select
              value={selectedVersion}
              onChange={(e) => setSelectedVersion(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
            >
              {currentAgent?.available_versions.map((v) => (
                <option key={v} value={v}>
                  v{v} {v === currentAgent.version ? "(active)" : ""}
                </option>
              ))}
            </select>
          </div>
        </div>

        {currentAgent && (
          <p className="text-xs text-slate-300 bg-slate-950/60 p-3 rounded-lg border border-slate-800/80 leading-relaxed">
            {currentAgent.description}
          </p>
        )}
      </div>

      {/* Dynamic Schema Inputs */}
      <div className="p-5 bg-slate-900 border border-slate-800 rounded-xl space-y-5">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <Sliders className="h-4 w-4 text-indigo-400" />
            <h2 className="text-sm font-semibold text-slate-100">Input Parameters (Validated)</h2>
          </div>
          <span className="text-[11px] font-mono text-cyan-400">
            Draft 2020-12 Schema
          </span>
        </div>

        {currentAgent?.input_schema?.properties ? (
          <div className="space-y-4">
            {Object.keys(currentAgent.input_schema.properties).map((key) => {
              const field = currentAgent.input_schema!.properties[key];
              const isRequired = currentAgent.input_schema!.required?.includes(key);
              const fieldError = validationErrors[key];

              // Render textareas for large fields
              const isMultiLine =
                field.type === "string" &&
                !field.enum &&
                (key === "requirements" || key === "context" || key === "specification" || key === "description");

              return (
                <div key={key} className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-slate-200 flex items-center gap-1">
                      <span>{field.title || key}</span>
                      {isRequired && <span className="text-indigo-400 font-bold">*</span>}
                      <span className="text-[10px] text-slate-500 font-mono ml-1">
                        ({field.type}{field.enum ? " enum" : ""})
                      </span>
                    </label>
                    {field.default !== undefined && (
                      <span className="text-[10px] text-slate-500 font-mono">
                        default: {String(field.default)}
                      </span>
                    )}
                  </div>

                  {field.description && (
                    <p className="text-[11px] text-slate-400">{field.description}</p>
                  )}

                  {/* Enum Select */}
                  {field.enum ? (
                    <select
                      value={formData[key] || ""}
                      onChange={(e) => handleFieldChange(key, e.target.value)}
                      className={`w-full bg-slate-950 border ${
                        fieldError ? "border-rose-500" : "border-slate-800"
                      } rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-mono`}
                    >
                      {field.enum.map((opt: string) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : field.type === "array" && field.items?.enum ? (
                    /* Multi-select Pills for enum array */
                    <div className="flex flex-wrap gap-2 pt-1">
                      {field.items.enum.map((opt: string) => {
                        const selected = Array.isArray(formData[key]) && formData[key].includes(opt);
                        return (
                          <button
                            type="button"
                            key={opt}
                            onClick={() => handleArrayToggle(key, opt)}
                            className={`px-3 py-1 rounded-lg text-xs font-mono transition-colors border ${
                              selected
                                ? "bg-indigo-600 text-white border-indigo-500 font-medium shadow-sm"
                                : "bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200 hover:border-slate-700"
                            }`}
                          >
                            {opt}
                          </button>
                        );
                      })}
                    </div>
                  ) : isMultiLine ? (
                    /* Multiline Textarea */
                    <textarea
                      rows={4}
                      value={formData[key] || ""}
                      onChange={(e) => handleFieldChange(key, e.target.value)}
                      placeholder={`Enter ${key}...`}
                      className={`w-full bg-slate-950 border ${
                        fieldError ? "border-rose-500" : "border-slate-800"
                      } rounded-lg p-3 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 font-sans leading-relaxed`}
                    />
                  ) : field.type === "boolean" ? (
                    /* Checkbox */
                    <label className="flex items-center gap-2 cursor-pointer pt-1">
                      <input
                        type="checkbox"
                        checked={!!formData[key]}
                        onChange={(e) => handleFieldChange(key, e.target.checked)}
                        className="rounded border-slate-800 bg-slate-950 text-indigo-600 focus:ring-0"
                      />
                      <span className="text-xs text-slate-300">Enable {key}</span>
                    </label>
                  ) : (
                    /* Regular Input */
                    <input
                      type={field.type === "number" || field.type === "integer" ? "number" : "text"}
                      value={formData[key] || ""}
                      onChange={(e) => handleFieldChange(key, e.target.value)}
                      placeholder={`Enter ${key}...`}
                      className={`w-full bg-slate-950 border ${
                        fieldError ? "border-rose-500" : "border-slate-800"
                      } rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500`}
                    />
                  )}

                  {fieldError && (
                    <p className="text-[11px] text-rose-400 font-medium flex items-center gap-1">
                      <AlertCircle className="h-3 w-3" />
                      {fieldError}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-slate-500">No schema parameters found for this agent.</p>
        )}
      </div>

      {/* Advanced Options (Idempotency Key & Preview) */}
      <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-xl space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-300">Execution Parameters</span>
          <button
            type="button"
            onClick={() => setShowJsonPreview(!showJsonPreview)}
            className="text-[11px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1 font-medium"
          >
            <Code2 className="h-3.5 w-3.5" />
            {showJsonPreview ? "Hide JSON Preview" : "Inspect Payload"}
          </button>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[11px] text-slate-400">Idempotency Key (Optional)</label>
            <button
              type="button"
              onClick={generateIdempotencyKey}
              className="text-[10px] text-indigo-400 hover:underline"
            >
              Generate Key
            </button>
          </div>
          <input
            type="text"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
            placeholder="Unique key for safe duplicate prevention..."
            className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
          />
        </div>

        {showJsonPreview && (
          <div className="pt-2 border-t border-slate-800/80 space-y-2">
            <div className="flex items-center justify-between text-[11px] text-slate-400">
              <span>Canonical Payload:</span>
              <button
                type="button"
                onClick={handleCopyPayload}
                className="hover:text-slate-200 flex items-center gap-1 text-[10px]"
              >
                <Copy className="h-3 w-3" />
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <pre className="p-3 bg-slate-950 border border-slate-800 rounded-lg font-mono text-[11px] text-indigo-300 overflow-x-auto max-h-48">
              {JSON.stringify({ agent: selectedSlug, version: selectedVersion, input: formData }, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Error Banner */}
      {apiError && (
        <div className="p-4 bg-rose-950/40 border border-rose-800/80 rounded-xl text-xs text-rose-300 flex items-start gap-2.5">
          <AlertCircle className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold">Dispatch Error</p>
            <p className="text-[11px] leading-relaxed">{apiError}</p>
          </div>
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={() => router.push("/runs")}
          className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-xs font-medium rounded-lg text-slate-300 transition-colors"
        >
          Cancel
        </button>

        <button
          type="submit"
          disabled={submitting}
          className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg shadow-sm transition-colors flex items-center gap-2"
        >
          {submitting ? (
            <>
              <div className="h-3.5 w-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              <span>Scheduling Run...</span>
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>Launch Run</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </>
          )}
        </button>
      </div>
    </form>
  );
}
