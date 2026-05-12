from __future__ import annotations

import hashlib
import json
from typing import Any


def _creators(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for c in item.get("creators") or []:
        if not isinstance(c, dict):
            continue
        last = (c.get("lastName") or "").strip()
        first = (c.get("firstName") or "").strip()
        if last and first:
            names.append(f"{last}, {first}")
        elif last:
            names.append(last)
    return names


def _year(item: dict[str, Any]) -> int | None:
    d = item.get("date")
    if not d:
        return None
    s = str(d)
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def parse_zotero_json(text: str, source_name: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        abstract = (item.get("abstractNote") or "").strip() or None
        if not title and not abstract:
            continue
        doi = (item.get("DOI") or item.get("doi") or "").strip() or None
        journal = (item.get("publicationTitle") or "").strip() or None
        payload = {
            "title": title or "(untitled)",
            "abstract": abstract,
            "authors": _creators(item),
            "year": _year(item),
            "doi": doi,
            "journal": journal,
            "source_file": source_name,
        }
        h = hashlib.sha256(
            json.dumps(
                {"t": payload["title"], "a": payload["abstract"], "doi": payload["doi"]},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        payload["content_hash"] = h
        out.append(payload)
    return out
