# Supplier Risk Dashboard — Three-Engine Parallel Retrieval (Phase 2)

**Domain chosen (Problem Statement option 7): Supplier Risk Dashboard.**
SQL = delivery/defect metrics · FAISS = contract/complaint/audit text · Neo4j = subsidiary/ownership
and sub-tier dependency graph · Verdict = **APPROVE / FLAG**.

**Live app:** https://frontend-krushnasr96gmailcoms-projects.vercel.app
**Live API:** _<https://... — fill in after deploy>_ (`/health`, `/docs`)

Three retrieval engines are queried **simultaneously** for the same supplier, and a **DSPy
`ChainOfThought` module compiled with `BootstrapFewShot`** (teacher/student model split over
OpenRouter) synthesizes one structured verdict from all three raw contexts. The UI shows the three
contexts side by side, so the retrieval is visible, not hidden behind the answer.

---

## Architecture

```
                      ┌──────────────────────────────────────┐
   supplier name ───▶ │  retrieve_all_timed()                │
                      │  ThreadPoolExecutor(max_workers=3)   │
                      └───┬───────────┬───────────┬──────────┘
                          │           │           │        (fired at the same time)
              ┌───────────▼──┐  ┌─────▼────────┐  ┌▼─────────────────┐
              │ query_sql()  │  │ query_faiss()│  │ query_graph()    │
              │ SQLite       │  │ FAISS +BM25  │  │ Neo4j (Cypher)   │
              │ metrics,     │  │ RRF fusion,  │  │ ownership, sites,│
              │ spend, tier  │  │ exact-match  │  │ buyers, sub-tier │
              │              │  │ + neighbours │  │ dependencies     │
              └───────┬──────┘  └──────┬───────┘  └────────┬─────────┘
            "[SQL] …"  │   "[FAISS] …" │      "[GRAPH] …"   │
                       └───────────────┴────────────────────┘
                                       │  (any engine may return a tagged ERROR string;
                                       │   the others still answer — no crash)
                        ┌──────────────▼──────────────────┐
                        │ dspy.ChainOfThought(            │
                        │   SupplierRiskVerdict)          │
                        │ compiled_synthesizer.json       │
                        │ student: gpt-4o-mini            │
                        │ teacher: claude-sonnet-4.5      │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │ judge_failures() — rule metric  │
                        │ structure · ranges · banned     │
                        │ words · FLAG/score consistency  │
                        └──────────────┬──────────────────┘
                                       ▼
                    { verdict, risk_score, key_risks[], rationale }
```

## Tech stack

| Layer            | Choice                                                            |
| ---------------- | ----------------------------------------------------------------- |
| Structured store | SQLite (`suppliers`, `deliveries`)                                |
| Semantic store   | FAISS `IndexFlatIP` + `rank_bm25`, fused with Reciprocal Rank Fusion |
| Embeddings       | `sentence-transformers/all-MiniLM-L6-v2` (local, baked into image) |
| Graph store      | Neo4j 5 (Aura or Docker)                                          |
| Synthesis        | DSPy `ChainOfThought` + `BootstrapFewShot`, LLMs via OpenRouter    |
| API              | FastAPI + uvicorn on **:8080**                                    |
| UI               | Next.js 15 (App Router), deployed on Vercel                       |

## What lives where (and why the three engines don't duplicate each other)

| Store  | Only it knows                                                                 |
| ------ | ----------------------------------------------------------------------------- |
| SQLite | on-time rate, defect ppm, lead time, credit notes, spend, tier, month-by-month |
| FAISS  | contract SLA/termination clauses, complaint narratives, audit findings         |
| Neo4j  | parent company + jurisdiction, co-located suppliers, buyer exposure, sub-tier dependencies and whether any of them is watchlisted |

A supplier can look fine in SQL and still be flagged because the graph shows its own upstream
dependency is watchlisted — that is the point of querying all three.

---

## Setup (≈10 minutes)

### 1. Neo4j

Either **Aura free** (recommended, nothing to run locally) — create an instance at
<https://console.neo4j.io> and copy the URI/password into `backend/.env` — or local Docker:

```bash
docker compose --profile local-graph up -d neo4j     # bolt://localhost:7687, user neo4j / supplierrisk
```

### 2. Backend

```bash
cp backend/.env.example backend/.env     # set OPENROUTER_API_KEY + NEO4J_*
docker compose up -d --build             # API on :8080; seeds the sandbox on first boot
curl localhost:8080/health
```

First build takes a few minutes (torch + MiniLM are baked into the image). `/health` reports each
store independently.

Local (no Docker) alternative:

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python generate_sandbox.py               # populates SQLite + FAISS + Neo4j, prints counts
python test_smoke.py                     # engines, degradation, parallelism, judge
uvicorn app.main:app --port 8080
```

### 3. Compile the DSPy module (required for the "compiled" badge)

```bash
docker compose exec api python compile_dspy.py       # ~3-5 min, ~25 OpenRouter calls
```

Writes `data/compiled_synthesizer.json` (the optimized state, loaded automatically on the next
request) and `data/compile_report.json` (uncompiled vs compiled judge pass rate, plus the held-out
supplier's before/after output). Served at `GET /compile-report`.

### 4. Frontend

```bash
cd frontend && npm install
cp .env.local.example .env.local         # point at http://localhost:8080
npm run dev                              # http://localhost:3000
```

Deploy: `vercel deploy --prod` (the production API URL is committed in `.env.production`).

---

## API

| Endpoint          | Purpose                                                                    |
| ----------------- | -------------------------------------------------------------------------- |
| `GET /health`     | per-store status, whether the DSPy state is compiled, model ids            |
| `GET /entities`   | the 26 suppliers + which one is held out of DSPy training                  |
| `POST /assess`    | parallel retrieval → three raw contexts, verdict, judge result, per-engine timings |
| `GET /compile-report` | before/after evidence from the last compile run                        |
| `GET /judge-demo` | one output the judge rejects and one it accepts, with the exact reasons    |

```bash
curl -s localhost:8080/assess -H 'content-type: application/json' \
  -d '{"entity":"Aureus Polymers"}' | python -m json.tool
```

## Evidence for the phase checklist

- **Three genuinely different contexts** — see the table above; `test_smoke.py::test_engines` asserts all three differ.
- **Graceful degradation** — stop Neo4j (`docker compose stop neo4j`, or break `NEO4J_URI`) and re-run `/assess`: SQL and FAISS still answer, the graph panel shows `[GRAPH] ERROR: …`, and `degraded: ["graph"]` appears in the response. No 500.
- **Real parallelism** — every `/assess` response carries `timings.parallel_total` vs `timings.sum_of_engines`; the total tracks the slowest engine, not the sum.
- **Really compiled** — `data/compiled_synthesizer.json` exists on disk and `/compile-report` shows the bootstrapped demo count and the uncompiled→compiled judge pass rate on the same dev set, plus the held-out supplier's output both ways.
- **Judge really rejects** — `GET /judge-demo` returns a deliberately broken output (`verdict: MAYBE`, `risk_score: 140`, banned phrase) with the exact failure list; `test_smoke.py::test_judge_rejects_bad_output` asserts it.

## Judge metric rules

Structure and ranges: `verdict ∈ {APPROVE, FLAG}`, `risk_score` an integer in 0–100, ≥2 key risks,
rationale ≥25 words. Hard content rules: no banned phrases (`as an ai`, `i cannot`, `n/a`, …), at
least one concrete figure cited from the retrieved contexts, and verdict/score consistency —
`FLAG` requires score ≥50, `APPROVE` requires score <70.

## Out of scope (Phase 3/4)

No agent orchestration, tool-calling loop, or supervisor — synthesis is a single compiled module
call. No caching, fallback cascades, or production observability.
