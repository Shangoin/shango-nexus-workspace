"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const BACKEND = process.env.NEXT_PUBLIC_NEXUS_BACKEND_URL || "http://localhost:8000";

async function apiFetch(path: string) {
  const r = await fetch(`${BACKEND}${path}`, { next: { revalidate: 30 } });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

interface KPIs {
  total_pods: number;
  aurora: { total_calls: number; avg_score: number };
  evolution: { total_cycles: number; pods_evolved: number };
  events: { total: number; pods_active: number };
  estimated_mrr_usd: number;
}

interface Pod {
  id: string; label: string; role: string; completion: number; mrr: number;
}

export default function NexusPage() {
  const [kpis, setKpis] = useState<KPIs | null>(null);
  const [pods, setPods] = useState<Pod[]>([]);
  const [health, setHealth] = useState<{ status: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [k, p, h] = await Promise.all([
          apiFetch("/api/nexus/kpis"),
          apiFetch("/api/nexus/pods"),
          apiFetch("/health"),
        ]);
        setKpis(k);
        setPods(p.pods || []);
        setHealth(h);
      } catch { /* backend may be offline */ }
      finally { setLoading(false); }
    }
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="min-h-screen p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Nexus HQ</h1>
          <p className="text-gray-400 text-sm">Live metrics · Auto-refresh 30s</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-sm px-3 py-1 rounded-full ${health?.status === "ok" ? "bg-green-900/30 text-green-400" : "bg-red-900/30 text-red-400"}`}>
            {health?.status === "ok" ? "● Backend OK" : "● Backend Offline"}
          </span>
          <a href="https://nexus.shango.in:8501" target="_blank" rel="noreferrer" className="text-sm text-purple-400 hover:underline">
            Full Dashboard ↗
          </a>
          <Link href="/" className="text-sm text-gray-400 hover:text-white">← Home</Link>
        </div>
      </div>

      {loading && <div className="text-gray-400">Loading nexus metrics...</div>}

      {kpis && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
          {[
            { label: "Active Pods", value: kpis.total_pods },
            { label: "Aurora Calls", value: kpis.aurora.total_calls },
            { label: "Avg Score", value: `${kpis.aurora.avg_score}/100` },
            { label: "Evolution Cycles", value: kpis.evolution.total_cycles },
            { label: "Est. MRR", value: `$${kpis.estimated_mrr_usd.toLocaleString()}` },
          ].map(m => (
            <div key={m.label} className="nexus-card p-4 text-center">
              <div className="text-2xl font-bold text-white">{m.value}</div>
              <div className="text-gray-500 text-xs mt-1">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      <h2 className="text-xl font-semibold text-white mb-4">Prometheus Organs</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {pods.map(pod => {
          const color = pod.completion >= 80 ? "#10b981" : pod.completion >= 50 ? "#f59e0b" : "#ef4444";
          return (
            <div key={pod.id} className="nexus-card p-4">
              <div className="flex justify-between items-start mb-2">
                <div>
                  <p className="font-medium text-white text-sm">{pod.label}</p>
                  <p className="text-gray-500 text-xs">{pod.role}</p>
                </div>
                {pod.mrr > 0 && <span className="text-xs text-green-400">${pod.mrr}/mo</span>}
              </div>
              <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden mt-3">
                <div className="h-full rounded-full" style={{ width: `${pod.completion}%`, background: color }} />
              </div>
              <p className="text-right text-xs text-gray-500 mt-1">{pod.completion}%</p>
            </div>
          );
        })}
      </div>

      <div className="mt-8 text-center">
        <p className="text-gray-500 text-sm">
          Full analytics, evolution graphs, and revenue tracking →{" "}
          <a href="https://nexus.shango.in:8501" className="text-purple-400 hover:underline">Streamlit Dashboard</a>
        </p>
      </div>
    </main>
  );
}
