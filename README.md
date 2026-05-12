# BibTalk

Local web app for managing reference libraries (RIS, PubMed `.nbib`, Zotero exports, and more), searching papers, and chatting with an AI assistant that uses retrieval-augmented generation (RAG) over your uploads. Replies stream in real time; citations like `[3]` open the matching record when they map to retrieved context.

---

## Prerequisites

- **Python** 3.11+ (3.12+ recommended)
- **Node.js** 18+
- A **Google Gemini API key** ([Google AI Studio](https://aistudio.google.com/app/apikey))

Data lives in `backend/data/` (SQLite, created on first run). API calls go to Google Gemini only.

---

## Clone this repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd bibtalk/bibtalk
```

Use the directory that contains **`backend/`** and **`frontend/`** (your clone path may differ).

---

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set `GEMINI_API_KEY`. Optional tuning variables are listed in `.env.example`.

Run the API:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

---

## Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api` to `http://127.0.0.1:8000`.

---

## Using BibTalk

1. **Import** references via the upload card (RIS, PubMed `.nbib`, Zotero JSON/SQLite, etc.).
2. **Select** the active library; the table and chat are scoped to it.
3. **Shortlist** papers (★) to steer the first retrieval pass.
4. **Chat** with retrieval mode as needed; use **New conversation** to reset history when context grows large.
5. **Delete library** removes that library and all embeddings locally (cannot be undone).

---

## Production build (optional)

```bash
cd frontend
npm run build
```

Output is in `frontend/dist/`. For a single URL you would serve `dist/` behind a reverse proxy or mount it from FastAPI; for local use, dev + uvicorn is enough.

---

## Layout of the repo

| Path | Purpose |
|------|---------|
| `backend/app/` | FastAPI, ingest, retrieval, Gemini streaming |
| `backend/data/` | SQLite and runtime data |
| `frontend/src/` | React UI (Vite + TypeScript) |

---

## Troubleshooting

| Problem | Try |
|---------|-----|
| Auth / API errors | Check `GEMINI_API_KEY` in `backend/.env`, restart uvicorn. |
| UI cannot load data | Backend must be on port **8000**; use `npm run dev` so `/api` proxies correctly. |
| Slow embedding on big imports | Tune batch/delay/retry in `.env` per `.env.example`. |
