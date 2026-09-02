from core.base_plugin import BasePlugin


_FOLLOW_MSG = "Thanks for the follow, {user}! Welcome to the community PogChamp"
_SUB_MSG = "Thanks for subscribing, {user}! Welcome to the sub club KomodoHype"
_RESUB_MSG = "Thanks for resubbing {months} months, {user}! KomodoHype"
_GIFT_MSG = "{gifter} just gifted {count} sub(s) to the community! PogChamp"
_RAID_MSG = "Welcome raiders from {from_channel}! {viewers} viewers joining us PogChamp"


class ChatAutoResponsePlugin(BasePlugin):
    """
    Sends automatic chat messages in response to Twitch events.

    Registers for: follow, subscribe, resub, gift sub, raid.
    Messages are configurable via class-level constants (easily extensible
    to DB-backed config in the future).
    """

    def __init__(self, twitch, event_bus, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.twitch.register(
            "channel.follow", "2",
            scopes=["moderator:read:followers"],
            condition={
                "broadcaster_user_id": "{broadcaster_id}",
                "moderator_user_id": "{broadcaster_id}",
            },
        )
        self.twitch.register(
            "channel.subscribe", "1",
            scopes=["channel:read:subscriptions"],
        )
        self.twitch.register(
            "channel.subscription.message", "1",
            scopes=["channel:read:subscriptions"],
        )
        self.twitch.register(
            "channel.subscription.gift", "1",
            scopes=["channel:read:subscriptions"],
        )
        self.twitch.register(
            "channel.raid", "1",
            scopes=[],
            condition={"to_broadcaster_user_id": "{broadcaster_id}"},
        )

        self.twitch.on_event("channel.follow", self._on_follow)
        self.twitch.on_event("channel.subscribe", self._on_subscribe)
        self.twitch.on_event("channel.subscription.message", self._on_resub)
        self.twitch.on_event("channel.subscription.gift", self._on_gift)
        self.twitch.on_event("channel.raid", self._on_raid)

    async def _send(self, message: str):
        try:
            session = self.twitch.get_session()
            if not session:
                return
            await self.bus.publish("chat.message.send", {
                "platform": "twitch",
                "channel_id": session["broadcaster_id"],
                "channel_name": session["login"],
                "message": message,
            })
        except Exception as e:
            self.logger.error(f"[AutoResponse] Failed to send message: {e}")

    async def _on_follow(self, event: dict):
        user = event.get("user_name", event.get("user_login", "someone"))
        await self._send(_FOLLOW_MSG.replace("{user}", user))

    async def _on_subscribe(self, event: dict):
        user = event.get("user_name", event.get("user_login", "someone"))
        await self._send(_SUB_MSG.replace("{user}", user))

    async def _on_resub(self, event: dict):
        user = event.get("user_name", event.get("user_login", "someone"))
        months = str(event.get("cumulative_months", 1))
        msg = _RESUB_MSG.replace("{user}", user).replace("{months}", months)
        await self._send(msg)

    async def _on_gift(self, event: dict):
        gifter = event.get("user_name", event.get("user_login", "Anonymous"))
        count = str(event.get("total", 1))
        msg = _GIFT_MSG.replace("{gifter}", gifter).replace("{count}", count)
        await self._send(msg)

    async def _on_raid(self, event: dict):
        from_channel = event.get("from_broadcaster_user_name", "someone")
        viewers = str(event.get("viewers", 0))
        msg = _RAID_MSG.replace("{from_channel}", from_channel).replace("{viewers}", viewers)
        await self._send(msg)
