"""Generate A2UI v1.0 with any language model, and only ship UI that passed."""

from .generate import Result, generate, system_prompt, with_data
from .llm import Chain, LlmError, chain, connect
from .repair import repair
from .validate import Issue, Report, basic_catalog, validate

__all__ = [
    "Chain",
    "Issue",
    "LlmError",
    "Report",
    "Result",
    "basic_catalog",
    "chain",
    "connect",
    "generate",
    "repair",
    "system_prompt",
    "validate",
    "with_data",
]
__version__ = "1.0.0"
