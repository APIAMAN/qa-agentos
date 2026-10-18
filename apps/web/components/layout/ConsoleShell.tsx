"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  CheckSquare,
  Package,
  FolderGit2,
  Layers,
  LayoutDashboard,
  LogOut,
  Menu,
  Server,
  User,
  X,
} from "lucide-react";
import { useAuth } from "@/components/auth/AuthContext";

interface ConsoleShellProps {
  children: React.ReactNode;
}

export function ConsoleShell({ children }: ConsoleShellProps) {
  const pathname = usePathname();
  const { token, isAuthenticated, logout, projects, selectedProject, setSelectedProject } = useAuth();
  const [backendHealth, setBackendHealth] = useState<"checking" | "healthy" | "unreachable">("checking");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    fetch("/health")
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error("HTTP error");
      })
      .then((data) => {
        if (data?.status === "healthy") {
          setBackendHealth("healthy");
        } else {
          setBackendHealth("unreachable");
        }
      })
      .catch(() => setBackendHealth("unreachable"));
  }, []);

  const navItems = [
    {
      name: "Dashboard & Diagnostics",
      href: "/",
      icon: LayoutDashboard,
      active: pathname === "/",
    },
    {
      name: "Agent Catalog",
      href: "/agents",
      icon: Bot,
      active: pathname.startsWith("/agents"),
      badge: "Slice 1",
    },
    {
      name: "Runs & Workflows",
      href: "/runs",
      icon: Activity,
      active: pathname.startsWith("/runs"),
      badge: "Slice 2",
    },
    {
      name: "Approval Inbox",
      href: "/approvals",
      icon: CheckSquare,
      active: pathname.startsWith("/approvals"),
      badge: "Slice 4",
    },
    {
      name: "Artifact Gallery",
      href: "/artifacts",
      icon: Package,
      active: pathname.startsWith("/artifacts"),
      badge: "Slice 5",
    },
  ];

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100 font-sans antialiased">
      {/* Sidebar Desktop */}
      <aside className="hidden md:flex w-64 flex-col border-r border-slate-800 bg-slate-900/40 p-4 justify-between">
        <div className="space-y-6">
          {/* Brand */}
          <div className="flex items-center space-x-3 px-2 py-1">
            <div className="p-2 bg-indigo-600 rounded-lg shadow-sm">
              <Bot className="h-5 w-5 text-white" />
            </div>
            <div>
              <div className="font-bold tracking-tight text-slate-100 text-sm flex items-center gap-1.5">
                QA AgentOS
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-400 border border-indigo-800">
                  M2
                </span>
              </div>
              <p className="text-[11px] text-slate-400">Enterprise AI Testing Platform</p>
            </div>
          </div>

          {/* Project Selector */}
          {isAuthenticated && projects.length > 0 && (
            <div className="px-2">
              <label className="block text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">
                Active Project
              </label>
              <div className="relative">
                <select
                  value={selectedProject?.id || ""}
                  onChange={(e) => {
                    const match = projects.find((p) => p.id === e.target.value);
                    if (match) setSelectedProject(match);
                  }}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-mono"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {/* Navigation */}
          <nav className="space-y-1">
            <div className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Core Platform
            </div>
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  className={`flex items-center justify-between px-3 py-2 text-xs font-medium rounded-lg transition-colors ${
                    item.active
                      ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/30"
                      : "text-slate-300 hover:bg-slate-800/60 hover:text-white"
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className="h-4 w-4" />
                    <span>{item.name}</span>
                  </div>
                  {item.badge && (
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                        item.active
                          ? "bg-indigo-500/20 text-indigo-200 border border-indigo-400/40"
                          : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>
        </div>

        {/* User / Health Footer in Sidebar */}
        <div className="pt-4 border-t border-slate-800/80 space-y-3">
          {/* Health Pill */}
          <div className="flex items-center justify-between px-3 py-2 bg-slate-950/60 rounded-lg border border-slate-800 text-xs">
            <span className="text-slate-400 flex items-center gap-1.5">
              <Server className="h-3.5 w-3.5 text-slate-400" />
              API Gateway
            </span>
            <div className="flex items-center gap-1.5">
              {backendHealth === "healthy" ? (
                <>
                  <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-[11px] font-mono text-emerald-400 font-medium">Healthy</span>
                </>
              ) : backendHealth === "checking" ? (
                <>
                  <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
                  <span className="text-[11px] font-mono text-amber-400">Checking...</span>
                </>
              ) : (
                <>
                  <span className="h-2 w-2 rounded-full bg-rose-400" />
                  <span className="text-[11px] font-mono text-rose-400">Offline</span>
                </>
              )}
            </div>
          </div>

          {/* User Profile */}
          <div className="flex items-center justify-between px-2 text-xs">
            <div className="flex items-center gap-2 overflow-hidden">
              <div className="h-7 w-7 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center shrink-0">
                <User className="h-3.5 w-3.5 text-slate-300" />
              </div>
              <div className="overflow-hidden">
                <p className="font-medium text-slate-200 truncate text-[11px]">
                  {isAuthenticated ? "Admin Operator" : "Unauthenticated"}
                </p>
                <p className="text-[10px] text-slate-400 truncate font-mono">
                  {isAuthenticated ? "admin@qa-agentos.local" : "Guest Mode"}
                </p>
              </div>
            </div>
            {isAuthenticated && (
              <button
                onClick={logout}
                title="Sign out"
                className="p-1.5 text-slate-400 hover:text-rose-400 rounded-md transition-colors"
              >
                <LogOut className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Main Container */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Navbar */}
        <header className="h-14 border-b border-slate-800 bg-slate-900/30 backdrop-blur px-4 md:px-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800"
            >
              {mobileMenuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span className="hidden sm:inline">Console</span>
              <span className="hidden sm:inline">/</span>
              <span className="text-slate-200 font-medium capitalize">
                {pathname === "/" ? "Dashboard" : pathname.replace("/", "")}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {selectedProject && (
              <div className="hidden sm:flex items-center gap-1.5 bg-slate-900 px-2.5 py-1 rounded-full border border-slate-800 text-[11px] font-mono text-slate-300">
                <FolderGit2 className="h-3 w-3 text-indigo-400" />
                <span>Project: {selectedProject.slug}</span>
              </div>
            )}
            <div className="flex items-center gap-1.5 bg-slate-900 px-2.5 py-1 rounded-full border border-slate-800 text-[11px] font-mono text-slate-300">
              <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" />
              <span>Workspace: M2 Console</span>
            </div>
          </div>
        </header>

        {/* Mobile Sidebar overlay */}
        {mobileMenuOpen && (
          <div className="md:hidden border-b border-slate-800 bg-slate-900 p-4 space-y-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center justify-between px-3 py-2 text-xs font-medium rounded-lg ${
                    item.active
                      ? "bg-indigo-600 text-white"
                      : "text-slate-300 hover:bg-slate-800"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Icon className="h-4 w-4" />
                    <span>{item.name}</span>
                  </div>
                  {item.badge && (
                    <span className="text-[10px] font-mono px-1 rounded bg-slate-800 text-slate-400">
                      {item.badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        )}

        {/* Page Content */}
        <main className="flex-1 p-4 md:p-8 max-w-7xl w-full mx-auto">{children}</main>
      </div>
    </div>
  );
}
