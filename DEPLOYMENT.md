# Deployment — Phase 2 (Supplier Risk, three-engine retrieval)

Frontend on Vercel, backend as a Docker container on an EC2 instance listening on **:8080**,
and Neo4j as a second container on the same box.

**Live app:** https://frontend-krushnasr96gmailcoms-projects.vercel.app
**Live API:** _<https://... >_ (`/health`, `/docs`)

---

## Why the backend can't be serverless

`torch` + `sentence-transformers` + `faiss-cpu` exceed the Vercel/Lambda 250 MB function limit, the
FAISS index and SQLite file live on local disk, and the compiled DSPy state is written to that same
volume. The backend needs a container host with persistent storage. Only the frontend goes on Vercel.

---

## 1. Backend — Docker on EC2

```bash
ssh -i ~/.ssh/<key>.pem ubuntu@<ec2-host>

sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/TechnicalShree/ns_phase_2.git && cd ns_phase_2
cp backend/.env.example backend/.env   # set OPENROUTER_API_KEY and NEO4J_*
docker compose up -d --build           # API listens on :8080
curl localhost:8080/health
```

First boot seeds all three stores automatically (`generate_sandbox.py` runs if `data/suppliers.db`
is missing) and prints row/vector/node counts in the logs. MiniLM is baked into the image, so
nothing is downloaded at runtime.

Security group: open **8080** (or keep it closed and use a Cloudflare tunnel — see §2).

### Add swap first on a 1 GB box

The API container needs ~1.3 GB RSS and Neo4j another ~700 MB. On a `t2.micro` both get
OOM-killed without swap:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Neo4j

- **Same box (default):** the `neo4j` service in `docker-compose.yml` starts with the stack, bolt
  and the browser bound to `127.0.0.1` only. Keep `NEO4J_URI=bolt://neo4j:7687` and make
  `NEO4J_PASSWORD` in `backend/.env` match the compose default (`supplierrisk`) or set it in a root
  `.env` too — compose reads that one for the container's `NEO4J_AUTH`.
- **Aura instead:** set `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io` and remove the `depends_on`
  block from the `api` service. Free Aura instances **pause when idle and are deleted after 30 days** —
  `Cannot resolve address …databases.neo4j.io` means the instance is gone.

Re-seed the graph after switching:

```bash
docker compose exec api python generate_sandbox.py
```

### Compile the DSPy module on the box

```bash
docker compose exec api python compile_dspy.py       # ~3-5 min, ~25 OpenRouter calls
curl -s localhost:8080/compile-report | head -40
```

The named volume `sandbox` is mounted at `/app/data`, so `suppliers.db`, `docs.faiss`,
`compiled_synthesizer.json` and `compile_report.json` survive restarts and rebuilds. Verify with
`docker compose restart` — `/health` should still report `dspy_compiled: true`.

---

## 2. HTTPS (required)

The Vercel frontend is served over HTTPS, so the browser **blocks a plain-HTTP backend as mixed
content**. `http://<ec2-ip>:8080` works from curl and from a local frontend, but never from the
deployed site. Pick one:

- **Cloudflare tunnel (used here):** point the tunnel at `http://localhost:8080`. TLS terminates at
  Cloudflare's edge and no inbound port needs to be open in the security group at all.
- **Domain + reverse proxy:** point an A record at the instance and put Caddy/nginx in front of
  `:8080`; requires inbound 80 (ACME) and 443.

---

## 3. Frontend — Vercel

```bash
cd frontend
npm install
vercel deploy --prod
```

`frontend/.env.production` holds `NEXT_PUBLIC_API_URL`; edit it and redeploy to repoint at a
different backend.

---

## Gotchas

**1. Don't mark `NEXT_PUBLIC_API_URL` as a Vercel "Sensitive" env var.** Vercel hides sensitive
values from the build, so the URL never reaches the bundle and the deployed app silently has no
backend. `NEXT_PUBLIC_*` is browser-exposed by definition — committing it in `.env.production` is
correct, not a leak.

**2. Never submit a bare `*.vercel.app` subdomain.** Those are shared across accounts. Use the
project's full production alias (`<project>-<org>.vercel.app`) or your own domain.

**3. `docker compose exec api python compile_dspy.py`, not `run`.** `run` starts a second container
with the same volume; `exec` reuses the running one and its already-warm model.

**4. A missing `OPENROUTER_API_KEY` fails at request time, not boot.** `/health` stays green while
`/assess` returns 502 `synthesis failed: RuntimeError: OPENROUTER_API_KEY is not set`. Check
`/health` **and** one `/assess` after deploying.

**5. Neo4j down is not an outage.** `/assess` still returns SQL + FAISS context with
`degraded: ["graph"]`. Don't read a `[GRAPH] ERROR` panel in the UI as a broken deployment — that
is the required degradation path.

---

## Operational notes

| Operation                       | Cost on a t2.micro (1 GB, 1 vCPU) |
| ------------------------------- | --------------------------------- |
| Image build                     | ~15-20 min                        |
| First boot to healthy (+seed)   | ~3 min                            |
| Parallel retrieval, one entity  | <0.1 s                            |
| One `/assess` end to end        | ~5-12 s (LLM-bound)               |
| `compile_dspy.py`               | ~3-5 min                          |

For code-only changes prefer `git pull && docker compose up -d --build api` — the pip layer is
cached unless `requirements.txt` changes.

```bash
docker compose logs -f --tail 50
curl -s localhost:8080/health
curl -s localhost:8080/judge-demo          # judge rejecting a bad output
```
