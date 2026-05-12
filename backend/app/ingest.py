from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.database import get_conn
from app.gemini_service import iter_embed_document_vectors
from app.parsers import parse_nbib, parse_ris, parse_zotero_json, parse_zotero_sqlite
from app.retrieval import embedding_document_text
from app import vector_sqlite


def _parse_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    name = Path(filename).name.lower()

    if content[:16].startswith(b"SQLite format 3"):
        return parse_zotero_sqlite(content, name)

    text = content.decode("utf-8", errors="replace")

    if name.endswith(".ris") or name.endswith(".wos"):
        return parse_ris(text, name)
    if name.endswith(".nbib") or name.endswith(".medline") or name.endswith(".txt"):
        if "TI  -" in text or "AB  -" in text or "AU  -" in text:
            return parse_nbib(text, name)
        if "TY  -" in text:
            return parse_ris(text, name)
        return parse_nbib(text, name)
    if name.endswith(".json"):
        return parse_zotero_json(text, name)
    if name.endswith(".sqlite") or name.endswith(".db"):
        return parse_zotero_sqlite(content, name)

    if "TY  -" in text:
        return parse_ris(text, name)
    if "TI  -" in text:
        return parse_nbib(text, name)
    return []


def ingest_bytes(filename: str, content: bytes, library_id: str) -> tuple[int, int, list[str]]:
    records = _parse_file(filename, content)
    imported = 0
    skipped = 0
    new_ids: list[str] = []
    pending: list[tuple[str, str]] = []

    with get_conn() as conn:
        for rec in records:
            pid = str(uuid.uuid4())
            authors_json = json.dumps(rec.get("authors") or [])
            try:
                conn.execute(
                    """
                    INSERT INTO papers (
                        id, title, abstract, authors_json, year, doi, journal,
                        source_file, raw_json, shortlisted, content_hash, library_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        pid,
                        rec["title"],
                        rec.get("abstract"),
                        authors_json,
                        rec.get("year"),
                        rec.get("doi"),
                        rec.get("journal"),
                        rec.get("source_file"),
                        json.dumps(rec, ensure_ascii=False),
                        rec["content_hash"],
                        library_id,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                skipped += 1
                continue

            imported += 1
            new_ids.append(pid)
            emb_text = embedding_document_text(rec["title"], rec.get("abstract"))
            pending.append((pid, emb_text))

    if pending:
        texts = [t for _, t in pending]
        print(f"[ingest] embedding {len(texts)} new records from {filename}…", flush=True)
        for emb, (pid, _) in zip(
            iter_embed_document_vectors(texts), pending, strict=True
        ):
            vector_sqlite.save_embedding(pid, emb)
        print(f"[ingest] finished embeddings for {filename}", flush=True)

    return imported, skipped, new_ids
