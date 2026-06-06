"""The GovernanceLayer is the public facade. It wires the configured stages
into a pipeline, runs requests through it, and persists the audit trail.

Typical use:

    layer = GovernanceLayer.from_config("policy.yaml")
    result = layer.handle("Please email max.mustermann@example.com")
    print(result.decision, result.final_output)

The stage *order* is fixed and deliberate (detect -> analyze -> apply policy ->
confirm -> forward). Order matters for compliance, so it lives in code, not in
config. Config decides parameters and which optional stages are active.
"""

from __future__ import annotations

from .audit.repository import AuditRepository
from .config.loader import load_config
from .config.schema import GovernanceConfig
from .factories import build_audit_repository, build_provider
from .pipeline.context import Decision, GovernanceContext
from .pipeline.pipeline import Pipeline
from .pipeline.stage import PipelinePaused
from .stages.deterministic_pii import DeterministicPiiStage
from .stages.downstream_llm import DownstreamLlmStage
from .stages.human_confirm import HumanConfirmStage
from .stages.llm_analysis import LlmAnalysisStage
from .stages.pseudonymization import PseudonymizationStage


class GovernanceLayer:
    def __init__(self, config: GovernanceConfig):
        self.config = config
        self.audit: AuditRepository = build_audit_repository(config.setup.audit)

        trusted = build_provider(config.setup.trusted_provider)
        commercial = build_provider(config.setup.commercial_provider)

        # Fixed, auditable order.
        self.pipeline = Pipeline(
            [
                DeterministicPiiStage(),
                LlmAnalysisStage(trusted, config.policy),
                PseudonymizationStage(config.policy),
                HumanConfirmStage(config.setup.mode),
                DownstreamLlmStage(commercial, config.setup.commercial_system_prompt),
            ]
        )

    @classmethod
    def from_config(cls, path: str | None = None) -> "GovernanceLayer":
        return cls(load_config(path))

    def handle(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> GovernanceContext:
        """Run a request through the full pipeline and persist the audit record."""
        context = GovernanceContext(original_input=user_input)
        if conversation_history:
            context.conversation_history = list(conversation_history)
        try:
            context = self.pipeline.run(context)
        except PipelinePaused as paused:
            context = paused.context
            # Persist the paused state too -- the pause is an auditable event.
            self.audit.save(context)
            return context

        self.audit.save(context)
        return context

    def resume(
        self,
        context: GovernanceContext,
        approved: bool,
        edited_input: str | None = None,
    ) -> GovernanceContext:
        """Resume a paused (NEEDS_CONFIRM) request with a human decision.

        If *edited_input* is supplied and differs from the original, the full
        analysis pipeline re-runs on the new text and pauses again for a second
        human review before reaching the commercial LLM.
        """
        if approved:
            if edited_input is not None and edited_input.strip() != context.original_input.strip():
                # User revised their prompt — re-run the full analysis and pause
                # again so the human can review the re-analysed version.
                new_ctx = GovernanceContext(
                    request_id=context.request_id,
                    original_input=edited_input,
                )
                stages_by_name = {s.name: s for s in self.pipeline.stages}
                re_run = Pipeline([
                    stages_by_name["deterministic_pii_scan"],
                    stages_by_name["trusted_llm_analysis"],
                    stages_by_name["pseudonymization"],
                    stages_by_name["human_confirm_gate"],
                    stages_by_name["commercial_llm"],
                ])
                try:
                    context = re_run.run(new_ctx)
                except PipelinePaused as paused:
                    context = paused.context
                    self.audit.save(context)
                    return context
            else:
                context.decision = Decision.ALLOW
                remaining = Pipeline(
                    [s for s in self.pipeline.stages if s.name in {"human_confirm_gate", "commercial_llm"}]
                )
                context = remaining.run(context)
        else:
            context.block("rejected by human reviewer")
        self.audit.save(context)
        return context
