"""Onboarding assist: cheaper adoption without touching the topology model."""

from .apply import apply_capabilities
from .openai_adapter import from_openai_specs
from .proposals import (
    CapabilityProposal,
    CapabilityProposalSet,
    DroppedProposal,
    propose_capabilities,
    render_capability_diff,
)

__all__ = [
    "CapabilityProposal",
    "CapabilityProposalSet",
    "DroppedProposal",
    "apply_capabilities",
    "from_openai_specs",
    "propose_capabilities",
    "render_capability_diff",
]
