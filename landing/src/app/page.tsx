import Link from "next/link";

const PODS = [
  { id: "aurora",           label: "Aurora",         role: "Sales Organ",   pct: 65,  price: "$99/mo",  color: "#ef4444" },
  { id: "janus",            label: "Janus",          role: "Trading Brain", pct: 75,  price: "1% AUM",  color: "#f59e0b" },
  { id: "dan",              label: "DAN",            role: "IT Swarm",      pct: 60,  price: "$49/mo",  color: "#3b82f6" },
  { id: "syntropy",         label: "Syntropy",       role: "Tutor Organ",   pct: 85,  price: "$29/pack",color: "#10b981" },
  { id: "sentinel-prime",   label: "Sentinel Prime", role: "Doc Intel",     pct: 80,  price: "$199/mo", color: "#8b5cf6" },
  { id: "shango-automation",label: "Automation",     role: "Webhook Veins", pct: 90,  price: "$19/mo",  color: "#06b6d4" },
];

const PRICING = [
  { name: "Aurora Pro", price: "$99", period: "/mo", features: ["Unlimited AI sales calls", "Voice cloning (ElevenLabs)", "DEAP prompt evolution", "PACV nurture sequences", "Nexus dashboard access"], cta: "Start Selling", popular: true, productId: "aurora_pro" },
  { name: "Nexus Pro", price: "$299", period: "/mo", features: ["All Aurora Pro features", "Janus trading signals", "Syntropy exam packs", "Doc intel (Sentinel)", "Automation webhooks", "Priority support"], cta: "Get Everything", popular: false, productId: "nexus_pro" },
  { name: "Syntropy Pack", price: "$29", period: "/pack", features: ["50 AI study packs", "pgvector semantic search", "CrewAI debate mode", "Prophet predictions", "Mobile app access"], cta: "Buy Pack", popular: false, productId: "syntropy_pack" },
];

export default function HomePage() {
  return (
    <main className="min-h-screen">
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-4 border-b border-purple-900/30 sticky top-0 bg-[#080810]/90 backdrop-blur z-50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-purple-600 to-cyan-500 flex items-center justify-center text-sm font-bold">SN</div>
          <span className="font-semibold text-white">Shango Nexus</span>
        </div>
        <div className="hidden md:flex items-center gap-6 text-sm text-gray-400">
          <a href="#pods" className="hover:text-white">Pods</a>
          <a href="#pricing" className="hover:text-white">Pricing</a>
          <Link href="/nexus" className="hover:text-white">Dashboard</Link>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/nexus" className="text-sm text-gray-400 hover:text-white px-3 py-1.5">Live KPIs →</Link>
          <a href="#pricing" className="btn-primary text-sm">Get Started</a>
        </div>
      </nav>

      {/* Hero */}
      <section className="text-center py-24 px-6 max-w-5xl mx-auto">
        <div className="inline-flex items-center gap-2 bg-purple-900/30 border border-purple-700/30 rounded-full px-4 py-1.5 text-sm text-purple-300 mb-8">
          <span className="w-2 h-2 rounded-full bg-green-400 pulse-dot inline-block" />
          Kolkata, India · 13 AI pods running 24/7
        </div>
        <h1 className="text-5xl md:text-7xl font-bold mb-6 leading-tight">
          <span className="gradient-text">Alien Intelligence</span>
          <br />
          <span className="text-white">built in Kolkata</span>
        </h1>
        <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
          Shango Nexus fuses 13 AI pods into one self-evolving system — Aurora sells, Janus trades, Syntropy teaches, and DEAP genetics improve every pod autonomously.
        </p>
        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <a href="#pricing" className="btn-primary text-lg">Start with Aurora $99/mo</a>
          <Link href="/nexus" className="border border-purple-700 text-purple-300 hover:bg-purple-900/30 px-6 py-3 rounded-lg font-medium transition-all">
            Live Nexus Dashboard →
          </Link>
        </div>
      </section>

      {/* Pods Grid */}
      <section id="pods" className="py-20 px-6 max-w-6xl mx-auto">
        <h2 className="text-3xl font-bold text-center mb-3 text-white">Prometheus Organs</h2>
        <p className="text-gray-400 text-center mb-12">Each pod is a discrete AI system. All share the same cascade LLM, DEAP evolution, and pgvector memory.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {PODS.map(pod => (
            <div key={pod.id} className="nexus-card p-6">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-white text-lg">{pod.label}</h3>
                  <p className="text-gray-500 text-sm">{pod.role}</p>
                </div>
                <span className="text-sm font-medium px-2 py-1 rounded" style={{ background: pod.color + "20", color: pod.color }}>{pod.price}</span>
              </div>
              <div className="mt-4">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>Completion</span>
                  <span>{pod.pct}%</span>
                </div>
                <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${pod.pct}%`, background: pod.color }} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Metrics */}
      <section className="py-16 bg-purple-950/20 border-y border-purple-900/20">
        <div className="max-w-5xl mx-auto px-6 grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
          {[
            { value: "13", label: "Active Pods" },
            { value: "6-LLM", label: "Cascade Stack" },
            { value: "DEAP", label: "Genetic Evolution" },
            { value: "24h", label: "Cache TTL" },
          ].map(m => (
            <div key={m.label}>
              <div className="text-3xl font-bold gradient-text">{m.value}</div>
              <div className="text-gray-400 text-sm mt-1">{m.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20 px-6 max-w-5xl mx-auto">
        <h2 className="text-3xl font-bold text-center mb-3 text-white">Simple Pricing</h2>
        <p className="text-gray-400 text-center mb-12">No hidden fees. Cancel anytime. team@shango.in for enterprise.</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {PRICING.map(plan => (
            <div key={plan.name} className={`nexus-card p-6 flex flex-col ${plan.popular ? "border-purple-500/60 bg-purple-900/10" : ""}`}>
              {plan.popular && <div className="text-xs font-semibold text-purple-400 mb-3 uppercase tracking-wider">Most Popular</div>}
              <h3 className="text-xl font-bold text-white mb-1">{plan.name}</h3>
              <div className="flex items-end gap-1 mb-6">
                <span className="text-4xl font-bold gradient-text">{plan.price}</span>
                <span className="text-gray-400 mb-1">{plan.period}</span>
              </div>
              <ul className="space-y-2 mb-8 flex-1">
                {plan.features.map(f => (
                  <li key={f} className="flex items-center gap-2 text-sm text-gray-300">
                    <span className="text-green-400">✓</span> {f}
                  </li>
                ))}
              </ul>
              <a
                href={`/api/payments/stripe/checkout?product=${plan.productId}`}
                className={`text-center py-3 rounded-lg font-medium transition-all ${plan.popular ? "btn-primary" : "border border-purple-700 text-purple-300 hover:bg-purple-900/30"}`}
              >
                {plan.cta}
              </a>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6 text-center">
        <h2 className="text-4xl font-bold text-white mb-4">Ready to go alien?</h2>
        <p className="text-gray-400 mb-8">Email us at <a href="mailto:team@shango.in" className="text-purple-400 hover:underline">team@shango.in</a> or start immediately.</p>
        <a href="#pricing" className="btn-primary text-lg">Launch Nexus →</a>
      </section>

      <footer className="border-t border-purple-900/20 py-8 text-center text-gray-500 text-sm">
        © 2026 Shango India · Kolkata · shango.in · Built with alien intelligence
      </footer>
    </main>
  );
}
