from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def parse_zotero_sqlite(data: bytes, source_name: str) -> list[dict[str, Any]]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        Path(path).unlink(missing_ok=True)
        return []

    papers: list[dict[str, Any]] = []
    try:
        cur = conn.execute("SELECT fieldID, fieldName FROM fields")
        field_id_to_name = {int(r[0]): r[1] for r in cur.fetchall()}

        cur = conn.execute(
            """
            SELECT itemID, itemTypeID
            FROM items
            WHERE itemID NOT IN (SELECT itemID FROM deletedItems)
            """
        )
        items = cur.fetchall()

        for row in items:
            item_id = int(row["itemID"])
            cur2 = conn.execute(
                "SELECT fieldID, value FROM itemData WHERE itemID = ?", (item_id,)
            )
            fields: dict[str, str] = {}
            for r in cur2.fetchall():
                name = field_id_to_name.get(int(r["fieldID"]))
                if name:
                    fields[name] = r["value"] or ""

            title = (fields.get("title") or "").strip()
            abstract = (fields.get("abstractNote") or "").strip() or None
            if not title and not abstract:
                continue

            cur3 = conn.execute(
                """
                SELECT c.lastName, c.firstName
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                WHERE ic.itemID = ?
                ORDER BY ic.orderIndex
                """,
                (item_id,),
            )
            authors: list[str] = []
            for cr in cur3.fetchall():
                last = (cr["lastName"] or "").strip()
                first = (cr["firstName"] or "").strip()
                if last and first:
                    authors.append(f"{last}, {first}")
                elif last:
                    authors.append(last)

            year = None
            d = (fields.get("date") or "").strip()
            if len(d) >= 4 and d[:4].isdigit():
                year = int(d[:4])

            doi = (fields.get("DOI") or "").strip() or None
            journal = (fields.get("publicationTitle") or "").strip() or None

            payload = {
                "title": title or "(untitled)",
                "abstract": abstract,
                "authors": authors,
                "year": year,
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
            papers.append(payload)
    finally:
        conn.close()
        Path(path).unlink(missing_ok=True)

    return papers
