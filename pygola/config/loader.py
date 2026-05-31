"""Loads a config file (YAML or JSON) and validates it against the schema.
Invalid configs raise immediately -- a tool like this must never start with a
half-broken policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import GovernanceConfig


def load_config(path: str | Path | None = None) -> GovernanceConfig:
    """Load config from a file, or return validated defaults if no path given.

    YAML is supported if PyYAML is installed; JSON always works.
    """
    if path is None:
        return GovernanceConfig()

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any]
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "PyYAML is required to load YAML configs. Install it or use JSON."
            ) from exc
        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw)

    # Pydantic does the validation and will raise on bad input.
    return GovernanceConfig.model_validate(data)
