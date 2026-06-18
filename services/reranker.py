"""Local cross-encoder reranker for retrieval results.

A bi-encoder (the embedding model) measures semantic nearness by comparing two
independently-computed vectors. A cross-encoder instead feeds the (query, chunk)
pair through the model *together*, so it can attend across both texts and judge
true relevance far more accurately. We run it as a second-stage reranker: pull a
wider candidate pool from BM25 / vector / hybrid search, then reorder by
cross-encoder score and keep the top_k.

Why this lives next to the embedder rather than behind the LLM reranker in
`ai_service`:

  - It is **local** (sentence-transformers, same dependency as the embedder),
    so it costs no API tokens and adds no latency from a network round-trip.
  - Nothing leaves the machine — no data is sent to an external model.
  - It runs for **every** caller, including the MCP `search_collection` tool,
    which never passes an `ai_service` and therefore never triggered the
    LLM-judgment reranker.

The model is loaded lazily on first use and the whole thing degrades silently:
if `sentence-transformers` is missing, the model can't be downloaded, or scoring
throws, callers get their input order back (capped to top_k) instead of an
error.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)


def _default_text_getter(item: Any) -> str:
    """Pull the rerankable text off a SearchResult-like object or dict."""
    if isinstance(item, dict):
        return item.get("text_snippet") or item.get("text") or item.get("excerpt") or ""
    return (
        getattr(item, "text_snippet", None)
        or getattr(item, "text", None)
        or getattr(item, "excerpt", None)
        or ""
    )


class CrossEncoderReranker:
    """Lazily-loaded cross-encoder reranker with graceful degradation."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True if the model is loaded or can plausibly still be loaded."""
        return not self._load_failed

    def _ensure_model(self):
        """Load the cross-encoder on first use. Thread-safe; loads at most once."""
        if self._model is not None or self._load_failed:
            return self._model
        with self._lock:
            if self._model is not None or self._load_failed:
                return self._model
            try:
                from sentence_transformers import CrossEncoder

                logger.info("Loading cross-encoder reranker: %s", self.model_name)
                self._model = CrossEncoder(self.model_name)
                logger.info("Cross-encoder reranker ready: %s", self.model_name)
            except Exception as e:  # noqa: BLE001 - any failure must degrade, not crash
                self._load_failed = True
                logger.warning(
                    "Cross-encoder reranker unavailable (%s); falling back to "
                    "retrieval order. %s",
                    self.model_name,
                    e,
                )
        return self._model

    def rerank(
        self,
        query: str,
        items: Sequence[Any],
        top_k: int,
        text_getter: Optional[Callable[[Any], str]] = None,
    ) -> List[Any]:
        """Reorder `items` by cross-encoder relevance to `query`, keep top_k.

        Returns the input list (capped to top_k, original order) unchanged if
        the model is unavailable or anything goes wrong — never raises.
        """
        items = list(items)
        if not items or top_k <= 0:
            return items[: max(0, top_k)]
        if len(items) == 1:
            return items[:top_k]

        model = self._ensure_model()
        if model is None:
            return items[:top_k]

        getter = text_getter or _default_text_getter
        try:
            pairs = [[query, getter(it) or ""] for it in items]
            scores = model.predict(pairs)
            order = sorted(
                range(len(items)), key=lambda i: float(scores[i]), reverse=True
            )
            ranked = [items[i] for i in order[:top_k]]
            logger.info(
                "Cross-encoder reranked %d candidates -> top %d",
                len(items),
                len(ranked),
            )
            return ranked
        except Exception as e:  # noqa: BLE001
            logger.warning("Cross-encoder rerank failed, using retrieval order: %s", e)
            return items[:top_k]


_reranker: Optional[CrossEncoderReranker] = None
_reranker_lock = threading.Lock()


def get_reranker() -> Optional[CrossEncoderReranker]:
    """Return the shared reranker if enabled in settings, else None.

    The instance is created once and reused. Returns None when reranking is
    disabled so callers can cheaply skip the wider candidate fetch.
    """
    from config import settings

    if not getattr(settings, "enable_reranker", False):
        return None

    global _reranker
    if _reranker is None or _reranker.model_name != settings.reranker_model:
        with _reranker_lock:
            if _reranker is None or _reranker.model_name != settings.reranker_model:
                _reranker = CrossEncoderReranker(settings.reranker_model)
    return _reranker
