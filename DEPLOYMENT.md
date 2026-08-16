# Deployment

How this project is deployed, and how to reproduce it. Frontend on Vercel, backend as a Docker
container on a VM behind HTTPS.

**Live app:** https://scheme-navigator-krushnasr96gmailcoms-projects.vercel.app
**Live API:** https://scheme-api.technicalshree.in (`/health`, `/docs`)

---

## Why the backend can't be serverless

`torch` + `sentence-transformers` + `faiss-cpu` exceed the Vercel/Lambda 250 MB function limit, and
the FAISS index lives on local disk. The backend needs a container host with persistent storage —
a VM, Render, Railway or Fly.io. Only the frontend goes on Vercel.

---

## 1. Backend — Docker on a VM

```bash
git clone https://github.com/TechnicalShree/ns_phase_1.git
cd ns_phase_1
cp backend/.env.example backend/.env      # set OPENROUTER_API_KEY
sudo docker compose up -d --build         # API listens on :8000
curl localhost:8000/health
```

First boot takes a few minutes — torch and both MiniLM models load into memory before the health
check passes. The models are baked into the image at build time, so there is no download at runtime.

### Add swap first on a 1 GB box

The container needs ~1.3 GB RSS. On a `t2.micro` it will be OOM-killed without swap:

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Index the documents once

```bash
curl -F file=@sample_docs/snap_7cfr273_eligibility.pdf "localhost:8000/upload?reset=true"
curl -F file=@sample_docs/snap_7cfr271_general.pdf     "localhost:8000/upload?reset=false"
```

A named volume is mounted at `/app/data`, so the FAISS index and `eval_log.jsonl` survive restarts
and rebuilds. Verify with `docker compose restart` — `/health` should still list both sources.

---

## 2. HTTPS (required)

The Vercel frontend is served over HTTPS, so the browser **blocks a plain-HTTP backend as mixed
content**. `http://<ip>:8000` works from curl and from a local frontend, but never from the
deployed site. Pick one:

### Option A — Cloudflare tunnel (used by this deployment)

Point the tunnel at `http://localhost:8000`. TLS terminates at Cloudflare's edge. No inbound ports
need to be open on the security group at all, since the tunnel connects outbound. Caddy is not used.

### Option B — Domain + Caddy

```bash
echo "API_DOMAIN=api.example.com" >> .env    # A record must point at this host
sudo docker compose --profile tls up -d      # Caddy fetches a Let's Encrypt cert automatically
```

Requires inbound **80** (ACME challenge) and **443** open in the firewall/security group.

---

## 3. Frontend — Vercel

```bash
cd frontend
npm install
vercel deploy --prod
```

The API URL is committed in `frontend/.env.production`, so no dashboard configuration is needed.
To repoint at a different backend, edit that file and redeploy.

---

## Gotchas

These all cost real debugging time. They are listed because each one fails in a way that does not
obviously point at its cause.

**1. `--loop asyncio` is mandatory.**
`uvicorn[standard]` installs uvloop. RAGAS imports `nest_asyncio`, which cannot patch a uvloop loop
and kills the process at startup:

```
ValueError: Can't patch loop of type <class 'uvloop.Loop'>
```

The flag is baked into the Dockerfile CMD and the README's local command — don't remove it. Note
that `TestClient` does not use uvloop, so this bug is invisible to tests and only appears under a
real server.

**2. Don't mark `NEXT_PUBLIC_API_URL` as a Vercel "Sensitive" env var.**
Vercel hides sensitive values from the build, so the URL never reaches the bundle and the deployed
app silently has no backend. `NEXT_PUBLIC_*` is browser-exposed by definition, so committing it in
`.env.production` is correct, not a leak.

**3. `API_DOMAIN` must not use `${VAR:?}`.**
Compose interpolates variables even for services in a disabled profile, so `:?` on the Caddy
service breaks a plain `docker compose up` for anyone not using TLS. It defaults to empty instead.

**4. Never submit a bare `*.vercel.app` subdomain.**
Those are shared across all Vercel accounts. `scheme-navigator.vercel.app` resolved to this project
briefly and now serves an unrelated app. Use the project's full production alias
(`<project>-<org>.vercel.app`) or a custom domain you control.

---

## Operational notes

| Operation | Cost on a t2.micro (1 GB, 1 vCPU) |
|---|---|
| Image build | ~20 min |
| First boot to healthy | ~2–3 min |
| Indexing a 204-page PDF | ~2m15s |
| One query end to end | ~16 s |

On a 2 GB / 2 vCPU box these drop several-fold. For code-only changes prefer `git pull &&
docker compose up -d` over a rebuild — the image only needs rebuilding when `requirements.txt` or
the `Dockerfile` changes.

Logs and health:

```bash
sudo docker compose logs -f --tail 50
curl -s localhost:8000/health
curl -s "localhost:8000/logs?limit=5"      # recent RAGAS-scored queries
```
