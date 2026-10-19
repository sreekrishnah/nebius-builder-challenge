# Ambition Gazette — Personal Intelligence Agent

> **Nebius × NVIDIA Global AI Hackathon 2026**
> *Track: Best Apps and Agents* | *Bonus: Best Use of Tavily*

Ambition Gazette is a personal intelligence system that understands a user's strategic ambition and continuously identifies, reasons over, and synthesizes real-world developments that influence that goal.

It is **not** a generic news aggregator, chatbot, or simple summarizer. Its autonomous loop operates across time:

```text
User Ambition & Temporal Memory
               │
               ▼
NVIDIA Nemotron (Nebius Token Factory) ──► Targeted Multi-Angle Research Plan
               │
               ▼
      Tavily Search Engine ──────────────► Live Web Discovery & Evidence Retrieval
               │
               ▼
Evidence Layer & Normalization ──────────► Deduplication, Canonical URLs & Provenance
               │
               ▼
NVIDIA Nemotron Reasoning Engine ────────► Trajectory Shift, Opportunities, Risks & Actions
               │
               ▼
    Structured Daily Report ─────────────► Grounded Text Briefing & Citations
               │
               ▼
     Gemini 3.8 Live Voice ──────────────► Interactive Spoken Briefing & Clarifications
               │
               ▼
Feedback Interpreter (Nemotron) ─────────► Structured Preferences & Suppressions
               │
               ▼
Durable Temporal Memory ─────────────────► Changes Day 2 Research Queries & Relevance
```

---

## 1. Core Architecture & Provider Responsibilities

| Subsystem | Provider | Role & Boundary |
| :--- | :--- | :--- |
| **Reasoning Engine** | **Nebius Token Factory**<br>`nvidia/nemotron-3-super-120b-a12b` | Owns core intelligence: formulates research plans, extracts developments, evaluates ambition relevance, assesses trajectory changes, synthesizes structured reports, and interprets feedback into memory deltas. |
| **Web Discovery & Evidence** | **Tavily AI Search** | Real-time targeted web searches, counter-evidence investigation, and structured evidence extraction preserving canonical source URLs, publishers, and publication dates. |
| **Voice Interface** | **Gemini 3.8 Live** | Real-time interactive voice agent that explains the pre-computed daily report, answers questions grounded in evidence, and captures spoken feedback. |
| **Durable Continuity** | **Supabase / PostgreSQL + pgvector** | Stores ambitions, identity, structured temporal memory (`interests`, `suppressions`), belief states, report items, and verifiable source provenance. |

---

## 2. Quick Start & Setup

### Prerequisites
- Python 3.11+ / 3.12
- Node.js 18+ / 20+
- Nebius Token Factory API key (for NVIDIA Nemotron inference)
- Tavily API key (for live web evidence retrieval)
- Google Gemini API key (for embeddings & Gemini Live voice)
- Supabase project (PostgreSQL + pgvector)

### Backend Setup (`api/`)

```bash
cd api

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in NEBIUS_API_KEY, TAVILY_API_KEY, GEMINI_API_KEY, and SUPABASE credentials

# Run database migrations
python scripts/migrate.py

# Run test suite
pytest tests -v

# Start FastAPI backend (http://localhost:5000)
python -m src.main
```

### Frontend Setup (`app/`)

```bash
cd app

# Install dependencies
npm install

# Configure environment
cp .env.example .env.local

# Run build verification
npm run build

# Start Next.js development server (http://localhost:3000)
npm run dev
```

---

## 3. Environment Variables

### Backend (`api/.env`)
```bash
# Server
PORT=5000
NODE_ENV=development
FRONTEND_URL=http://localhost:3000

# Nebius Token Factory (NVIDIA Nemotron Reasoning Engine)
NEBIUS_API_KEY=your_nebius_api_key
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
NEBIUS_MODEL_ID=nvidia/nemotron-3-super-120b-a12b

# Tavily Search API
TAVILY_API_KEY=your_tavily_api_key
TAVILY_SEARCH_DEPTH=basic
TAVILY_MAX_RESULTS=5

# Google Gemini (Embeddings & Live Voice)
GEMINI_API_KEY=your_gemini_api_key
GEMINI_TEXT_MODEL=gemini-3.5-flash-lite
GEMINI_LIVE_MODEL=gemini-3.8-live
GEMINI_EMBEDDING_MODEL=gemini-embedding-001

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
DATABASE_URL=postgresql://...

# Authentication & Security
AUTH_SECRET=your_32_char_minimum_secret_key
CRON_SECRET=your_cron_secret_token
```

### Frontend (`app/.env.local`)
```bash
PORT=3000
NEXT_PUBLIC_API_URL=http://localhost:5000
INTERNAL_API_URL=http://localhost:5000
NEXT_PUBLIC_DEMO_MODE=false
```

---

## 4. Proving the Feedback Learning Loop (Day 1 ➔ Day 2)

Ambition Gazette's core differentiator is that user feedback today changes what the system searches for, considers important, and explains tomorrow.

### Test Scenario
1. **Day 1**: User has ambition *"Build an AI robotics startup"*.
   - Nemotron generates research queries across hardware and software.
   - Report presents developments in robotics actuators and AI foundation models.
2. **User Feedback**: Spoken to voice agent or submitted via UI:
   > *"I only care about the AI software layer, not robotics hardware manufacturing."*
3. **Nemotron Interpretation**:
   - `feedback_type`: `topic_preference_correction`
   - `operation`: `suppress_topic` / `decrease_priority`
   - `proposed_topic`: `robotics hardware`
   - `proposed_memory_type`: `suppression`
   - Persisted durably to `user_memory` and `feedback`.
4. **Day 2 Intelligence Run**:
   - Research planner retrieves active suppressions: `suppressions=['robotics hardware']`.
   - Nemotron excludes hardware manufacturing queries and generates focused software agent queries.
   - Relevance engine filters out hardware-only news.
   - The resulting report proves that durable learning occurred.

Run the automated test verifying this behavior:
```bash
pytest api/tests/test_temporal_memory_loop.py -v
```

---

## 5. Hackathon Verification & Test Endpoints

- **Live Provider Health Check**:
  `GET /api/pipeline/providers/health`
  Tests real server-side inference on Nebius Token Factory (`nvidia/nemotron-3-super-120b-a12b`), verifies Tavily Search API, and checks Gemini Live connectivity.
- **Natural Language Feedback Interpreter**:
  `POST /api/feedback/interpret`
  Interprets freeform speech or text corrections into durable memory updates using NVIDIA Nemotron.
- **Automated Unit & Contract Tests**:
  `pytest api/tests -v`
  Includes test suites for Tavily normalization, Nebius structured output extraction, report contract validation, and temporal memory loops.

---

## 6. Significant Updates Made During Hackathon

1. **Integrated Nebius Token Factory & NVIDIA Nemotron**: Decoupled the intelligence layer from vendor lock-in. Routed research planning, event analysis, trajectory evaluation, structured report synthesis, and feedback interpretation to NVIDIA Nemotron on Nebius.
2. **Integrated Tavily AI Search**: Replaced generic scraping with official Tavily multi-step search engine, preserving canonical source URLs, publisher attribution, and publication dates.
3. **Structured Daily Intelligence Contract**: Added executive summary, trajectory assessment, historical event connections, opportunities, risks, and evidence-grounded next actions.
4. **Dual Text + Gemini 3.8 Live Experience**: Wired real-time voice tools so the voice agent discusses the existing report and persists spoken feedback directly into temporal memory.
5. **Durable Temporal Feedback Loop**: Proved that user corrections dynamically reshape subsequent research queries and relevance evaluations.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
