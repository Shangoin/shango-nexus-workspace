/**
 * landing/src/lib/api.ts
 * Region-aware API base URL selector.
 *
 * Selects the Singapore backend by default.
 * Falls back to the US node when the hostname contains "us"
 * (e.g. a Vercel preview URL served from iad1).
 *
 * Usage:
 *   import { API_BASE } from "@/lib/api";
 *   const res = await fetch(`${API_BASE}/api/nexus/kpis`);
 */

export const API_BASE: string =
  typeof window !== "undefined" &&
  (window.location.hostname.includes("us") ||
    window.location.hostname.includes("iad"))
    ? process.env.NEXT_PUBLIC_API_BASE_US ||
      "https://nexus-backend-us.onrender.com"
    : process.env.NEXT_PUBLIC_API_BASE_SG ||
      "https://nexus-backend-sg.onrender.com";

/**
 * Typed fetch wrapper — throws on non-2xx responses.
 */
export async function apiFetch<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}
