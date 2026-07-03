from core.base_plugin import BasePlugin


class StreamStatusPlugin(BasePlugin):
    """
    Listens to Twitch stream.online and stream.offline events.
    Also polls Twitch API periodically to ensure state consistency.

    On stream.online:
      - Saves a new session to DB.
      - Updates the 'stream_state' namespace in the state tool.
      - Publishes stream.session.started to the event_bus.

    On stream.offline:
      - Closes the current session in DB.
      - Clears the state tool.
      - Publishes stream.session.ended to the event_bus.
    """

    def __init__(self, twitch, db, state, event_bus, logger, scheduler):
        self.twitch = twitch
        self.db = db
        self.state = state
        self.bus = event_bus
        self.logger = logger
        self.scheduler = scheduler

    async def on_boot(self):
        # Register EventSub subscriptions (no scopes needed for stream events)
        self.twitch.register(
            "stream.online",
            version="1",
            scopes=[],
            condition={"broadcaster_user_id": "{broadcaster_id}"},
        )
        self.twitch.register(
            "stream.offline",
            version="1",
            scopes=[],
            condition={"broadcaster_user_id": "{broadcaster_id}"},
        )

        # Register callbacks
        self.twitch.on_event("stream.online", self._on_stream_online)
        self.twitch.on_event("stream.offline", self._on_stream_offline)

        # Schedule periodic sync (every minute) to handle missed events or initial boot
        self.scheduler.add_job("*/1 * * * *", self.sync_current_status, job_id="stream_status_sync")

    async def sync_current_status(self):
        """Polls Twitch Helix API to check if the stream is currently live."""
        session = self.twitch.get_session()
        if not session:
            return

        try:
            broadcaster_id = session["broadcaster_id"]
            access_token = session["access_token"]

            # GET /streams?user_id=... returns a list (empty if offline)
            response = await self.twitch.get(
                "/streams",
                params={"user_id": broadcaster_id},
                user_token=access_token
            )

            streams = response.get("data", [])
            is_live = len(streams) > 0
            currently_in_state = await self.state.get("online", default=False, namespace="stream_state")

            if is_live and not currently_in_state:
                # Stream is live but we thought it was offline (reboot or missed event)
                stream_data = streams[0]
                await self._on_stream_online({
                    "id": stream_data["id"],
                    "started_at": stream_data["started_at"],
                    "broadcaster_user_login": session["login"]
                })
            elif not is_live and currently_in_state:
                # Stream is offline but we thought it was live
                await self._on_stream_offline({})

        except Exception as e:
            self.logger.error(f"[StreamState] Failed to sync status: {e}")

    async def _on_stream_online(self, event: dict):
        try:
            twitch_stream_id = event.get("id")
            started_at = event.get("started_at")
            broadcaster_login = event.get("broadcaster_user_login", "")

            # Avoid duplicate sessions if already online in state
            if await self.state.get("online", default=False, namespace="stream_state"):
                return

            session_id = await self.db.execute(
                """INSERT INTO stream_sessions (twitch_stream_id, started_at)
                   VALUES ($1, $2) RETURNING id""",
                [twitch_stream_id, started_at],
            )

            # Update in-memory state
            await self.state.set("online", True, namespace="stream_state")
            await self.state.set("session_id", session_id, namespace="stream_state")
            await self.state.set("started_at", started_at, namespace="stream_state")
            await self.state.set("broadcaster_login", broadcaster_login, namespace="stream_state")
            await self.state.set("ended_at", None, namespace="stream_state")

            await self.bus.publish("stream.session.started", {
                "session_id": session_id,
                "twitch_stream_id": twitch_stream_id,
                "started_at": started_at,
                "broadcaster_login": broadcaster_login,
            })
            self.logger.info(f"[StreamState] Stream online — session_id={session_id}")
        except Exception as e:
            self.logger.error(f"[StreamState] Error handling stream.online: {e}")

    async def _on_stream_offline(self, event: dict):
        try:
            from datetime import datetime, timezone
            ended_at = datetime.now(timezone.utc).isoformat()

            session_id = await self.state.get("session_id", namespace="stream_state")
            if session_id:
                await self.db.execute(
                    "UPDATE stream_sessions SET ended_at=$1 WHERE id=$2",
                    [ended_at, session_id],
                )

            # Clear in-memory state
            await self.state.set("online", False, namespace="stream_state")
            await self.state.set("session_id", None, namespace="stream_state")
            await self.state.set("ended_at", ended_at, namespace="stream_state")
            await self.state.set("started_at", None, namespace="stream_state")
            await self.state.set("broadcaster_login", None, namespace="stream_state")

            await self.bus.publish("stream.session.ended", {
                "session_id": session_id,
                "ended_at": ended_at,
            })
            self.logger.info(f"[StreamState] Stream offline — session_id={session_id}")
        except Exception as e:
            self.logger.error(f"[StreamState] Error handling stream.offline: {e}")
