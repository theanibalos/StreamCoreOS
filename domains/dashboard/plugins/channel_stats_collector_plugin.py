from microcoreos.base_plugin import BasePlugin


class ChannelStatsCollectorPlugin(BasePlugin):
    """
    Scheduled every 5 minutes. Calls Helix API for current viewer count
    and follower count, then persists a snapshot to channel_stats.

    Requires an active Twitch session (broadcaster_id + access_token in state tool).
    Skips silently if the stream is offline or no session is active.
    """

    def __init__(self, twitch, db, scheduler, event_bus, logger):
        self.twitch = twitch
        self.db = db
        self.scheduler = scheduler
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.scheduler.add_job(
            "*/5 * * * *",
            self._collect,
            job_id="dashboard_stats_collector",
        )

    async def _collect(self):
        session = self.twitch.get_session()
        if not session:
            return  # no active session
        broadcaster_id = session["broadcaster_id"]
        access_token = session["access_token"]

        try:
            # Viewer count from streams endpoint
            stream_data = await self.twitch.get(
                "/streams",
                params={"user_id": broadcaster_id},
                user_token=access_token,
            )
            streams = stream_data.get("data", [])
            viewer_count = streams[0].get("viewer_count", 0) if streams else 0

            # Follower count
            followers_data = await self.twitch.get(
                "/channels/followers",
                params={"broadcaster_id": broadcaster_id},
                user_token=access_token,
            )
            follower_count = followers_data.get("total", 0)

            await self.db.execute(
                "INSERT INTO channel_stats (viewer_count, follower_count) VALUES ($1, $2)",
                [viewer_count, follower_count],
            )
            await self.bus.publish("dashboard.stats.updated", {
                "viewer_count": viewer_count,
                "follower_count": follower_count,
            })
        except Exception as e:
            self.logger.error(f"[StatsCollector] Failed to collect stats: {e}")
