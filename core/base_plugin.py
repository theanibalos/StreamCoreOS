from abc import ABC

class BasePlugin(ABC):

    # Registered identity ("<domain>.<ClassName>"), stamped by the Kernel at
    # instantiation. Infrastructure uses it to name this plugin consistently
    # (registry, derived consumer groups, traces). None outside the Kernel
    # (e.g. unit tests) — consumers must fall back gracefully.
    _identity: str | None = None

    async def on_boot(self):
        """
        Lifecycle hook: executed when the plugin is loaded.
        Register endpoints, event subscriptions, etc.
        """
        pass

    async def shutdown(self):
        """Optional cleanup hook."""
        pass
