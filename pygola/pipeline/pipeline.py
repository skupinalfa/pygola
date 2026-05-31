"""The Pipeline runs a sequence of stages over a context. It is deliberately
*dumb*: it does not make compliance decisions itself, it just orchestrates the
stages in a fixed, predictable order and short-circuits if a request gets
blocked. All the intelligence lives in the stages.

This is the deterministic backbone the earlier design discussion called for:
control flow stays in code, not in an LLM.
"""

from __future__ import annotations

import logging

from .context import Decision, GovernanceContext
from .stage import PipelinePaused, Stage

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, stages: list[Stage]):
        if not stages:
            raise ValueError("A pipeline needs at least one stage.")
        self.stages = stages

    def run(self, context: GovernanceContext) -> GovernanceContext:
        """Run all stages in order.

        Stops early if a stage blocks the request. Re-raises PipelinePaused so
        the caller can persist state and resume later (used by the confirm
        gate). Every stage runs inside a guard so an unexpected error becomes a
        BLOCK rather than a silent pass-through -- secure by default.
        """
        for stage in self.stages:
            if context.decision == Decision.BLOCK:
                logger.info("Pipeline short-circuited before %s (request blocked).", stage.name)
                break
            try:
                context = stage.process(context)
            except PipelinePaused:
                raise
            except Exception as exc:  # noqa: BLE001 -- intentional catch-all
                # Fail closed: any unhandled error blocks the request.
                logger.exception("Stage %s raised; failing closed.", stage.name)
                context.block(f"stage '{stage.name}' errored: {type(exc).__name__}")
                context.record(stage.name, {"error": type(exc).__name__})
                break

        # If nothing blocked and we never set an explicit decision, the request
        # is cleared to proceed.
        if context.decision == Decision.PENDING:
            context.decision = Decision.ALLOW
        return context
