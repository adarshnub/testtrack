import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";
import { resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "../..");
loadEnvConfig(repositoryRoot, process.env.NODE_ENV !== "production");

const config: NextConfig = {
  outputFileTracingRoot: repositoryRoot,
  serverExternalPackages: ["@prisma/client"],
  poweredByHeader: false,
  experimental: { cpus: 2 },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "same-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }];
  },
};

export default config;
