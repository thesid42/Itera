# Itera — The Autonomous Cloud Lab Compiler

> Type a biology experiment goal. Get verified, cost-optimized, robot-ready Opentrons code.

## Architecture

```
main.py
├── engines/
│   ├── marketplace.py   # Engine 1 — LLM generates 3 cost-ranked strategies
│   ├── compiler.py      # Engine 2 — LLM compiles strategy → Opentrons Python
│   └── iteration.py     # Engine 3 — sim loop, error correction, diff display
├── tui/
│   └── app.py           # Textual 3-pane terminal dashboard
├── web/
│   └── app.py           # FastAPI SSE backend
├── frontend/
│   └── src/main.js      # Vite-based web UI
└── utils/
    ├── models.py         # Pydantic models shared across all engines
    ├── cost_analyzer.py  # AST-based cost extraction from generated .py
    ├── bio_rag.py        # RAG — local KB + live protocols.io retrieval
    ├── openrouter_client.py  # OpenRouter / Qwen3 streaming client
    └── config_loader.py  # Cached YAML config loader
```

---

## Setup

### 1. Clone and enter the project

```bash
cd Itera
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure API keys

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

Open `.env` and set:

```
OPENROUTER_API_KEY=sk-or-v1-...      # https://openrouter.ai
PROTOCOLS_IO_TOKEN=...               # https://www.protocols.io/developers (optional)
```

---

## Running — TUI (Terminal UI)

The TUI runs entirely in your terminal using a 3-pane Textual dashboard.

```bash
python main.py
```

### TUI Keybindings

| Key      | Action                        |
|----------|-------------------------------|
| Enter    | Submit goal / confirm         |
| 1 / 2 / 3 | Select a strategy            |
| Ctrl+R   | Reset all panes               |
| Ctrl+C   | Quit                          |

---

## Running — Web UI

The web UI requires two processes running at the same time: the FastAPI backend and the Vite frontend dev server.

### Terminal 1 — Start the backend

```bash
python main.py --web
# Listening on http://localhost:8000
```

Optional flags:

```bash
python main.py --web --port 8080   # use a different port
python main.py --web --host 127.0.0.1
```

If port 8000 is already in use:

```bash
# Find and kill the process
lsof -ti :8000 | xargs kill
# Then retry
python main.py --web
```

### Terminal 2 — Start the frontend

```bash
cd frontend
npm install       # first time only
npm run dev
```

Open **http://localhost:5173** in your browser.

> The Vite dev server proxies all `/api/*` requests to the FastAPI backend, so no CORS setup is needed.

### Building for production (optional)

```bash
cd frontend
npm run build     # outputs to frontend/dist/
```

You can then serve `frontend/dist/` with any static file host and point it at the backend.

---

## Configuration

Edit `config.yaml` to adjust the LLM model, pricing, and RAG settings:

```yaml
llm:
  model: qwen/qwen3-235b-a22b   # any OpenRouter model ID
  max_tokens:
    marketplace: 2000
    compiler: 3000

pricing:
  tips:
    standard_200ul: 0.18
    large_1000ul: 0.35
  reagents:
    enzyme: 0.08
    antibody: 0.12
    buffer: 0.001
    default: 0.004
  machine_time_per_min: 0.35

rag:
  top_k: 3
  min_score: 0.05
  protocols_io_max: 2
```

---

## Cost Model

| Resource       | Default Rate  |
|----------------|---------------|
| Tips (200 µL)  | $0.18 each    |
| Tips (1000 µL) | $0.35 each    |
| Enzymes        | $0.08 / µL    |
| Antibodies     | $0.12 / µL    |
| Buffers        | $0.001 / µL   |
| Machine time   | $0.35 / min   |

All rates are configurable in `config.yaml`.

---

## Notes

- If `opentrons_simulate` is not installed, Itera falls back to built-in static analysis (dry-run mode)
- Qwen3 `<think>` tokens are filtered out before display — you only see the final answer
- The cost analyzer parses the generated code's AST — it reads what was actually written, not what was planned
- Logs are written to `logs/itera.log` (rotating, 5 MB × 3 backups)
- The `PROTOCOLS_IO_TOKEN` is optional — without it, only the local protocol knowledge base is used for RAG
