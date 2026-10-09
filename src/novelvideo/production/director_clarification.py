"""Compatibility exports for the creative-execution admission policy.

New code imports :mod:`novelvideo.creative_execution.director_clarification`.  Keep this
module for existing callers while the production package stops owning a policy
that is also enforced by Workflow, Canvas, and Hermes.
"""

from novelvideo.creative_execution.director_clarification import (
    DIRECTOR_CLARIFICATION_SCHEMA,
    DirectorClarificationRequiredError,
    assess_director_clarification,
    require_director_clarification_ready,
)

__all__ = [
    "DIRECTOR_CLARIFICATION_SCHEMA",
    "DirectorClarificationRequiredError",
    "assess_director_clarification",
    "require_director_clarification_ready",
]
