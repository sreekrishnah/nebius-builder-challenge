# Ambition Gazette — Python API Backend

FastAPI + Socket.IO Python backend for Ambition Gazette, providing real-time event intelligence, memory indexing, Gemini AI synthesis, and bidirectional voice streaming.

## Tech Stack

- **Framework:** FastAPI, Uvicorn, Python-SocketIO (ASGI)
- **Database & Storage:** Supabase (`supabase-py`), AsyncPG
- **AI Models:** Google GenAI (`google-genai` for Gemini 3.5 Flash Lite & Gemini 3.8 Live)
- **Validation & Settings:** Pydantic v2, Pydantic-Settings

## Setup

1. **Virtual Environment:**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment:**
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

4. **Run Database Migrations:**
   ```bash
   python scripts/migrate.py
   ```

5. **Start Development Server:**
   ```bash
   python -m src.main
   # or with Uvicorn directly:
   uv run uvicorn src.main:socket_app --host 0.0.0.0 --port 5000 --reload
   ```

## Scripts

- `python scripts/migrate.py` — Run database migrations against Supabase.
- `python scripts/seed_demo.py` — Seed demo personas (Ananya, Joe, etc.) with cached news developments.
- `python scripts/reembed.py` — Refresh embeddings for all active user ambitions.
- `python scripts/e2e.py` — End-to-end integration test validating auth, dashboard, ambitions, and pipeline.
