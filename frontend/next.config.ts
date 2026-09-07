import type { NextConfig } from "next";

const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
if (configured) {
  const url = new URL(configured);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must be an HTTP(S) base URL without credentials, query, or fragment.");
  }
}

const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  // Conventional same-origin forwarding: FastAPI needs no CORS or behavior changes.
  async rewrites() {
    if (!configured) return [];
    const base = configured.replace(/\/+$/, "");
    return ["health", "capabilities", "query"].map((path) => ({
      source: `/api/${path}`,
      destination: `${base}/${path}`,
    }));
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Frame-Options", value: "DENY" },
    ] }];
  },
};
export default config;
