from __future__ import annotations

import json

import numpy as np

from app.database import get_conn


def _rows_for_search(
    paper_id_filter: set[str] | None,
    library_id: str,
) -> list[tuple[str, str]]:
    with get_conn() as conn:
        base = """
            SELECT id, embedding_json FROM papers
            WHERE embedding_json IS NOT NULL
              AND library_id = ?
        """
        params: list[str] = [library_id]
        if paper_id_filter:
            placeholders = ",".join("?" for _ in paper_id_filter)
            sql = base + f" AND id IN ({placeholders})"
            params.extend(list(paper_id_filter))
            cur = conn.execute(sql, params)
        else:
            cur = conn.execute(base, params)
        return [(str(r[0]), str(r[1])) for r in cur.fetchall()]


def query_similar(
    query_embedding: list[float],
    top_k: int,
    library_id: str,
    paper_id_filter: set[str] | None = None,
) -> list[tuple[str, float]]:
    rows = _rows_for_search(paper_id_filter, library_id)
    if not rows:
        return []

    ids: list[str] = []
    mat: list[list[float]] = []
    for pid, js in rows:
        try:
            vec = json.loads(js)
        except json.JSONDecodeError:
            continue
        if not vec:
            continue
        ids.append(pid)
        mat.append(vec)

    if not mat:
        return []

    q = np.asarray(query_embedding, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    M = np.asarray(mat, dtype=np.float32)
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    sims = M @ q
    k = min(top_k, sims.shape[0])
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(ids[int(i)], float(sims[int(i)])) for i in idx]


def save_embedding(paper_id: str, embedding: list[float]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE papers SET embedding_json = ? WHERE id = ?",
            (json.dumps(embedding), paper_id),
        )
        conn.commit()
