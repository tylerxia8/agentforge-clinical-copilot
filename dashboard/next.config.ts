import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Standalone output keeps the deploy artifact small (only the
  // server.js + traced node_modules ship to Railway, not the full
  // node_modules tree).
  output: "standalone",
  experimental: {
    // Server Actions used for the sign-out form.
    serverActions: { allowedOrigins: ["*"] },
  },
};

export default config;
