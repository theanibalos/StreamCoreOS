import os
import httpx
from core.base_plugin import BasePlugin

class HackThePlanetPlugin(BasePlugin):
    """
    Listens for Twitch channel point redemptions and triggers a Home Assistant webhook
    when 'Hack the Planet' is redeemed.

    Reads HOME_ASSISTANT_WEBHOOK from the environment (.env).
    """
    def __init__(self, twitch, logger):
        self.twitch = twitch
        self.logger = logger
        self.webhook_url = os.getenv("HOME_ASSISTANT_WEBHOOK", "")

    async def on_boot(self):
        # Register the redemption event with TwitchTool
        # Required scope: channel:read:redemptions
        # Condition defaults to {"broadcaster_user_id": "{broadcaster_id}"} in TwitchTool
        self.twitch.register(
            "channel.channel_points_custom_reward_redemption.add",
            "1",
            ["channel:read:redemptions"]
        )
        
        # Subscribe to the event callback
        self.twitch.on_event(
            "channel.channel_points_custom_reward_redemption.add",
            self._on_redemption
        )
        
        self.logger.info("[HackThePlanet] Plugin initialized and registered with Twitch.")

    async def _on_redemption(self, event: dict):
        # The event structure for channel.channel_points_custom_reward_redemption.add
        # contains 'reward', 'user_id', 'user_name', 'user_input', etc.
        reward = event.get("reward", {})
        title = reward.get("title", "")
        
        if title == "Hack the Planet":
            if not self.webhook_url:
                self.logger.warning("[HackThePlanet] HOME_ASSISTANT_WEBHOOK not set in .env — skipping.")
                return
            self.logger.info(f"[HackThePlanet] Reward '{title}' detected from {event.get('user_name')}! Triggering HA webhook...")
            
            payload = {
                "user_name": event.get("user_name"),
                "user_id": event.get("user_id"),
                "reward_id": reward.get("id"),
                "user_input": event.get("user_input", ""),
                "redeemed_at": event.get("redeemed_at")
            }
            
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.webhook_url,
                        json=payload,
                        timeout=5.0
                    )
                    
                    if response.status_code < 300:
                        self.logger.info("[HackThePlanet] HA Webhook triggered successfully.")
                    else:
                        self.logger.error(f"[HackThePlanet] HA Webhook failed with status {response.status_code}: {response.text}")
            except Exception as e:
                self.logger.error(f"[HackThePlanet] Failed to trigger HA webhook: {e}")
