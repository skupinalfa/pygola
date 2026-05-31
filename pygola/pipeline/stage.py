"""Every step in the governance pipeline is a Stage. Keeping a single, narrow
interface is what makes the system extensible: adding a new compliance check
later means writing a new Stage and adding it to the config -- nothing else in
the system needs to change.

A Stage may:
  - enrich the context (e.g. add detected entities),
  - set a decision (e.g. block the request),
  - signal that it needs to pause (e.g. the human-confirm gate),
by raising PipelinePaused.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .context import GovernanceContext


class PipelinePaused(Exception):
    """Raised by a stage that cannot complete without external input.

    The canonical example is the human-confirmation gate: it pauses the run
    and the caller is expected to persist the context, obtain a decision, and
    resume later.
    """

    def __init__(self, context: GovernanceContext, reason: str):
        self.context = context
        self.reason = reason
        super().__init__(reason)


class Stage(ABC):
    """Base class for all pipeline stages."""

    #: Human-readable name used in the audit trail. Override in subclasses.
    name: str = "unnamed_stage"

    @abstractmethod
    def process(self, context: GovernanceContext) -> GovernanceContext:
        """Do this stage's work and return the (mutated) context.

        Implementations should call ``context.record(...)`` with a PII-free
        summary so the audit trail stays complete.
        """
        raise NotImplementedError
