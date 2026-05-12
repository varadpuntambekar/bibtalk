from __future__ import annotations

import hashlib
import json
from typing import Any

import rispy


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _authors_from_ris(entry: dict[str, Any]) -> list[str]:
    val = entry.get("authors")
    if isinstance(val, list):
        return [_norm(a) for a in val if _norm(a)]
    if val:
        return [_norm(str(val))]
    return []


def _year(entry: dict[str, Any]) -> int | None:
    y = entry.get("year")
    if y is None:
        return None
    try:
        return int(str(y)[:4])
    except ValueError:
        return None


def parse_ris(text: str, source_name: str) -> list[dict[str, Any]]:
    entries = rispy.loads(text, skip_unknown_tags=True)
    out: list[dict[str, Any]] = []
    for e in entries:
        title = _norm(e.get("title"))
        abstract = _norm(e.get("abstract"))
        if not title and not abstract:
            continue
        authors = _authors_from_ris(e)
        payload = {
            "title": title or "(untitled)",
            "abstract": abstract or None,
            "authors": authors,
            "year": _year(e),
            "doi": _norm(e.get("doi")) or None,
            "journal": _norm(e.get("journal"))
            or _norm(e.get("secondary_title"))
            or _norm(e.get("journal_name"))
            or None,
            "source_file": source_name,
        }
        h = hashlib.sha256(
            json.dumps(
                {
                    "t": payload["title"],
                    "a": payload["abstract"],
                    "doi": payload["doi"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        payload["content_hash"] = h
        out.append(payload)
    return out
