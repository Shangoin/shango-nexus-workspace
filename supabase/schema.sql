-- ════════════════════════════════════════════════════════════════
-- Shango Nexus — Supabase Schema
-- Run this in Supabase SQL Editor (Settings → SQL Editor)
-- ════════════════════════════════════════════════════════════════

-- Enable pgvector extension
create extension if not exists vector;

-- ── Nexus Events Bus ─────────────────────────────────────────────
create table if not exists nexus_events (
  id          uuid default gen_random_uuid() primary key,
  pod         text not null,
  event_type  text not null,
  payload     jsonb default '{}',
  timestamp   double precision not null,
  created_at  timestamptz default now()
);
create index if not exists nexus_events_pod_idx on nexus_events(pod);
create index if not exists nexus_events_ts_idx on nexus_events(timestamp desc);

-- ── DEAP Evolution History ───────────────────────────────────────
create table if not exists nexus_evolutions (
  id           uuid default gen_random_uuid() primary key,
  pod          text not null,
  best_genome  jsonb not null,
  best_score   float not null,
  generations  int,
  population   int,
  timestamp    double precision not null,
  created_at   timestamptz default now()
);
create index if not exists nexus_evolutions_pod_idx on nexus_evolutions(pod);

-- ── pgvector Semantic Memory ─────────────────────────────────────
create table if not exists nexus_memories (
  id          uuid default gen_random_uuid() primary key,
  pod         text not null,
  content     text not null,
  embedding   vector(768),              -- Gemini text-embedding-004
  metadata    jsonb default '{}',
  created_at  timestamptz default now()
);
create index if not exists nexus_memories_pod_idx on nexus_memories(pod);
create index if not exists nexus_memories_embedding_idx
  on nexus_memories using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- pgvector RPC for semantic search
create or replace function match_nexus_memories(
  query_embedding vector(768),
  match_count int,
  filter_pod text default null
)
returns table (
  id uuid, pod text, content text, metadata jsonb,
  similarity float
)
language sql stable
as $$
  select
    id, pod, content, metadata,
    1 - (embedding <=> query_embedding) as similarity
  from nexus_memories
  where filter_pod is null or pod = filter_pod
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- ── Constitution Violations ──────────────────────────────────────
create table if not exists nexus_violations (
  id          uuid default gen_random_uuid() primary key,
  pod         text not null,
  rule_id     text not null,
  severity    text not null,
  detail      text,
  created_at  timestamptz default now()
);

-- ── Subscriptions / Payments ─────────────────────────────────────
create table if not exists nexus_subscriptions (
  id                 uuid default gen_random_uuid() primary key,
  user_id            text,
  user_email         text,
  product_id         text not null,
  status             text default 'active',
  stripe_session_id  text,
  razorpay_order_id  text,
  provider           text default 'stripe',
  amount_paise       integer,
  currency           text default 'USD',
  payment_id         text,
  created_at         timestamptz default now(),
  updated_at         timestamptz default now(),
  unique (user_id, product_id),
  unique (user_email, product_id)
);

-- ── Aurora Leads ──────────────────────────────────────────────────
create table if not exists aurora_leads (
  id            uuid default gen_random_uuid() primary key,
  name          text,
  phone         text,
  country_code  text default 'IN',
  company       text,
  pain_point    text,
  source        text default 'web',
  lead_score    int,
  tier          text,
  created_at    timestamptz default now()
);
create index if not exists aurora_leads_tier_idx on aurora_leads(tier);

-- ── Aurora Calls ──────────────────────────────────────────────────
create table if not exists aurora_calls (
  id                  uuid default gen_random_uuid() primary key,
  lead_id             uuid references aurora_leads(id),
  overall_score       int,
  pacing_score        int,
  silence_score       int,
  geo_region          text,
  call_duration_secs  int,
  vapi_call_id        text,
  critique            jsonb default '{}',
  created_at          timestamptz default now()
);

-- ── Janus Portfolio ───────────────────────────────────────────────
create table if not exists janus_portfolio (
  id          uuid default gen_random_uuid() primary key,
  symbol      text not null,
  quantity    float,
  entry_price float,
  regime      text,
  pnl_pct     float,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);

-- ── Syntropy Packs ───────────────────────────────────────────────
create table if not exists syntropy_packs (
  id           uuid default gen_random_uuid() primary key,
  user_id      text,
  title        text not null,
  content      text not null,
  exam_profile text,
  created_at   timestamptz default now()
);

-- ── MARS Lessons (Aurora legacy + Nexus) ─────────────────────────
create table if not exists mars_lessons (
  id               uuid default gen_random_uuid() primary key,
  pod              text not null,
  module           text,
  improvement      text,
  score_before     float,
  score_after      float,
  prompt_version   text,
  created_at       timestamptz default now()
);

-- ── Prompt Versions ──────────────────────────────────────────────
create table if not exists prompt_versions (
  id          uuid default gen_random_uuid() primary key,
  pod         text not null,
  version     text not null,
  prompt_text text not null,
  score       float,
  active      boolean default false,
  created_at  timestamptz default now()
);
create index if not exists prompt_versions_pod_active on prompt_versions(pod, active);

-- ── Row Level Security (enable for prod) ─────────────────────────
-- alter table nexus_subscriptions enable row level security;
-- create policy "users see own subs" on nexus_subscriptions for select using (auth.uid()::text = user_id);

-- ── Realtime (enable for event bus) ──────────────────────────────
-- In Supabase Dashboard: Database → Replication → toggle nexus_events, nexus_evolutions

-- ════════════════════════════════════════════════════════════════
-- Sprint 5 additions
-- ════════════════════════════════════════════════════════════════

-- ── Aurora RL Variant Stats ───────────────────────────────────────
create table if not exists nexus_variant_stats (
  id           uuid primary key default gen_random_uuid(),
  pod_name     text not null default 'aurora',
  element      text not null,
  variant_hash text not null,
  variant_text text,
  calls        integer default 0,
  wins         integer default 0,
  win_rate     float generated always as (
                 case when calls > 0 then wins::float / calls else 0 end
               ) stored,
  retired      boolean default false,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now(),
  unique (pod_name, element, variant_hash)
);
create index if not exists idx_variant_stats_pod
  on nexus_variant_stats(pod_name, element, retired);

-- ── Improvement Proofs Ledger ─────────────────────────────────────
create table if not exists nexus_improvement_proofs (
  id               uuid primary key default gen_random_uuid(),
  pod_name         text not null,
  cycle_id         text not null unique,
  avg_score_before float,
  avg_score_after  float,
  delta            float generated always as (avg_score_after - avg_score_before) stored,
  improved         boolean,
  genome_hash      text,
  proof_hash       text not null,
  n_calls          integer,
  created_at       timestamptz default now()
);
create index if not exists idx_proofs_pod
  on nexus_improvement_proofs(pod_name, improved, created_at desc);
