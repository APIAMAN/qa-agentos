import "./globals.css";
import type { Metadata } from "next";
import { AuthProvider } from "@/components/auth/AuthContext";
import { DevToolbar } from "@/components/dev/DevToolbar";

export const metadata: Metadata = {
  title: "QA AgentOS - Console Shell",
  description: "Enterprise Autonomous Testing Control Plane Console",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        <AuthProvider>
          <DevToolbar />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
