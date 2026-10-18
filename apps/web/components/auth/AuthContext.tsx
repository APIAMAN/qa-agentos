"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";

export interface Project {
  id: string;
  name: string;
  slug: string;
  description?: string;
  is_active: boolean;
  created_at: string;
}

interface AuthContextType {
  token: string | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<string>;
  logout: () => void;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  projects: Project[];
  selectedProject: Project | null;
  setSelectedProject: (p: Project | null) => void;
  fetchProjects: () => Promise<Project[]>;
}

const AuthContext = createContext<AuthContextType>({
  token: null,
  isAuthenticated: false,
  login: async () => "",
  logout: () => {},
  apiFetch: async () => new Response(),
  projects: [],
  selectedProject: null,
  setSelectedProject: () => {},
  fetchProjects: async () => [],
});

const TOKEN_KEY = "qa_agentos_token";
const PROJECT_KEY = "qa_agentos_project_id";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProjectState] = useState<Project | null>(null);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(TOKEN_KEY);
      if (stored) {
        setToken(stored);
      }
    } catch {
      // Ignore localStorage errors
    }
  }, []);

  const apiFetch = useCallback(
    async (input: string, init?: RequestInit): Promise<Response> => {
      const headers = new Headers(init?.headers);
      if (token && !headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      if (selectedProject?.id && !headers.has("X-Project-Id")) {
        headers.set("X-Project-Id", selectedProject.id);
      }
      return fetch(input, { ...init, headers });
    },
    [token, selectedProject]
  );

  const fetchProjects = useCallback(async (): Promise<Project[]> => {
    if (!token) return [];
    try {
      const res = await fetch("/v1/projects", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data: Project[] = await res.json();
        setProjects(data);

        // Restore or default selected project
        const storedProjId = typeof window !== "undefined" ? localStorage.getItem(PROJECT_KEY) : null;
        const matched = data.find((p) => p.id === storedProjId);
        if (matched) {
          setSelectedProjectState(matched);
        } else if (data.length > 0) {
          setSelectedProjectState(data[0]);
          try {
            localStorage.setItem(PROJECT_KEY, data[0].id);
          } catch {
            // Ignore
          }
        }
        return data;
      }
    } catch (err) {
      console.error("Failed to fetch projects", err);
    }
    return [];
  }, [token]);

  useEffect(() => {
    if (token) {
      fetchProjects();
    } else {
      setProjects([]);
      setSelectedProjectState(null);
    }
  }, [token, fetchProjects]);

  const setSelectedProject = useCallback((p: Project | null) => {
    setSelectedProjectState(p);
    try {
      if (p) {
        localStorage.setItem(PROJECT_KEY, p.id);
      } else {
        localStorage.removeItem(PROJECT_KEY);
      }
    } catch {
      // Ignore
    }
  }, []);

  const login = useCallback(
    async (email: string, password: string): Promise<string> => {
      const res = await fetch("/v1/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        const message = errData?.error?.message || errData?.detail || "Authentication failed";
        throw new Error(message);
      }

      const data = await res.json();
      const accessToken = data.access_token;
      setToken(accessToken);
      try {
        localStorage.setItem(TOKEN_KEY, accessToken);
      } catch {
        // Ignore
      }
      return accessToken;
    },
    []
  );

  const logout = useCallback(() => {
    setToken(null);
    setProjects([]);
    setSelectedProjectState(null);
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(PROJECT_KEY);
    } catch {
      // Ignore
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token,
        isAuthenticated: !!token,
        login,
        logout,
        apiFetch,
        projects,
        selectedProject,
        setSelectedProject,
        fetchProjects,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
