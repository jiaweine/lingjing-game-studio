"""Runtime provider integrations.

Providers adapt Lingjing execution semantics to concrete game runtimes.

Current providers may be synthetic or real-game implementations. The core
runtime should depend only on the provider contract, never on engine details.
"""

from .base import RuntimeProvider
from .synthetic import SyntheticWorldForgeProvider

__all__ = ["RuntimeProvider", "SyntheticWorldForgeProvider"]
