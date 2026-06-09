"""
/api/ask — natural-language Q&A over the GenAI-Intel knowledge graph (GBrain).

Retrieves from the brain (hybrid vector + keyword + graph traversal via the `gbrain`
CLI), then synthesizes a cited answer with Claude, grounded ONLY in what was retrieved.

Prototype note: this shells out to the local `gbrain` CLI, so it works when the FastAPI
backend runs on the machine that holds the brain (local dev, or a host with gbrain + the
brain). For deployed use the brain must be hosted (gbrain --supabase) and reachable here.
Requires env: ANTHROPIC_API_KEY, OPENAI_API_KEY (gbrain embeddings), and gbrain on PATH.
"""

import os
import re
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from anthropic import Anthropic

router = APIRouter(prefix="/api", tags=["ask"])

_BUN = os.path.expanduser("~/.bun/bin")
# Prefer the absolute path (local dev); fall back to PATH (Railway/Docker installs to /root/.bun/bin).
_GBRAIN = os.path.join(_BUN, "gbrain") if os.path.exists(os.path.join(_BUN, "gbrain")) else "gbrain"
_MODEL = os.getenv("ASK_MODEL", "claude-sonnet-4-6")
# When GBRAIN_DATABASE_URL is set (Railway), gbrain queries the HOSTED brain in Supabase
# instead of a local PGLite store — inherited automatically via os.environ in _gbrain_query().

_SYNTH = """You answer questions for an AWS Startups sales team using ONLY the retrieved
knowledge-graph context (company + signal pages from the GenAI-Intel brain). Cite the page
slugs you use in [brackets]. If the retrieved context does not contain the answer, say so
plainly rather than guessing. Be concise, specific, and oriented toward what an account
manager should DO or KNOW."""


class AskRequest(BaseModel):
    question: str


class Source(BaseModel):
    slug: str
    score: float | None = None
    title: str = ""


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


def _gbrain_query(question: str, limit: int = 8) -> str:
    env = dict(os.environ)
    env["PATH"] = _BUN + os.pathsep + env.get("PATH", "")
    try:
        # No --no-expand: allow gbrain query expansion for better NL recall.
        # --no-expand only helps with exact-slug lookups; NL questions need expansion.
        r = subprocess.run(
            [_GBRAIN, "query", question, "--limit", str(limit)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        return r.stdout.strip()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"knowledge graph unavailable: {e}")


def _parse_sources(raw: str) -> list[Source]:
    out: list[Source] = []
    for m in re.finditer(r"^\[([\d.]+)\]\s+(\S+)\s+--\s+(.*)$", raw, re.M):
        out.append(Source(slug=m.group(2), score=float(m.group(1)), title=m.group(3)[:100]))
    return out[:8]


@router.get("/ask/_debug")
def ask_debug():
    """TEMPORARY: surface gbrain's runtime state (doctor + a query with stderr/exit)
    so we can diagnose why retrieval is empty on the deployed container."""
    env = dict(os.environ)
    env["PATH"] = _BUN + os.pathsep + env.get("PATH", "")

    import time

    def run(args, timeout=120):
        t0 = time.time()
        try:
            r = subprocess.run([_GBRAIN, *args], capture_output=True, text=True, env=env, timeout=timeout)
            return {"rc": r.returncode, "secs": round(time.time() - t0, 1),
                    "stdout_len": len(r.stdout or ""), "stdout": (r.stdout or "")[:500],
                    "stderr": (r.stderr or "")[:500]}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "secs": round(time.time() - t0, 1)}

    q = "Which startups raised the largest funding rounds, and who should an AWS AM prioritize?"
    return {
        "has_db": bool(os.getenv("GBRAIN_DATABASE_URL")),
        "has_openai": bool(os.getenv("OPENAI_API_KEY")),
        "has_ze": bool(os.getenv("ZEROENTROPY_API_KEY")),
        "gbrain_path": _GBRAIN,
        "list_pages": run(["list", "--limit", "5"]),
        "query_simple_suno": run(["query", "suno funding", "--limit", "3"]),
        "query_keyword_funding": run(["query", "funding raised series", "--limit", "3"]),
        "query_keyword_hiring": run(["query", "hiring engineers jobs", "--limit", "3"]),
        "query_nl_no_expand": run(["query", q, "--no-expand", "--limit", "3"]),
        "query_nl_with_expand": run(["query", q, "--limit", "3"]),
    }


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question required")

    raw = _gbrain_query(q)
    if not raw:
        return AskResponse(
            answer="No relevant information found in the knowledge graph for that question.",
            sources=[],
        )

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    resp = Anthropic(api_key=key).messages.create(
        model=_MODEL,
        max_tokens=700,
        system=_SYNTH,
        messages=[{"role": "user", "content": (
            f"QUESTION: {q}\n\nRETRIEVED KNOWLEDGE-GRAPH CONTEXT:\n{raw}\n\n"
            f"Answer using only this context; cite the page slugs you use."
        )}],
    )
    return AskResponse(answer=resp.content[0].text.strip(), sources=_parse_sources(raw))
