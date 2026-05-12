from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import vector_sqlite
from app.config import settings
from app.database import create_library, get_conn, init_db, library_exists, unique_library_name
from app.gemini_service import approx_tokens, embed_query, stream_answer
from app.ingest import ingest_bytes
from app.models import (
    ChatMessage,
    ChatStreamRequest,
    LibraryOut,
    PaperOut,
    ShortlistUpdate,
    UploadResponse,
)
from app.retrieval import should_run_vector_search

app = FastAPI(title="Research Library RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _row_to_paper(row: Any) -> PaperOut:
    authors = json.loads(row["authors_json"] or "[]")
    return PaperOut(
        id=row["id"],
        library_id=row["library_id"],
        title=row["title"],
        abstract=row["abstract"],
        authors=authors,
        year=row["year"],
        doi=row["doi"],
        journal=row["journal"],
        source_file=row["source_file"],
        shortlisted=bool(row["shortlisted"]),
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/libraries", response_model=list[LibraryOut])
def list_libraries() -> list[LibraryOut]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT l.id, l.name, COUNT(p.id) AS paper_count
            FROM libraries l
            LEFT JOIN papers p ON p.library_id = l.id
            GROUP BY l.id
            ORDER BY LOWER(l.name) ASC, l.created_at DESC
            """
        )
        return [
            LibraryOut(id=str(r["id"]), name=str(r["name"]), paper_count=int(r["paper_count"]))
            for r in cur.fetchall()
        ]


@app.delete("/api/libraries/{library_id}")
def delete_library(library_id: str) -> dict[str, str]:
    lid = library_id.strip()
    if not lid:
        raise HTTPException(status_code=400, detail="library_id is required")
    if not library_exists(lid):
        raise HTTPException(status_code=404, detail="Library not found")

    with get_conn() as conn:
        conn.execute("DELETE FROM papers WHERE library_id = ?", (lid,))
        conn.execute("DELETE FROM libraries WHERE id = ?", (lid,))
        conn.commit()

    return {"status": "deleted", "library_id": lid}


@app.post("/api/upload", response_model=UploadResponse)
async def upload(
    files: list[UploadFile] = File(...),
    library_id: str | None = Form(None),
    new_library_name: str | None = Form(None),
) -> UploadResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    want_existing = (library_id or "").strip()
    want_new_name = (new_library_name or "").strip()

    total_imported = 0
    total_skipped = 0
    all_ids: list[str] = []
    touched_library_ids: list[str] = []

    if want_existing:
        if not library_exists(want_existing):
            raise HTTPException(status_code=404, detail="Library not found")
        for f in files:
            raw = await f.read()
            imp, sk, ids = ingest_bytes(f.filename or "upload", raw, want_existing)
            total_imported += imp
            total_skipped += sk
            all_ids.extend(ids)
        touched_library_ids = [want_existing]

    elif want_new_name:
        lib_id = create_library(unique_library_name(want_new_name))
        for f in files:
            raw = await f.read()
            imp, sk, ids = ingest_bytes(f.filename or "upload", raw, lib_id)
            total_imported += imp
            total_skipped += sk
            all_ids.extend(ids)
        touched_library_ids = [lib_id]

    elif len(files) == 1:
        stem = Path(files[0].filename or "Library").stem.strip() or "Library"
        lib_id = create_library(unique_library_name(stem))
        raw = await files[0].read()
        imp, sk, ids = ingest_bytes(files[0].filename or "upload", raw, lib_id)
        total_imported += imp
        total_skipped += sk
        all_ids.extend(ids)
        touched_library_ids = [lib_id]

    else:
        for f in files:
            stem = Path(f.filename or "Library").stem.strip() or "Library"
            lib_id = create_library(unique_library_name(stem))
            raw = await f.read()
            imp, sk, ids = ingest_bytes(f.filename or "upload", raw, lib_id)
            total_imported += imp
            total_skipped += sk
            all_ids.extend(ids)
            touched_library_ids.append(lib_id)

    primary = touched_library_ids[0] if touched_library_ids else ""

    return UploadResponse(
        imported=total_imported,
        skipped_duplicates=total_skipped,
        paper_ids=all_ids,
        library_id=primary,
        library_ids=touched_library_ids,
    )


@app.get("/api/papers", response_model=list[PaperOut])
def list_papers(
    library_id: str,
    q: str | None = None,
    shortlisted_only: bool = False,
) -> list[PaperOut]:
    if not library_id.strip():
        raise HTTPException(status_code=400, detail="library_id is required")
    if not library_exists(library_id.strip()):
        raise HTTPException(status_code=404, detail="Library not found")

    sql = "SELECT * FROM papers WHERE library_id = ?"
    params: list[Any] = [library_id.strip()]
    if shortlisted_only:
        sql += " AND shortlisted = 1"
    if q:
        sql += " AND (title LIKE ? OR abstract LIKE ? OR authors_json LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY created_at DESC"
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return [_row_to_paper(r) for r in cur.fetchall()]


@app.get("/api/papers/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: str, library_id: str | None = None) -> PaperOut:
    with get_conn() as conn:
        if library_id:
            cur = conn.execute(
                "SELECT * FROM papers WHERE id = ? AND library_id = ?",
                (paper_id, library_id),
            )
        else:
            cur = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return _row_to_paper(row)


@app.patch("/api/papers/shortlist")
def shortlist(body: ShortlistUpdate) -> dict[str, int]:
    if not body.paper_ids:
        return {"updated": 0}
    with get_conn() as conn:
        placeholders = ",".join("?" for _ in body.paper_ids)
        cur = conn.execute(
            f"UPDATE papers SET shortlisted = ? WHERE id IN ({placeholders})",
            [1 if body.shortlisted else 0, *body.paper_ids],
        )
        conn.commit()
        return {"updated": cur.rowcount}


def _fetch_papers_by_ids(ids: list[str], library_id: str) -> list[PaperOut]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            SELECT * FROM papers
            WHERE library_id = ? AND id IN ({placeholders})
            """,
            [library_id, *ids],
        )
        rows = {r["id"]: r for r in cur.fetchall()}
    ordered = [rows[i] for i in ids if i in rows]
    return [_row_to_paper(r) for r in ordered]


def _fetch_library_fallback(limit: int, library_id: str) -> list[PaperOut]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT * FROM papers
            WHERE library_id = ?
            ORDER BY shortlisted DESC, created_at DESC
            LIMIT ?
            """,
            (library_id, limit),
        )
        return [_row_to_paper(r) for r in cur.fetchall()]


def _reference_block(papers: list[PaperOut]) -> str:
    lines: list[str] = []
    for i, p in enumerate(papers, start=1):
        auth = "; ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")
        yr = str(p.year) if p.year else "n.d."
        lines.append(
            f"[{i}] id={p.id} | {auth} ({yr}) | {p.title} | DOI: {p.doi or 'n/a'}"
        )
        if p.abstract:
            lines.append(f"    Abstract: {p.abstract}")
        else:
            lines.append("    Abstract: (not available)")
    return "\n".join(lines)


def _build_system_instruction(corpus_block: str) -> str:
    return (
        "You are an expert systematic reviewer and librarian. "
        "Answer using ONLY the corpus below. If the corpus is insufficient, say so explicitly.\n"
        "When you cite evidence, use bracket numbers that match the reference list, e.g. [3].\n"
        "At the end, add a short 'Sources' section listing each cited [n] with title + first author + year.\n\n"
        "CORPUS:\n"
        f"{corpus_block}"
    )


@app.post("/api/chat/stream")
async def chat_stream(req: ChatStreamRequest) -> StreamingResponse:
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="Last message must be from the user")

    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="Server missing GEMINI_API_KEY")

    lid = req.library_id.strip()
    if not lid:
        raise HTTPException(status_code=400, detail="library_id is required")
    if not library_exists(lid):
        raise HTTPException(status_code=404, detail="Library not found")

    last_user = req.messages[-1].content
    prior = req.messages[:-1]
    has_prior_assistant = any(m.role == "assistant" for m in prior)

    run_vector = should_run_vector_search(req, has_prior_assistant)
    selected = list(dict.fromkeys(req.selected_paper_ids or []))

    async def gen() -> AsyncIterator[bytes]:
        try:
            papers: list[PaperOut] = []
            retrieved = False

            cap = settings.max_context_papers
            context_fallback = False

            if run_vector:
                retrieved = True
                q_emb = embed_query(last_user)
                filt: set[str] | None = None
                if (
                    req.retrieval_mode == "auto"
                    and selected
                    and not has_prior_assistant
                ):
                    filt = set(selected)
                pairs = vector_sqlite.query_similar(
                    q_emb,
                    settings.retrieval_top_k,
                    library_id=lid,
                    paper_id_filter=filt,
                )
                ids = [pid for pid, _ in pairs]
                papers = _fetch_papers_by_ids(ids, lid)
            else:
                papers = _fetch_papers_by_ids(selected[:cap], lid)

            if not papers and selected:
                papers = _fetch_papers_by_ids(selected[:cap], lid)
            if not papers and req.retrieval_mode != "never":
                fb = _fetch_library_fallback(cap, lid)
                if fb:
                    papers = fb
                    context_fallback = True

            if not papers:
                yield (
                    json.dumps(
                        {
                            "type": "error",
                            "message": "No papers in context. Upload into this library, shortlist articles, or start a new search.",
                        }
                    ).encode()
                    + b"\n"
                )
                return

            corpus = _reference_block(papers)
            system_instr = _build_system_instruction(corpus)

            history_msgs = [m for m in prior if m.role in ("user", "assistant")]
            gemini_history = [ChatMessage(role=m.role, content=m.content) for m in history_msgs]

            est = approx_tokens(system_instr)
            est += approx_tokens(last_user)
            for m in gemini_history:
                est += approx_tokens(m.content)

            meta = {
                "type": "meta",
                "retrieved": retrieved,
                "context_paper_ids": [p.id for p in papers],
                "citation_map": {str(i + 1): p.id for i, p in enumerate(papers)},
                "estimated_context_tokens": est,
                "context_near_limit": est >= settings.context_token_warn_threshold,
                "context_fallback": context_fallback,
            }
            yield (json.dumps(meta).encode() + b"\n")

            for piece in stream_answer(system_instr, gemini_history, last_user):
                yield (json.dumps({"type": "token", "text": piece}).encode() + b"\n")

            yield (json.dumps({"type": "done"}).encode() + b"\n")
        except Exception as e:  # noqa: BLE001
            yield (
                json.dumps({"type": "error", "message": str(e)}).encode() + b"\n"
            )

    return StreamingResponse(gen(), media_type="application/x-ndjson")
