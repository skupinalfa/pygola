"""Runnable demo. No API key needed -- uses the mock provider.

Run:  python example.py

To use real providers, copy .env.example to .env, fill in your keys, and
install python-dotenv:  pip install governance-layer[dotenv]
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; set env vars manually before running

from pygola import GovernanceLayer, GovernanceConfig
from pygola.config.schema import Mode, AuditConfig, SetupConfig, PolicyConfig


def divider(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# --- 1. AUTO mode, in-memory audit so the demo leaves no files ----------
divider("AUTO MODE")

config = GovernanceConfig(
    setup=SetupConfig(mode=Mode.AUTO, audit=AuditConfig(backend="memory")),
    policy=PolicyConfig(),
)
layer = GovernanceLayer(config)

result = layer.handle(
    "Hi, please contact Marian Szucowski at marian.slayer@example.com "
    "or call 01701234567 about the project."
)

print("Decision:        ", result.decision.value)
print("Original input:  ", result.original_input)
print("Sanitized input: ", result.sanitized_input)
print("Final output:    ", result.final_output)
print("Entities found:  ", [(e.entity_type, e.placeholder) for e in result.entities])
print("Block reasons:   ", result.block_reasons)


# --- 2. A request that must be blocked (IBAN) ---------------------------
divider("BLOCK CASE (IBAN in policy = block)")

result2 = layer.handle("Transfer to my account DE89370400440532013000 please.")
print("Decision:      ", result2.decision.value)
print("Block reasons: ", result2.block_reasons)


# --- 3. CONFIRM mode: pipeline pauses before forwarding -----------------
divider("CONFIRM MODE")

confirm_layer = GovernanceLayer(
    GovernanceConfig(
        setup=SetupConfig(mode=Mode.CONFIRM, audit=AuditConfig(backend="memory")),
        policy=PolicyConfig(),
    )
)

paused = confirm_layer.handle("Email john.doe@example.com the summary.")
print("After handle(): ", paused.decision.value)

resumed = confirm_layer.resume(paused, approved=True)
print("After approve:  ", resumed.decision.value)
print("Final output:   ", resumed.final_output)


# --- 4. Show the audit record (PII-free) --------------------------------
divider("AUDIT RECORD (note: no PII, only hash + types)")

from pygola.audit.repository import to_audit_record
import json

print(json.dumps(to_audit_record(result), indent=2, ensure_ascii=False))


# --- 5. Streaming example (mock provider supports streaming) -------------
divider("STREAMING (mock provider)")

from pygola.config.schema import ProviderConfig
from pygola.factories import build_provider

mock_provider = build_provider(ProviderConfig(kind="mock", model="mock-model"))

if mock_provider.supports_streaming:
    print("Tokens: ", end="", flush=True)
    for token in mock_provider.streaming_complete("Hello, world!"):
        print(token, end="", flush=True)
    print()
else:
    print(f"Provider '{mock_provider.name}' does not support streaming.")

# To stream from a local LLM (requires Ollama running):
#
#   from pygola.config.schema import ProviderConfig
#   from pygola.factories import build_provider
#
#   local = build_provider(ProviderConfig(kind="local", model="llama3"))
#   if local.supports_streaming:
#       for token in local.streaming_complete("Explain PII in one sentence."):
#           print(token, end="", flush=True)
#   print()
