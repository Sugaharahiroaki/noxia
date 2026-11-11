import type { NextConfig } from "next";
import { networkInterfaces } from "node:os";

// Django API はこのサーバー経由でプロキシする（同一オリジンにしてCookie/CSRFを単純化する）
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

/** 開発モードで同じ LAN の iPad などから開けるよう、この PC の IPv4 を許可する */
function lanAddresses(): string[] {
  const out: string[] = [];
  for (const list of Object.values(networkInterfaces())) {
    for (const ni of list ?? []) {
      if (ni.family === "IPv4" && !ni.internal && !ni.address.startsWith("169.254.")) out.push(ni.address);
    }
  }
  return out;
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: lanAddresses(),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
