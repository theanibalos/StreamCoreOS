import json
from core.base_plugin import BasePlugin


class RestoreAIConfigPlugin(BasePlugin):
    """
    Reads the saved AI config from DB on boot and pushes it into AITool.
    This is the only bridge DB → AITool, keeping the tool DB-free.

    ai_config holds chat-personality fields + active_provider_id; the actual
    connection settings live in ai_providers. Both are joined and merged here.
    extra_headers and extra_payload are stored as JSON strings in SQLite
    and deserialized here before passing to load_config().
    """

    def __init__(self, db, ai, logger):
        self.db = db
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        try:
            chat_row = await self.db.query_one("SELECT * FROM ai_config WHERE id = 1")
            active_id = chat_row.get("active_provider_id") if chat_row else None

            if not active_id:
                self.logger.info("[AIConfig] No active provider — AI tool unconfigured.")
                return

            provider_row = await self.db.query_one(
                "SELECT * FROM ai_providers WHERE id = $1", [active_id]
            )
            if not provider_row:
                self.logger.info("[AIConfig] active_provider_id points to a missing provider — AI tool unconfigured.")
                return

            config = dict(provider_row)
            config.update({
                "chat_cooldown_s":    chat_row.get("chat_cooldown_s", 120),
                "chat_system_prompt": chat_row.get("chat_system_prompt", ""),
                "chat_max_tokens":    chat_row.get("chat_max_tokens", 200),
                "chat_temperature":   chat_row.get("chat_temperature", 0.7),
            })

            # Deserialize JSON string fields back to dicts
            for field in ("extra_headers", "extra_payload"):
                val = config.get(field)
                if isinstance(val, str):
                    try:
                        config[field] = json.loads(val)
                    except Exception:
                        config[field] = {}

            self.ai.load_config(config)
            self.logger.info(f"[AIConfig] Config restored from DB — provider={config.get('provider')}")
        except Exception:
            # Normal on first boot — tables don't exist until migrations run.
            self.logger.info("[AIConfig] No config to restore (first boot or tables pending).")
