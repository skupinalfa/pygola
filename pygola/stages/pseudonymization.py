"""Pseudonymization stage. This is where the policy is actually applied to the
findings, and where the sanitized (forwardable) text is built.

For each detected entity, the policy says: BLOCK, PSEUDONYMIZE, or ALLOW.
  - BLOCK  -> the whole request is blocked (fail closed).
  - PSEUDONYMIZE -> replace the span with a stable placeholder like [PERSON_1]
    and remember the mapping so we can restore it in the response.
  - ALLOW  -> leave untouched.

We replace from the end of the string backwards so earlier offsets stay valid
while we mutate the text.
"""

from __future__ import annotations

from ..config.schema import EntityHandling, PolicyConfig
from ..pipeline.context import GovernanceContext
from ..pipeline.stage import Stage


class PseudonymizationStage(Stage):
    name = "pseudonymization"

    def __init__(self, policy: PolicyConfig):
        self.policy = policy

    def process(self, context: GovernanceContext) -> GovernanceContext:
        text = context.original_input
        # Sort by start offset descending so replacements don't shift others.
        entities = sorted(context.entities, key=lambda e: e.start, reverse=True)

        counters: dict[str, int] = {}
        actions: dict[str, int] = {"pseudonymized": 0, "blocked": 0, "allowed": 0}

        for entity in entities:
            rule = self.policy.rule_for(entity.entity_type)
            if entity.score < rule.min_score:
                continue

            if rule.handling == EntityHandling.BLOCK:
                context.block(f"policy blocks entity type '{entity.entity_type}'")
                actions["blocked"] += 1
                continue

            if rule.handling == EntityHandling.ALLOW:
                actions["allowed"] += 1
                continue

            # PSEUDONYMIZE
            n = counters.get(entity.entity_type, 0) + 1
            counters[entity.entity_type] = n
            placeholder = f"[{entity.entity_type}_{n}]"
            entity.placeholder = placeholder
            real_value = text[entity.start : entity.end]
            context.mapping[placeholder] = real_value
            text = text[: entity.start] + placeholder + text[entity.end :]
            actions["pseudonymized"] += 1

        context.sanitized_input = text
        context.record(
            self.name,
            {
                "actions": actions,
                "placeholders_created": len(context.mapping),
            },
        )
        return context
