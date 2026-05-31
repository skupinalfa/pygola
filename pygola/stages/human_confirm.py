"""The human-confirmation gate. In CONFIRM mode this stage pauses the pipeline
right before anything is sent to the commercial LLM, so a person can inspect
the *sanitized* request and approve or reject it.

Architecturally this is just a stage that raises PipelinePaused. The caller is
responsible for persisting the context, getting a human decision, and resuming.
This keeps the "mini agent with a human in the loop" behavior fully in code and
fully auditable -- the LLM never decides whether to proceed.
"""

from __future__ import annotations

from ..config.schema import Mode
from ..pipeline.context import Decision, GovernanceContext
from ..pipeline.stage import PipelinePaused, Stage


class HumanConfirmStage(Stage):
    name = "human_confirm_gate"

    def __init__(self, mode: Mode):
        self.mode = mode

    def process(self, context: GovernanceContext) -> GovernanceContext:
        if self.mode != Mode.CONFIRM:
            context.record(self.name, {"skipped": True, "mode": self.mode.value})
            return context

        # If a decision was already injected by the caller on resume, honor it.
        if context.decision in (Decision.ALLOW, Decision.BLOCK):
            context.record(self.name, {"resumed_with": context.decision.value})
            return context

        context.decision = Decision.NEEDS_CONFIRM
        context.record(self.name, {"paused": True})
        raise PipelinePaused(context, "awaiting human confirmation")
