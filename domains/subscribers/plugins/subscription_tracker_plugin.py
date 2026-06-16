from core.base_plugin import BasePlugin


class SubscriptionTrackerPlugin(BasePlugin):
    """
    Tracks subscriptions, tier changes, and gift history.

    Events handled:
      channel.subscribe              → new sub / gift sub received
      channel.subscription.message  → resub (has cumulative_months + streak)
      channel.subscription.end      → sub expired / cancelled
      channel.subscription.gift     → gift batch (updates gifters table)
      channel.chat.notification     → detect Prime via sub_plan="Prime"

    Tables updated:
      subscribers        — current state
      subscription_events — full history from today onwards
      gifters            — cumulative gift count per user
    """

    def __init__(self, twitch, db, event_bus, logger):
        self.twitch = twitch
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.twitch.register("channel.subscribe", "1",
                             scopes=["channel:read:subscriptions"])
        self.twitch.register("channel.subscription.message", "1",
                             scopes=["channel:read:subscriptions"])
        self.twitch.register("channel.subscription.end", "1",
                             scopes=["channel:read:subscriptions"])
        self.twitch.register("channel.subscription.gift", "1",
                             scopes=["channel:read:subscriptions"])
        self.twitch.register(
            "channel.chat.notification", "1",
            scopes=["user:read:chat"],
            condition={
                "broadcaster_user_id": "{broadcaster_id}",
                "user_id": "{broadcaster_id}",
            },
        )

        self.twitch.on_event("channel.subscribe",             self._on_subscribe)
        self.twitch.on_event("channel.subscription.message", self._on_resub)
        self.twitch.on_event("channel.subscription.end",     self._on_sub_end)
        self.twitch.on_event("channel.subscription.gift",    self._on_sub_gift)
        self.twitch.on_event("channel.chat.notification",    self._on_chat_notification)

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _current_tier(self, twitch_id: str) -> str | None:
        rows = await self.db.query(
            "SELECT tier FROM subscribers WHERE twitch_id=$1", [twitch_id]
        )
        return rows[0]["tier"] if rows else None

    async def _log_event(self, **kwargs):
        await self.db.execute(
            """INSERT INTO subscription_events
                   (twitch_id, login, display_name, event_type, tier, previous_tier,
                    cumulative_months, streak_months, is_gift,
                    gifter_id, gifter_login, gifter_display_name)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
            [
                kwargs.get("twitch_id"),
                kwargs.get("login", ""),
                kwargs.get("display_name", ""),
                kwargs.get("event_type"),
                kwargs.get("tier"),
                kwargs.get("previous_tier"),
                kwargs.get("cumulative_months"),
                kwargs.get("streak_months"),
                1 if kwargs.get("is_gift") else 0,
                kwargs.get("gifter_id"),
                kwargs.get("gifter_login"),
                kwargs.get("gifter_display_name"),
            ],
        )

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_subscribe(self, event: dict):
        twitch_id    = event.get("user_id", "")
        login        = event.get("user_login", "")
        display_name = event.get("user_name", login)
        tier         = event.get("tier", "1000")
        is_gift      = bool(event.get("is_gift", False))
        gifter_id    = event.get("gifter_user_id") or None
        gifter_login = event.get("gifter_user_login") or None
        gifter_name  = event.get("gifter_user_name") or None

        if not twitch_id:
            return

        try:
            previous_tier = await self._current_tier(twitch_id)
            event_type = "tier_change" if (previous_tier and previous_tier != tier) else "subscribe"
            
            # Logic: If they sub at T2 or T3, they definitely aren't Prime anymore.
            # If it's T1, we don't clear is_prime yet (could be a false negative from a future sync).
            # The chat notification will set is_prime=1 if it truly is Prime.
            is_prime_update = ""
            if tier in ("2000", "3000"):
                is_prime_update = ", is_prime = 0"

            await self.db.execute(
                f"""INSERT INTO subscribers
                       (twitch_id, login, display_name, tier, is_gift, cumulative_months, is_active)
                   VALUES ($1,$2,$3,$4,$5,1,1)
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login        = excluded.login,
                       display_name = excluded.display_name,
                       tier         = excluded.tier,
                       is_gift      = excluded.is_gift,
                       is_active    = 1,
                       last_sub_at  = datetime('now')
                       {is_prime_update}""",
                [twitch_id, login, display_name, tier, 1 if is_gift else 0],
            )

            await self._log_event(
                twitch_id=twitch_id, login=login, display_name=display_name,
                event_type=event_type, tier=tier, previous_tier=previous_tier,
                cumulative_months=1, is_gift=is_gift,
                gifter_id=gifter_id, gifter_login=gifter_login, gifter_display_name=gifter_name,
            )

            await self.bus.publish("subscriber.new", {
                "twitch_id": twitch_id, "display_name": display_name,
                "tier": tier, "is_gift": is_gift,
            })
        except Exception as e:
            self.logger.error(f"[SubscriptionTracker] subscribe: {e}")

    async def _on_resub(self, event: dict):
        twitch_id         = event.get("user_id", "")
        login             = event.get("user_login", "")
        display_name      = event.get("user_name", login)
        tier              = event.get("tier", "1000")
        cumulative_months = event.get("cumulative_months", 1)
        streak_months     = event.get("streak_months")

        if not twitch_id:
            return

        try:
            previous_tier = await self._current_tier(twitch_id)
            event_type = "tier_change" if (previous_tier and previous_tier != tier) else "resub"

            # Logic: If they resub at T2 or T3, they aren't Prime.
            is_prime_update = ""
            if tier in ("2000", "3000"):
                is_prime_update = ", is_prime = 0"

            await self.db.execute(
                f"""INSERT INTO subscribers
                       (twitch_id, login, display_name, tier, cumulative_months, streak_months, is_active)
                   VALUES ($1,$2,$3,$4,$5,$6,1)
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login             = excluded.login,
                       display_name      = excluded.display_name,
                       tier              = excluded.tier,
                       cumulative_months = excluded.cumulative_months,
                       streak_months     = excluded.streak_months,
                       is_active         = 1,
                       last_sub_at       = datetime('now')
                       {is_prime_update}""",
                [twitch_id, login, display_name, tier, cumulative_months, streak_months],
            )

            await self._log_event(
                twitch_id=twitch_id, login=login, display_name=display_name,
                event_type=event_type, tier=tier, previous_tier=previous_tier,
                cumulative_months=cumulative_months, streak_months=streak_months,
            )

            await self.bus.publish("subscriber.resub", {
                "twitch_id": twitch_id, "display_name": display_name,
                "tier": tier, "cumulative_months": cumulative_months,
                "streak_months": streak_months,
            })
        except Exception as e:
            self.logger.error(f"[SubscriptionTracker] resub: {e}")

    async def _on_sub_end(self, event: dict):
        twitch_id    = event.get("user_id", "")
        login        = event.get("user_login", "")
        display_name = event.get("user_name", login)

        if not twitch_id:
            return

        try:
            rows = await self.db.query(
                "SELECT tier FROM subscribers WHERE twitch_id=$1", [twitch_id]
            )
            tier = rows[0]["tier"] if rows else None

            await self.db.execute(
                "UPDATE subscribers SET is_active=0 WHERE twitch_id=$1", [twitch_id]
            )
            await self._log_event(
                twitch_id=twitch_id, login=login, display_name=display_name,
                event_type="sub_end", tier=tier,
            )
            await self.bus.publish("subscriber.expired", {"twitch_id": twitch_id})
        except Exception as e:
            self.logger.error(f"[SubscriptionTracker] sub_end: {e}")

    async def _on_sub_gift(self, event: dict):
        gifter_id   = event.get("user_id", "")
        gifter_login = event.get("user_login", "")
        gifter_name  = event.get("user_name", gifter_login)
        total            = event.get("total", 1)
        cumulative_total = event.get("cumulative_total")  # Twitch gives all-time total

        # Anonymous gifts have no user_id
        if not gifter_id:
            return

        try:
            if cumulative_total is not None:
                # Use Twitch's cumulative total directly (most accurate)
                await self.db.execute(
                    """INSERT INTO gifters (twitch_id, login, display_name, gifts_total, last_gift_at)
                       VALUES ($1,$2,$3,$4,datetime('now'))
                       ON CONFLICT(twitch_id) DO UPDATE SET
                           login        = excluded.login,
                           display_name = excluded.display_name,
                           gifts_total  = excluded.gifts_total,
                           last_gift_at = excluded.last_gift_at""",
                    [gifter_id, gifter_login, gifter_name, cumulative_total],
                )
            else:
                await self.db.execute(
                    """INSERT INTO gifters (twitch_id, login, display_name, gifts_total, last_gift_at)
                       VALUES ($1,$2,$3,$4,datetime('now'))
                       ON CONFLICT(twitch_id) DO UPDATE SET
                           login        = excluded.login,
                           display_name = excluded.display_name,
                           gifts_total  = gifts_total + $4,
                           last_gift_at = datetime('now')""",
                    [gifter_id, gifter_login, gifter_name, total],
                )

            await self.bus.publish("subscriber.gift", {
                "gifter_id": gifter_id, "gifter_name": gifter_name,
                "total": total, "cumulative_total": cumulative_total,
            })
        except Exception as e:
            self.logger.error(f"[SubscriptionTracker] sub_gift: {e}")

    async def _on_chat_notification(self, event: dict):
        notice_type = event.get("notice_type", "")
        if notice_type not in ("sub", "resub"):
            return

        twitch_id = event.get("chatter_user_id", "")
        if not twitch_id:
            return

        sub_data = event.get("sub") or event.get("resub") or {}
        if sub_data.get("sub_plan") != "Prime":
            return

        try:
            await self.db.execute(
                "UPDATE subscribers SET is_prime=1 WHERE twitch_id=$1", [twitch_id]
            )
        except Exception as e:
            self.logger.error(f"[SubscriptionTracker] prime detect: {e}")
