from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def parse_nbib(text: str, source_name: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    records: list[dict[str, list[str]]] = []
    cur: dict[str, list[str]] | None = None

    def flush():
        nonlocal cur
        if cur and (cur.get("TI") or cur.get("AB")):
            records.append(cur)
        cur = None

    tag_re = re.compile(r"^([A-Z]{2,4})\s*-\s")

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = tag_re.match(line)
        if m:
            tag = m.group(1)
            val = line[m.end() :].strip()
            if tag == "PMID" and cur is None:
                cur = {}
            if cur is None:
                cur = {}
            cur.setdefault(tag, []).append(val)
        elif cur is not None and line.startswith("      "):
            cont = line.strip()
            if cur:
                last_tag = None
                for t in ("AB", "TI", "FAU", "AU", "DP", "JT", "DOI"):
                    if t in cur:
                        last_tag = t
                if last_tag and cont:
                    cur[last_tag][-1] = f"{cur[last_tag][-1]} {cont}".strip()

    flush()

    out: list[dict[str, Any]] = []
    for r in records:
        title = " ".join(r.get("TI", [])).strip()
        abstract = " ".join(r.get("AB", [])).strip()
        authors = [a.strip() for a in r.get("FAU", []) or r.get("AU", []) if a.strip()]
        year = None
        dp = " ".join(r.get("DP", [])).strip()
        if dp:
            ym = re.search(r"(19|20)\d{2}", dp)
            if ym:
                year = int(ym.group(0)[:4])
        doi = None
        for lid in r.get("LID", []):
            if "[doi]" in lid.lower():
                doi = lid.split("[", 1)[0].strip()
                break
        if not doi:
            for aid in r.get("AID", []):
                if aid.lower().endswith("[doi]"):
                    doi = aid.rsplit("[", 1)[0].strip()
                    break
        journal = " ".join(r.get("JT", []) or r.get("TA", [])).strip() or None

        if not title and not abstract:
            continue
        payload = {
            "title": title or "(untitled)",
            "abstract": abstract or None,
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
        out.append(payload)
    return out
