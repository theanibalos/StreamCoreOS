from abc import ABC, abstractmethod


class ToolUnavailableError(Exception):
    """
    Contract (like BaseTool itself): marker for infrastructure failures — the
    tool's backing service is unreachable (connection refused, network down,
    auth to the backend failed, ...).

    A tool raises it (or makes its own connection-error class subclass it) to
    tell ToolProxy "this is NOT a business error — mark me DEAD immediately".
    Each tool defines its OWN exceptions in its OWN file; nothing is registered
    here.

    Exceptions that do NOT derive from this class (constraint violations, bad
    input, expired tokens, ...) never mark a tool DEAD on their own; only a
    sustained streak of consecutive failures does (see ToolProxy.DEAD_THRESHOLD).
    """


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def setup(self):
        pass

    @abstractmethod
    def get_interface_description(self) -> str:
        pass

    async def on_boot_complete(self, container):
        """Optional hook: executed when everything is loaded."""
        pass

    async def on_instrument(self, tracer_provider) -> None:
        """Optional hook: called by an observability tool to instrument this tool's
        underlying framework. Override to add driver-level spans specific to it.
        Runs on the raw tool instance, bypassing ToolProxy, so failures won't mark the tool DEAD.
        """
        pass

    async def shutdown(self):
        """Optional: Resource cleanup (close DB, stop server)"""
        pass