import sqlite3
import uuid
from contextlib import contextmanager

from app.config import settings


def _connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.sqlite_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def _migrate_legacy_papers(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS libraries (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    default_lib = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO libraries (id, name) VALUES (?, ?)",
        (default_lib, "Default library"),
    )
    conn.execute(
        """
        CREATE TABLE papers_mig (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            abstract TEXT,
            authors_json TEXT,
            year INTEGER,
            doi TEXT,
            journal TEXT,
            source_file TEXT,
            raw_json TEXT,
            shortlisted INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL,
            embedding_json TEXT,
            library_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (library_id) REFERENCES libraries(id),
            UNIQUE(library_id, content_hash)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO papers_mig (
            id, title, abstract, authors_json, year, doi, journal, source_file,
            raw_json, shortlisted, content_hash, embedding_json, library_id, created_at
        )
        SELECT
            id, title, abstract, authors_json, year, doi, journal, source_file,
            raw_json, shortlisted,
            COALESCE(NULLIF(TRIM(content_hash), ''), id),
            embedding_json,
            ?,
            created_at
        FROM papers
        """,
        (default_lib,),
    )
    conn.execute("DROP TABLE papers")
    conn.execute("ALTER TABLE papers_mig RENAME TO papers")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_papers_shortlisted ON papers(shortlisted)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_papers_library_id ON papers(library_id)"
    )


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS libraries (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
        )
        has_papers = cur.fetchone() is not None

        if not has_papers:
            conn.execute(
                """
                CREATE TABLE papers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    abstract TEXT,
                    authors_json TEXT,
                    year INTEGER,
                    doi TEXT,
                    journal TEXT,
                    source_file TEXT,
                    raw_json TEXT,
                    shortlisted INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    embedding_json TEXT,
                    library_id TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (library_id) REFERENCES libraries(id),
                    UNIQUE(library_id, content_hash)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_papers_shortlisted ON papers(shortlisted)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_papers_library_id ON papers(library_id)"
            )
        else:
            cols = _table_columns(conn, "papers")
            if "library_id" not in cols:
                _migrate_legacy_papers(conn)
            else:
                _ensure_column(conn, "papers", "embedding_json", "TEXT")

        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    names = _table_columns(conn, table)
    if col not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def unique_library_name(preferred_base: str) -> str:
    base = (preferred_base or "").strip() or "Library"
    with get_conn() as conn:
        name = base
        i = 2
        while conn.execute(
            "SELECT 1 FROM libraries WHERE name = ?", (name,)
        ).fetchone():
            name = f"{base} ({i})"
            i += 1
        return name


def create_library(name: str) -> str:
    lid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO libraries (id, name) VALUES (?, ?)",
            (lid, name.strip() or "Untitled library"),
        )
        conn.commit()
    return lid


def library_exists(library_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM libraries WHERE id = ?", (library_id,)
        ).fetchone()
        return row is not None
