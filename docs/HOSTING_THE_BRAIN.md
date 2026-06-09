# Hosting the Knowledge Graph (Path B) — runbook

Goal: make the `/api/ask` knowledge-graph Q&A work on the **deployed** site
(`cloud-intel.vercel.app` → Railway backend), not just local dev.

Today the brain is local PGLite on a laptop; the Railway backend can't reach it.
This moves the brain into the **genai-intel Supabase** Postgres and gives the
Railway backend the `gbrain` CLI so it can query the hosted brain.

## Status
- [x] `pgvector` enabled on genai-intel Supabase (v0.8.0)
- [x] `api/routers/ask.py` resolves gbrain on PATH and inherits `GBRAIN_DATABASE_URL`
- [x] `Dockerfile` added (Python + Bun + gbrain) for the Railway build
- [ ] Migrate brain → genai-intel Postgres  ← **needs the DB connection string**
- [ ] Deploy backend to Railway + set env vars
- [ ] Verify deployed `/api/ask`

## Step 1 — Migrate the brain into genai-intel Postgres
Get the connection string from Supabase → Project Settings → Database →
"Connection string" (URI). Use the **Session pooler** (port 5432). Then:

```bash
export PATH="$HOME/.bun/bin:$PATH"
export OPENAI_API_KEY=sk-...                       # embeddings
export GBRAIN_DATABASE_URL="postgresql://postgres.lzbpqavhrqbjryzimvnf:<DB-PASSWORD>@<host>:5432/postgres"
gbrain migrate --to supabase                       # moves local PGLite brain -> Supabase
gbrain doctor --fast                               # expect: connection OK, pgvector OK
gbrain list --type company -n 5                    # sanity: company pages present
```

## Step 2 — Deploy the backend (Railway picks up the Dockerfile automatically)
Set these Railway environment variables on the backend service:

```
GBRAIN_DATABASE_URL = <same connection string as above>
OPENAI_API_KEY      = sk-...
# ANTHROPIC_API_KEY already set
```

Then deploy (push to the connected branch, or `railway up`). The Docker build
installs Bun + gbrain; `ask.py` inherits `GBRAIN_DATABASE_URL` so `gbrain query`
runs against the hosted brain.

## Step 3 — Verify
```bash
curl -s -X POST https://<railway-backend-url>/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which startups raised the largest rounds recently?"}'
```
Then open `cloud-intel.vercel.app/ask` and ask a question.

## Notes
- Keep the brain fresh: the proactive-intel pipeline writes signals via the gbrain
  CLI; point IT at `GBRAIN_DATABASE_URL` too (instead of local PGLite) so new
  signals land in the hosted brain.
- The first Railway build is slower (installs Bun + gbrain). If `bun install -g`
  trips the blocked-postinstall issue, gbrain still installs; brain schema lives
  in Supabase already from Step 1.
