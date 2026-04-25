"""
Itera TUI — The command-center interface.
Three-pane Textual dashboard:
  Left:        Chat/goal input + pipeline status
  Top-right:   Strategy marketplace grid (3 cards)
  Bottom-right: Live simulation log + diff viewer
"""
import asyncio
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Footer, Header, Input, Label, Log,
    Markdown, RichLog, Static
)
from textual.reactive import reactive
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.columns import Columns
from io import StringIO

from engines.marketplace import run_marketplace
from engines.compiler import compile_strategy
from engines.iteration import run_iteration
from utils.models import Strategy, MarketplaceResult, SimulationAttempt, IterationResult


WELCOME = """[bold cyan]ITERA[/] — Autonomous Cloud Lab Compiler
[dim]Type a lab experiment goal and press Enter to begin.[/]

[dim]Examples:[/]
  [italic]• 96-well plate PCR for 80 samples, minimize cost
  • DNA extraction from 24 tissue samples
  • ELISA assay, high throughput, 384-well[/]
"""

PIPELINE_STAGES = [
    ("●", "IDLE",       "dim"),
    ("●", "MARKET",     "cyan"),
    ("●", "COMPILE",    "yellow"),
    ("●", "SIMULATE",   "magenta"),
    ("●", "VERIFIED",   "green"),
]


class StatusBar(Static):
    stage: reactive[int] = reactive(0)

    def render(self) -> Text:
        t = Text()
        for i, (dot, label, color) in enumerate(PIPELINE_STAGES):
            active = i == self.stage
            style = color if active else "dim"
            t.append(f" {dot} {label} ", style=f"bold {style}" if active else style)
            if i < len(PIPELINE_STAGES) - 1:
                t.append("→", style="dim")
        return t


class StrategyCard(Static):
    def __init__(self, strategy: Strategy, index: int, **kwargs):
        super().__init__(**kwargs)
        self.strategy = strategy
        self.index = index

    def render(self) -> Panel:
        s = self.strategy
        c = s.cost
        risk_color = "green" if c.failure_risk.score < 0.3 else "yellow" if c.failure_risk.score < 0.6 else "red"
        rec = "[bold green] ★ RECOMMENDED[/]" if s.recommended else ""

        body = (
            f"[dim]{s.description}[/]\n\n"
            f"[bold]Labware:[/] [cyan]{s.labware}[/]\n"
            f"[bold]Duration:[/] {s.estimated_duration_min} min\n\n"
            f"[bold]Cost breakdown:[/]\n"
            f"  Tips:      {c.tips.count} tips   [yellow]${c.tips.usd:.2f}[/]\n"
            f"  Reagents:  {c.reagents.total_ul:.0f} µL  [yellow]${c.reagents.usd:.2f}[/]\n"
            f"  Machine:   {c.machine_time.minutes:.0f} min  [yellow]${c.machine_time.usd:.2f}[/]\n"
            f"  [bold]Total: [yellow]${c.total_estimated_usd:.2f}[/][/]\n\n"
            f"Risk: [{risk_color}]{'█' * int(c.failure_risk.score * 10)}{'░' * (10 - int(c.failure_risk.score * 10))}[/] "
            f"[{risk_color}]{c.failure_risk.score:.0%}[/]"
        )
        if c.failure_risk.flags:
            body += f"\n[dim red]⚠ {c.failure_risk.flags[0]}[/]"

        title = f"[{['cyan','green','magenta'][self.index % 3]}]Strategy {s.id}: {s.name}[/]{rec}"
        return Panel(body, title=title, border_style="bright_black", padding=(0, 1))


class IteraApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        height: 1;
        background: $surface;
        padding: 0 2;
    }
    #main-panes {
        layout: horizontal;
        height: 1fr;
    }
    #left-pane {
        width: 38%;
        border-right: solid $surface-darken-2;
        layout: vertical;
    }
    #chat-log {
        height: 1fr;
        padding: 1 2;
    }
    #input-area {
        height: 3;
        border-top: solid $surface-darken-2;
        padding: 0 1;
    }
    #right-pane {
        width: 62%;
        layout: vertical;
    }
    #marketplace-pane {
        height: 55%;
        border-bottom: solid $surface-darken-2;
        padding: 1;
        overflow-y: auto;
    }
    #sim-pane {
        height: 45%;
        padding: 1;
        overflow-y: auto;
    }
    #goal-input {
        width: 100%;
    }
    .section-title {
        color: $text-muted;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+r", "reset", "Reset"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="ITERA — Cloud Lab Compiler")
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-panes"):
            with Vertical(id="left-pane"):
                yield Label(".section-title", id="left-title")
                yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
                with Container(id="input-area"):
                    yield Input(placeholder="Enter your lab goal…", id="goal-input")
            with Vertical(id="right-pane"):
                yield Label("MARKETPLACE — select strategy (type 1, 2, or 3)", classes="section-title")
                yield RichLog(id="marketplace-pane", highlight=True, markup=True)
                yield Label("SIMULATION LOG", classes="section-title")
                yield RichLog(id="sim-pane", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#chat-log", RichLog).write(WELCOME)
        self.query_one("#marketplace-pane", RichLog).write(
            "[dim]Waiting for lab goal…[/]"
        )
        self.query_one("#sim-pane", RichLog).write(
            "[dim]Simulation output will appear here.[/]"
        )
        self._marketplace_result = None
        self._awaiting_selection = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        self.query_one("#goal-input", Input).clear()

        if self._awaiting_selection and value in ("1", "2", "3"):
            self._on_strategy_selected(int(value))
        else:
            asyncio.create_task(self._run_marketplace(value))

    async def _run_marketplace(self, goal: str) -> None:
        chat = self.query_one("#chat-log", RichLog)
        market = self.query_one("#marketplace-pane", RichLog)
        status = self.query_one(StatusBar)

        status.stage = 1
        chat.write(f"\n[bold cyan]→ Goal:[/] {goal}\n")
        chat.write("[dim]Querying marketplace…[/]\n")
        market.clear()
        market.write("[dim]Generating strategies…[/]\n")

        def on_token(t):
            pass  # streaming collected internally; update after

        loop = asyncio.get_event_loop()
        try:
            result: MarketplaceResult = await loop.run_in_executor(
                None, lambda: run_marketplace(goal)
            )
        except Exception as e:
            chat.write(f"[bold red]Marketplace error:[/] {e}\n")
            status.stage = 0
            return

        self._marketplace_result = result
        market.clear()

        for i, s in enumerate(result.strategies):
            c = s.cost
            rec = " ★" if s.recommended else ""
            risk_bar = "█" * int(c.failure_risk.score * 10) + "░" * (10 - int(c.failure_risk.score * 10))
            risk_color = "green" if c.failure_risk.score < 0.3 else "yellow" if c.failure_risk.score < 0.6 else "red"
            colors = ["cyan", "green", "magenta"]
            col = colors[i % 3]

            market.write(
                f"[bold {col}]┌─ [{i+1}] {s.name}{rec}[/]\n"
                f"[dim]{s.description}[/]\n"
                f"[{col}]│[/] Labware:  [white]{s.labware}[/]\n"
                f"[{col}]│[/] Duration: {s.estimated_duration_min} min\n"
                f"[{col}]│[/] Tips:     {c.tips.count}×  [yellow]${c.tips.usd:.2f}[/]   "
                f"Reagents: {c.reagents.total_ul:.0f}µL  [yellow]${c.reagents.usd:.2f}[/]   "
                f"Machine: {c.machine_time.minutes:.0f}min  [yellow]${c.machine_time.usd:.2f}[/]\n"
                f"[{col}]│[/] [bold]Total: [yellow]${c.total_estimated_usd:.2f}[/][/]   "
                f"Risk: [{risk_color}]{risk_bar}[/] {c.failure_risk.score:.0%}\n"
            )
            if c.failure_risk.flags:
                for flag in c.failure_risk.flags:
                    market.write(f"[{col}]│[/] [dim red]⚠ {flag}[/]\n")
            market.write(f"[{col}]└{'─'*60}[/]\n\n")

        chat.write("[bold]Select a strategy:[/] type [cyan]1[/], [green]2[/], or [magenta]3[/] and press Enter\n")
        self._awaiting_selection = True
        status.stage = 0

    def _on_strategy_selected(self, choice: int) -> None:
        result = self._marketplace_result
        if not result or choice < 1 or choice > len(result.strategies):
            return
        strategy = result.strategies[choice - 1]
        self._awaiting_selection = False
        asyncio.create_task(self._run_compile_and_iterate(strategy))

    async def _run_compile_and_iterate(self, strategy: Strategy) -> None:
        chat = self.query_one("#chat-log", RichLog)
        sim = self.query_one("#sim-pane", RichLog)
        status = self.query_one(StatusBar)

        status.stage = 2
        chat.write(f"\n[bold green]→ Compiling:[/] {strategy.name}\n")
        sim.clear()
        sim.write("[dim]Compiling Opentrons protocol…[/]\n")

        loop = asyncio.get_event_loop()

        try:
            compiler_result = await loop.run_in_executor(
                None, lambda: compile_strategy(strategy)
            )
        except Exception as e:
            chat.write(f"[bold red]Compiler error:[/] {e}\n")
            status.stage = 0
            return

        sim.write("[bold]Generated code preview:[/]\n")
        preview_lines = compiler_result.python_code.split("\n")[:12]
        for line in preview_lines:
            sim.write(f"[dim]{line}[/]\n")
        sim.write("[dim]  …[/]\n\n")

        status.stage = 3
        chat.write("[bold magenta]→ Running simulation loop…[/]\n")

        def on_attempt(attempt: SimulationAttempt):
            if attempt.passed:
                sim.write(
                    f"[bold green]✓ Attempt {attempt.attempt} — PASSED[/]\n"
                )
            else:
                sim.write(
                    f"[bold red]✗ Attempt {attempt.attempt} — FAILED[/]\n"
                )
                if attempt.stderr:
                    for line in attempt.stderr.split("\n")[:6]:
                        sim.write(f"  [red]{line}[/]\n")
                if attempt.diff:
                    sim.write("\n[bold yellow]── diff ──────────────────[/]\n")
                    for line in attempt.diff.split("\n")[:20]:
                        if line.startswith("+"):
                            sim.write(f"[green]{line}[/]\n")
                        elif line.startswith("-"):
                            sim.write(f"[red]{line}[/]\n")
                        elif line.startswith("@"):
                            sim.write(f"[cyan]{line}[/]\n")
                        else:
                            sim.write(f"[dim]{line}[/]\n")
                sim.write("[bold yellow]─ recompiling…[/]\n\n")

        try:
            iter_result: IterationResult = await loop.run_in_executor(
                None, lambda: run_iteration(compiler_result, on_attempt=on_attempt)
            )
        except Exception as e:
            chat.write(f"[bold red]Simulation error:[/] {e}\n")
            status.stage = 0
            return

        await self._show_final_result(iter_result, strategy)

    async def _show_final_result(
        self, result: IterationResult, strategy: Strategy
    ) -> None:
        chat = self.query_one("#chat-log", RichLog)
        sim = self.query_one("#sim-pane", RichLog)
        status = self.query_one(StatusBar)

        c = result.cost

        if result.success:
            status.stage = 4
            sim.write("\n[bold green]═══ VERIFIED — Zero simulation errors ═══[/]\n")
        else:
            status.stage = 0
            sim.write(f"\n[bold red]═══ BEST EFFORT after {result.total_attempts} attempts ═══[/]\n")

        # Final cost scorecard
        sim.write("\n[bold]Final cost analysis (from verified code):[/]\n")
        sim.write(f"  Tips:        {c.tips.count} tips     [yellow]${c.tips.usd:.2f}[/]\n")
        sim.write(f"  Reagents:    {c.reagents.total_ul:.0f} µL     [yellow]${c.reagents.usd:.2f}[/]\n")
        sim.write(f"  Machine:     {c.machine_time.minutes:.1f} min    [yellow]${c.machine_time.usd:.2f}[/]\n")
        sim.write(f"  [bold]Total est.:  [yellow]${c.total_estimated_usd:.2f}[/][/]\n")

        if c.failure_risk.flags:
            sim.write("\n[bold red]Risk flags:[/]\n")
            for flag in c.failure_risk.flags:
                sim.write(f"  [red]⚠[/] {flag}\n")

        # Save output file
        output_path = f"/tmp/itera_{strategy.name.replace(' ', '_').lower()}.py"
        with open(output_path, "w") as f:
            f.write(result.final_code)

        chat.write(f"\n[bold green]✓ Protocol saved:[/] [cyan]{output_path}[/]\n")
        chat.write(f"[dim]Attempts: {result.total_attempts} | "
                   f"Total cost: ${c.total_estimated_usd:.2f}[/]\n")
        chat.write("\n[dim]Enter a new goal to start again, or Ctrl+C to exit.[/]\n")
        self._awaiting_selection = False

    def action_reset(self) -> None:
        self.query_one("#chat-log", RichLog).clear()
        self.query_one("#marketplace-pane", RichLog).clear()
        self.query_one("#sim-pane", RichLog).clear()
        self.query_one(StatusBar).stage = 0
        self._marketplace_result = None
        self._awaiting_selection = False
        self.query_one("#chat-log", RichLog).write(WELCOME)
        self.query_one("#marketplace-pane", RichLog).write("[dim]Waiting for lab goal…[/]")
        self.query_one("#sim-pane", RichLog).write("[dim]Simulation output will appear here.[/]")
