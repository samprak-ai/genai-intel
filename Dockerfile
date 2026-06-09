# Railway build for the GenAI-Intel FastAPI backend, now including Bun + gbrain so the
# /api/ask endpoint can query the HOSTED knowledge graph (gbrain --supabase brain).
#
# Required Railway env vars (in addition to the existing ones):
#   GBRAIN_DATABASE_URL   postgres connection string for the genai-intel Supabase brain
#   OPENAI_API_KEY        gbrain query embeddings
#   ANTHROPIC_API_KEY     answer synthesis (already set)
FROM python:3.12-slim

# system deps: curl (bun installer), git (bun install -g from GitHub), ca-certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git unzip \
    && rm -rf /var/lib/apt/lists/*

# install Bun + gbrain CLI into /root/.bun (matches ask.py's ~/.bun/bin resolution under root)
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"
RUN bun install -g github:garrytan/gbrain && gbrain --version

WORKDIR /app

# Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects PORT; read it in Python so no shell expansion is required
# (Railway runs the start command in exec form, which would pass "$PORT" literally).
CMD ["python", "-c", "import os, uvicorn; uvicorn.run('api.main:app', host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))"]
