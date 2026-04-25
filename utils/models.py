"""
Shared Pydantic models for Itera.
All three engines exchange these typed objects — no raw dicts.
"""
from pydantic import BaseModel, Field
from typing import Optional


# ─── Cost breakdown ────────────────────────────────────────────────────────────

class TipCost(BaseModel):
    count: int = 0
    usd: float = 0.0


class ReagentCost(BaseModel):
    total_ul: float = 0.0
    usd: float = 0.0


class MachineTimeCost(BaseModel):
    minutes: float = 0.0
    usd: float = 0.0


class FailureRisk(BaseModel):
    score: float = 0.0          # 0.0 (safe) → 1.0 (high risk)
    flags: list[str] = Field(default_factory=list)


class CostBreakdown(BaseModel):
    tips: TipCost = Field(default_factory=TipCost)
    reagents: ReagentCost = Field(default_factory=ReagentCost)
    machine_time: MachineTimeCost = Field(default_factory=MachineTimeCost)
    failure_risk: FailureRisk = Field(default_factory=FailureRisk)
    total_estimated_usd: float = 0.0

    def recalculate_total(self):
        self.total_estimated_usd = round(
            self.tips.usd + self.reagents.usd + self.machine_time.usd, 2
        )


# ─── Marketplace Engine output ─────────────────────────────────────────────────

class Strategy(BaseModel):
    id: int
    name: str
    description: str
    labware: str                  # e.g. "96-well plate, 8-channel pipette"
    estimated_duration_min: int
    cost: CostBreakdown
    recommended: bool = False


class MarketplaceResult(BaseModel):
    goal: str
    strategies: list[Strategy]


# ─── Compiler Engine output ────────────────────────────────────────────────────

class CompilerResult(BaseModel):
    strategy: Strategy
    python_code: str
    attempt: int = 1


# ─── Iteration Engine output ───────────────────────────────────────────────────

class SimulationAttempt(BaseModel):
    attempt: int
    code: str
    stderr: str
    passed: bool
    diff: Optional[str] = None   # unified diff vs previous attempt


class IterationResult(BaseModel):
    final_code: str
    attempts: list[SimulationAttempt]
    total_attempts: int
    success: bool
    cost: CostBreakdown           # recalculated after final code analysis
