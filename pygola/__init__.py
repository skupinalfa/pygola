"""governance_layer -- a configurable AI governance layer.

Public API kept intentionally small. Most users only need:

    from governance_layer import GovernanceLayer
    layer = GovernanceLayer.from_config("policy.yaml")
    result = layer.handle("...")

Advanced users can import the config models, the Stage base class (to write
their own compliance checks), and the context/decision types.
"""

from .config.loader import load_config
from .config.schema import (
    EntityHandling,
    EntityRule,
    GovernanceConfig,
    Mode,
    PolicyConfig,
    SetupConfig,
)
from .layer import GovernanceLayer
from .pipeline.context import Decision, DetectedEntity, GovernanceContext
from .pipeline.stage import PipelinePaused, Stage

__all__ = [
    "GovernanceLayer",
    "GovernanceConfig",
    "PolicyConfig",
    "SetupConfig",
    "EntityRule",
    "EntityHandling",
    "Mode",
    "Decision",
    "DetectedEntity",
    "GovernanceContext",
    "Stage",
    "PipelinePaused",
    "load_config",
    "load_config",
]

__version__ = "0.1.0"
