from __future__ import annotations

import time
from collections.abc import Iterator

from google.genai import Client
from google.genai import errors as genai_errors

from app.config import settings
from app.models import ChatMessage

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    if _client is None:
        _client = Client(api_key=settings.gemini_api_key)
    return _client


def _is_rate_limit(err: BaseException) -> bool:
    if isinstance(err, genai_errors.APIError):
        code = getattr(err, "code", None)
        status = (getattr(err, "status", None) or "").upper()
        return code == 429 or status == "RESOURCE_EXHAUSTED"
    s = str(err).lower()
    return "429" in s or "resource exhausted" in s


def _embed_content_chunk(client: Client, chunk: list[str]) -> list[list[float]]:
    model = settings.gemini_embedding_model
    delay = settings.embedding_initial_backoff_sec

    for _ in range(settings.embedding_max_retries):
        try:
            res = client.models.embed_content(
                model=model,
                contents=chunk,
                config={"task_type": "RETRIEVAL_DOCUMENT"},
            )
            embs = res.embeddings
            if not embs or len(embs) != len(chunk):
                raise RuntimeError("Embedding response missing or wrong count")
            out: list[list[float]] = []
            for emb in embs:
                vals = emb.values if emb else None
                if not vals:
                    raise RuntimeError("Embedding response missing 'values'")
                out.append(vals)
            return out
        except genai_errors.APIError as e:
            if not _is_rate_limit(e):
                raise
            time.sleep(min(delay, settings.embedding_max_backoff_sec))
            delay = min(delay * 2, settings.embedding_max_backoff_sec)

    if len(chunk) <= 1:
        raise RuntimeError(
            "Gemini embedding rate limit persists after retries. "
            "Try again later or tune EMBEDDING_* settings."
        )

    mid = max(1, len(chunk) // 2)
    return _embed_content_chunk(client, chunk[:mid]) + _embed_content_chunk(client, chunk[mid:])


def iter_embed_document_vectors(texts: list[str]) -> Iterator[list[float]]:
    if not texts:
        return
    client = _get_client()
    bs = max(1, settings.embedding_batch_size)
    for i in range(0, len(texts), bs):
        chunk = texts[i : i + bs]
        for vec in _embed_content_chunk(client, chunk):
            yield vec
        if settings.embedding_batch_delay_sec > 0 and i + bs < len(texts):
            time.sleep(settings.embedding_batch_delay_sec)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return list(iter_embed_document_vectors(texts))


def embed_query(text: str) -> list[float]:
    client = _get_client()
    delay = settings.embedding_initial_backoff_sec
    for _ in range(settings.embedding_max_retries):
        try:
            res = client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=text,
                config={"task_type": "RETRIEVAL_QUERY"},
            )
            embs = res.embeddings
            if not embs or not embs[0].values:
                raise RuntimeError("Embedding response missing 'values'")
            return embs[0].values
        except genai_errors.APIError as e:
            if not _is_rate_limit(e):
                raise
            time.sleep(min(delay, settings.embedding_max_backoff_sec))
            delay = min(delay * 2, settings.embedding_max_backoff_sec)
    raise RuntimeError("Gemini query embedding failed after retries (rate limited).")


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _normalize_history(history: list[ChatMessage]) -> list[ChatMessage]:
    h = list(history)
    while h and h[0].role == "assistant":
        h.pop(0)
    return h


def stream_answer(system_instruction: str, history: list[ChatMessage], last_user: str):
    client = _get_client()
    chat_history: list[dict] = []
    for m in _normalize_history(history):
        role = "user" if m.role == "user" else "model"
        chat_history.append({"role": role, "parts": [{"text": m.content}]})

    chat = client.chats.create(
        model=settings.gemini_chat_model,
        config={
            "system_instruction": system_instruction,
            "temperature": 0.35,
        },
        history=chat_history,
    )
    for chunk in chat.send_message_stream(last_user):
        t = getattr(chunk, "text", None) or ""
        if t:
            yield t
