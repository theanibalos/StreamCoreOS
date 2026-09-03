import os
from microcoreos.base_tool import BaseTool


class ChaosTool(BaseTool):
    """
    Chaos Engineering Tool — intentionally fails during boot.

    Purpose: verifies that the Kernel survives tool failures gracefully.
    This tool is a test fixture for kernel resilience, not production infrastructure.

    Enabled by setting CHAOS_ENABLED=true in the environment.
    When disabled (default), this tool is a silent no-op.
    """

    @property
    def name(self) -> str:
        return "chaos"

    async def setup(self) -> None:
        if os.getenv("CHAOS_ENABLED", "false").lower() == "true":
            print("[ChaosTool] 🔥 Chaos mode active. Exploding in 3... 2... 1...")
            raise RuntimeError("BOOM! Intentional tool failure (CHAOS_ENABLED=true).")
        # Silent no-op in production — tool registers but does nothing

    def get_interface_description(self) -> str:
        return """
        Chaos Engineering Tool (chaos):
        - PURPOSE: Intentionally fails during boot to verify Kernel fault tolerance.
        - Enabled by setting CHAOS_ENABLED=true in the environment.
        - No capabilities exposed to plugins.
        """
