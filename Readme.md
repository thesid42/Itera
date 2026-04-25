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
│   └── app.py           # Textual 3-pane dashboard
└── utils/
    ├── models.py         # Pydantic models shared across all engines
    └── cost_analyzer.py  # AST-based cost extraction from generated .py
```

## Setup

```bash
# 1. Clone / unzip
cd itera

# 2. Create virtualenv
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 5. Run
python main.py
```

## Usage

1. Type your experiment goal in the input bar and press Enter
   - Example: `96-well plate PCR for 80 samples, minimize cost`
2. Three strategies appear in the marketplace pane — type `1`, `2`, or `3` to select
3. Watch the compiler generate Opentrons code in real time
4. The iteration engine runs the simulator and self-heals errors (up to 5 attempts)
5. Verified `.py` file is saved to `/tmp/itera_<strategy_name>.py`

## Keybindings

| Key      | Action           |
|----------|------------------|
| Enter    | Submit goal / select strategy |
| Ctrl+R   | Reset all panes  |
| Ctrl+C   | Quit             |

## Cost Model

| Resource     | Rate                        |
|--------------|-----------------------------|
| Tips (300µL) | $0.18 each                  |
| Enzymes      | $0.08/µL                    |
| Antibodies   | $0.12/µL                    |
| Buffers      | $0.001/µL                   |
| Machine time | $0.35/min                   |

## Notes

- If `opentrons_simulate` is not installed, Itera uses built-in static analysis (dry-run mode)
- All Anthropic calls use `claude-sonnet-4-20250514` with streaming
- The Pydantic models enforce strict typing across all engine boundaries
- The cost analyzer parses the generated code's AST — it reads what was actually written, not what was planned
