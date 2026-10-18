import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Use standalone in Linux/Docker container; disable on Windows to prevent non-admin symlink EPERM
  output: process.platform === "win32" ? undefined : "standalone",
  reactStrictMode: true,
  transpilePackages: [
    "@21st-extension/toolbar-next",
    "@21st-extension/toolbar-react",
    "@21st-extension/toolbar",
  ],
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "@21st-extension/toolbar$": path.resolve(
        __dirname,
        "node_modules/@21st-extension/toolbar/dist/index.es.js"
      ),
    };
    return config;
  },
  async rewrites() {
    return [
      {
        source: "/health",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/health`,
      },
      {
        source: "/v1/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
