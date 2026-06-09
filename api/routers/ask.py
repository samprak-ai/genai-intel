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

_KEYWORD_SYSTEM = """You are a search query optimizer for a startup intelligence database.

Rules:
1. If the question names a specific company, return ONLY that company name (1-2 words max).
   Example: "Tell me about Suno" → "suno"
   Example: "What's new at Airspeed?" → "airspeed"
2. If the question is about a signal type (funding, hiring, product, acquisition, leadership),
   return 2-3 of the most specific terms for that type.
   Example: "Which startups raised recently?" → "funding raised"
   Example: "Which companies are hiring engineers?" → "hiring engineers"
3. Never return more than 3 words total.
4. Never include: AWS, AM, account manager, prioritize, should, who, which, largest, best,
   startups, companies, tell, about, what, me.

Return ONLY the keywords, space-separated, lowercase, no punctuation."""


class AskRequest(BaseModel):
    question: str


class Source(BaseModel):
    slug: str
    score: float | None = None
    title: str = ""


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


# Signal-type keywords that exist in the brain as chunk headings
_SIGNAL_TYPES = ("funding", "hiring", "product", "acquisition", "leadership", "raised", "series")


def _extract_keywords(question: str) -> str:
    """Rewrite a NL question into 1-3 keyword search terms.

    Strategy:
    1. If question contains a recognizable signal type AND is short, extract that signal type.
    2. Otherwise, use a Haiku call — but HARD-LIMIT output to 2 words in code.
    """
    q_lower = question.lower()

    # Fast path: if question is just about a signal type (no company name context)
    # pick the first signal keyword mentioned
    signal_hit = next((s for s in _SIGNAL_TYPES if s in q_lower), None)

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return signal_hit or question

    try:
        resp = Anthropic(api_key=key).messages.create(
            model="claude-haiku-4-5",
            max_tokens=20,  # tiny budget forces conciseness
            system=_KEYWORD_SYSTEM,
            messages=[{"role": "user", "content": question}],
        )
        kw = resp.content[0].text.strip().lower()
        # Hard-limit to 2 words regardless of what LLM returned
        words = [w for w in kw.split() if len(w) > 2][:2]
        if words:
            return " ".join(words)
    except Exception:
        pass

    return signal_hit or question


def _gbrain_query(question: str, limit: int = 8) -> str:
    """Convert NL question to keywords then query gbrain hybrid search."""
    env = dict(os.environ)
    env["PATH"] = _BUN + os.pathsep + env.get("PATH", "")

    search_terms = _extract_keywords(question)

    try:
        r = subprocess.run(
            [_GBRAIN, "query", search_terms, "--no-expand", "--limit", str(limit)],
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

    q_nl = "Which startups raised the largest funding rounds, and who should an AWS AM prioritize?"
    q_co = "Tell me about Suno"
    kw_nl = _extract_keywords(q_nl)
    kw_co = _extract_keywords(q_co)
    return {
        "has_db": bool(os.getenv("GBRAIN_DATABASE_URL")),
        "has_openai": bool(os.getenv("OPENAI_API_KEY")),
        "has_ze": bool(os.getenv("ZEROENTROPY_API_KEY")),
        "gbrain_path": _GBRAIN,
        "query_hardcoded_suno_funding": run(["query", "suno funding", "--no-expand", "--limit", "3"]),
        "keywords_for_nl_q": kw_nl,
        "query_nl_via_extracted": run(["query", kw_nl, "--no-expand", "--limit", "3"]),
        "keywords_for_suno_q": kw_co,
        "query_suno_via_extracted": run(["query", kw_co, "--no-expand", "--limit", "3"]),
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
