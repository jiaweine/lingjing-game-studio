from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PluginDescriptor:
    name: str
    capability: str
    version: str = "0.1"
    dependencies: tuple[str, ...] = ()
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginRegistry:
    """Capability registry, deliberately smaller than a general IoC framework but with lifecycle and dependency checks."""
    def __init__(self) -> None:
        self._plugins: dict[str, tuple[PluginDescriptor, Any]] = {}

    def mount(self, desc: PluginDescriptor, plugin: Any) -> None:
        missing = [d for d in desc.dependencies if d not in self._plugins]
        if missing:
            raise RuntimeError(f"Cannot mount {desc.name}; missing dependencies: {missing}")
        self._plugins[desc.name] = (desc, plugin)
        if hasattr(plugin, "on_mount"):
            plugin.on_mount(self)

    def unmount(self, name: str) -> None:
        if name not in self._plugins:
            return
        desc, plugin = self._plugins.pop(name)
        if hasattr(plugin, "on_unmount"):
            plugin.on_unmount()

    def get(self, name: str) -> Any:
        return self._plugins[name][1]

    def by_capability(self, capability: str) -> list[Any]:
        return [obj for desc, obj in self._plugins.values() if desc.enabled and desc.capability == capability]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": d.name, "capability": d.capability, "version": d.version, "dependencies": list(d.dependencies),
             "enabled": d.enabled, "metadata": d.metadata}
            for d, _ in self._plugins.values()
        ]
