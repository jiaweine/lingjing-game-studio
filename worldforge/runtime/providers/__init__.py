"""Runtime provider integrations.

Providers adapt Lingjing execution semantics to concrete game runtimes.

The core runtime depends only on the provider contract, never on engine
specific implementation details.
"""

from .base import RuntimeProvider
from .synthetic import SyntheticWorldForgeProvider
from .unreal import UnrealRuntimeProvider

__all__ = [
    "RuntimeProvider",
    "SyntheticWorldForgeProvider",
    "UnrealRuntimeProvider",
]
