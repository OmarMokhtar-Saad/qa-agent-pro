"""tools/embeddings.py — optional semantic text embeddings (never-raise).

Selected by ``QA_EMBEDDINGS_BACKEND`` (default ``""`` = disabled):
  ""       : disabled. ``embed_texts`` returns an error dict WITHOUT importing
             any optional dependency — the zero-cost default path.
  "local"  : sentence-transformers (optional extra: ``pip install -e ".[embeddings]"``).
             Imported lazily; the model is loaded once and cached at module level.
             A missing dependency degrades gracefully with a clear log, never raises.
  "voyage" : Voyage AI embeddings over httpx (``VOYAGE_API_KEY``). No new hard
             dependency — httpx is already required.

Contract (mirrors the rest of tools/): every public coroutine returns
  {"error": None, "content": [[float, ...], ...]}  on success
  {"error": <str>, "content": None}                on failure
and NEVER raises. Embeddings do NOT go through llm.ask (they are not chat).
"""

from __future__ import annotations

import logging
import math
import os
import threading

from config.settings import settings

logger = logging.getLogger(__name__)

# Voyage HTTP endpoint + a bounded network timeout (seconds). Kept as module
# constants so the disabled path adds no settings field.
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_TIMEOUT_S = 30.0

_DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_VOYAGE_MODEL = "voyage-3"

# Cached sentence-transformers model (local backend), loaded at most once. The
# lock serializes concurrent first calls so the model is never double-loaded.
_LOCAL_MODEL = None
_LOCAL_MODEL_LOCK = threading.Lock()
_LOCAL_MODEL_FAILED = False


def _backend() -> str:
    return str(getattr(settings, "qa_embeddings_backend", "") or "").strip().lower()


def backend_enabled() -> bool:
    """True when a supported embeddings backend is configured. Cheap; no imports."""
    return _backend() in ("local", "voyage")


def warm_local_model_background() -> None:
    """Load the local sentence-transformers model on a background thread.

    Fire-and-forget: the FIRST real ``embed_texts`` call used to pay the whole
    model-load cost (about 8s of a tester's first ``qa_prepare_test_cases``
    call, measured 2026-09-03). Warming it off the serving path, at server
    start, moves that cost to a moment nothing is waiting on it.

    No flag: this is strictly an improvement over the lazy-load default, never
    a behaviour change -- the model is still loaded at most once (the existing
    lock in :func:`_load_local_model` makes the warm thread and a real request
    that races it converge on the same load). A no-op when the backend is not
    ``local``, and every failure (missing extra, disk, import error) is caught
    and logged at DEBUG so an absent optional dependency never blocks or
    crashes startup.
    """
    if _backend() != "local":
        return

    def _warm() -> None:
        try:
            _load_local_model()
        except Exception:
            logger.debug("embeddings: background warm-up skipped", exc_info=True)

    try:
        threading.Thread(target=_warm, daemon=True, name="qa-embeddings-warm").start()
    except Exception:
        logger.debug("embeddings: could not start the warm-up thread", exc_info=True)


def _model_name(default: str) -> str:
    name = str(getattr(settings, "qa_embeddings_model", "") or "").strip()
    return name or default


def cosine_similarity(a, b) -> float:
    """Cosine similarity of two equal-length float vectors, pure stdlib. Returns
    0.0 for empty / mismatched-length / zero-norm inputs. Never raises."""
    try:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        return dot / (math.sqrt(na) * math.sqrt(nb))
    except Exception:
        return 0.0


def _load_local_model():
    """Import + construct the sentence-transformers model once, cached. Returns
    the model, or None when the optional dependency is missing / load fails.

    Called only from a worker thread (see _embed_local), so the potentially slow
    first-run import + model download never touches the event loop. The
    module-level lock makes concurrent first calls load exactly once."""
    global _LOCAL_MODEL, _LOCAL_MODEL_FAILED
    if _LOCAL_MODEL is not None:
        return _LOCAL_MODEL
    with _LOCAL_MODEL_LOCK:
        if _LOCAL_MODEL is not None:
            return _LOCAL_MODEL
        if _LOCAL_MODEL_FAILED:
            return None
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            _LOCAL_MODEL_FAILED = True
            logger.warning(
                "QA_EMBEDDINGS_BACKEND=local but sentence-transformers is not "
                "installed — install the optional extra: pip install -e "
                '".[embeddings]". Semantic features are disabled for now.'
            )
            return None
        try:
            _LOCAL_MODEL = SentenceTransformer(_model_name(_DEFAULT_LOCAL_MODEL))
        except Exception:
            _LOCAL_MODEL_FAILED = True
            logger.exception("Failed to load the local embeddings model")
            return None
        return _LOCAL_MODEL


def _embed_local_sync(texts: list[str]):
    """Load (once, cached) + encode on a worker thread. Returns the vectors, or
    None when the optional dependency / model is unavailable so the caller
    degrades cleanly."""
    model = _load_local_model()
    if model is None:
        return None
    vectors = model.encode(list(texts), convert_to_numpy=False)
    return [[float(x) for x in v] for v in vectors]


async def _embed_local(texts: list[str]) -> dict:
    import asyncio

    # Offload BOTH the (potentially slow / first-run downloading) model load AND
    # the encode to a worker thread — never block the event loop.
    vectors = await asyncio.to_thread(_embed_local_sync, texts)
    if vectors is None:
        return {"error": "local embeddings backend unavailable", "content": None}
    return {"error": None, "content": vectors}


async def _embed_voyage(texts: list[str]) -> dict:
    api_key = str(
        getattr(settings, "voyage_api_key", "") or ""
    ).strip() or os.environ.get("VOYAGE_API_KEY", "")
    if not api_key:
        return {"error": "VOYAGE_API_KEY is not set", "content": None}
    import httpx

    payload = {"input": list(texts), "model": _model_name(_DEFAULT_VOYAGE_MODEL)}
    async with httpx.AsyncClient(timeout=_VOYAGE_TIMEOUT_S) as client:
        resp = await client.post(
            _VOYAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if resp.status_code != 200:
        return {"error": f"voyage http {resp.status_code}", "content": None}
    data = resp.json()
    rows = data.get("data") or []
    vectors = [[float(x) for x in row.get("embedding", [])] for row in rows]
    if len(vectors) != len(texts):
        return {"error": "voyage returned a mismatched vector count", "content": None}
    return {"error": None, "content": vectors}


async def embed_texts(texts: list[str]) -> dict:
    """Embed a list of texts with the configured backend. Never raises.

    On the disabled default path this returns an error dict immediately without
    importing any optional dependency."""
    try:
        if not texts:
            return {"error": None, "content": []}
        backend = _backend()
        if backend == "local":
            return await _embed_local(texts)
        if backend == "voyage":
            return await _embed_voyage(texts)
        if backend:
            logger.warning(
                "Unknown QA_EMBEDDINGS_BACKEND=%r — expected 'local' or 'voyage'; "
                "embeddings disabled.",
                backend,
            )
        return {"error": "embeddings backend disabled", "content": None}
    except Exception as exc:
        logger.exception("embed_texts failed")
        return {"error": str(exc), "content": None}
