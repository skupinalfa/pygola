# governance_layer

A configurable AI governance layer (skeleton / v0.1).

It sits between your application and a commercial LLM and ensures requests are
made compliant before they leave your trust boundary. The first use case is
**PII detection and pseudonymization**, but the design is built to grow into
broader compliance.

## Core idea

A request flows through a fixed, auditable pipeline of stages:

```
input
  -> deterministic_pii_scan   (reproducible regex detection -- the auditable floor)
  -> trusted_llm_analysis     (a TRUSTED model catches contextual PII; can only ADD findings)
  -> pseudonymization         (apply policy: block or replace with [PLACEHOLDER]; keep reversible mapping)
  -> human_confirm_gate       (CONFIRM mode only: pause for a human before forwarding)
  -> commercial_llm           (forward sanitized text; restore real values in the answer)
output
```

Every stage writes a **PII-free** record to the audit trail.

## Design principles

- **Two-layer detection.** Deterministic rules are the authoritative, reproducible
  floor. The trusted LLM augments recall but can never relax the policy.
- **Pseudonymize, don't just delete.** A reversible placeholder mapping (held in
  memory only) keeps requests usable and makes the output path easy later.
- **Control flow stays in code.** This is a deterministic pipeline with an optional
  human-in-the-loop gate, not an autonomous agent. Predictability over autonomy.
- **Secure by default.** Unhandled errors fail closed (block). Unknown entity types
  default to block.
- **The config is the policy.** Policy lives in a versioned YAML/JSON file, separate
  from technical setup, so it can serve as compliance documentation.
- **PII never enters the audit log.** Only hashes, lengths, type counts, and decisions.

## Quick start

```python
from governance_layer import GovernanceLayer

layer = GovernanceLayer.from_config("policy.example.yaml")
result = layer.handle("Email max.mustermann@example.com the report.")

print(result.decision.value)     # allow / block / needs_confirm
print(result.sanitized_input)    # what the commercial LLM would see
print(result.final_output)       # answer with real values restored
```

Run the full demo (no API key needed -- uses the mock provider):

```bash
PYTHONPATH=. python example.py
```

## Where to extend

| You want to...                         | Do this                                                            |
|----------------------------------------|--------------------------------------------------------------------|
| Add a real LLM                         | Implement `LLMProvider` in `providers/`, wire it in `factories.py` |
| Improve PII detection                  | Replace `DeterministicPiiStage` with a Presidio-backed stage       |
| Add a new compliance check             | Write a `Stage` subclass, insert it into the pipeline in `layer.py`|
| Store audit logs in a database         | Implement `AuditRepository`, add it to `build_audit_repository`    |
| Filter the LLM's *response*            | Add a stage after `commercial_llm`                                 |

## Layout

```
governance_layer/
├── config/      # Pydantic schema + loader (validated, fail-fast)
├── pipeline/    # context object, Stage interface, orchestrator
├── stages/      # the concrete steps
├── providers/   # LLM interface + mock
├── audit/       # repository interface + json/memory backends
├── factories.py # build providers & audit repo from config
└── layer.py     # GovernanceLayer facade (public entry point)
```

## Status & next steps

This is a working skeleton with a mock LLM. Natural next steps:
1. Plug in a real trusted provider (local model) and a commercial one.
2. Swap the regex stage for Microsoft Presidio.
3. Harden the audit store (write-once / hash-chaining) for regulator-grade logs.
4. Add the output-filtering stage.
5. Once a stage's behavior is settled, write a Spec-Kit spec per stage --
   the module boundaries are already drawn for it.
