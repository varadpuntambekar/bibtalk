from __future__ import annotations

import re

from app.models import ChatStreamRequest

NEW_SEARCH_PATTERN = re.compile(
    r"\b("
    r"find more|search (the )?library|search (my )?collection|"
    r"other papers|different studies|broader (review|search)?|wider (review|search)?|"
    r"what else (is )?in|scan (my |the )?library|pull (more|additional)|"
    r"new sources|expand (the )?search|look (across|through) (my |the )?(library|collection)|"
    r"re-?query|run (a )?new search"
    r")\b",
    re.IGNORECASE,
)


def should_run_vector_search(req: ChatStreamRequest, has_prior_assistant: bool) -> bool:
    if req.retrieval_mode == "always":
        return True
    if req.retrieval_mode == "never":
        return False

    selected = req.selected_paper_ids or []
    if not selected:
        return True
    if not has_prior_assistant:
        return True
    if NEW_SEARCH_PATTERN.search(req.messages[-1].content):
        return True
    return False


def embedding_document_text(title: str, abstract: str | None) -> str:
    body = (abstract or "").strip()
    if body:
        return f"{title.strip()}\n\n{body}"
    return title.strip()
