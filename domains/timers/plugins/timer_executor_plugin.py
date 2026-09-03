import time
from datetime import datetime, timedelta, timezone
from microcoreos.base_plugin import BasePlugin

# Key for tracking chat line timestamps in the state tool
MSG_LOG_KEY = "chat_message_log"
NAMESPACE = "timers"

class TimerExecutorPlugin(BasePlugin):
    """
    Background executor for Timers.
    
    Logic:
    1. Tracks chat message timestamps in a 5-minute rolling window.
    2. Runs every minute via the scheduler.
    3. For each enabled timer, checks:
       - If 'interval_minutes' has passed since the last execution.
       - If at least 'min_lines' have been sent in the last 5 minutes.
    4. Sends the message to Twitch chat if conditions are met.
    """
    def __init__(self, scheduler, db, twitch, state, event_bus, logger):
        self.scheduler = scheduler
        self.db = db
        self.twitch = twitch
        self.state = state
        self.bus = event_bus
        self.logger = logger
        self.active_timers = []

    async def on_boot(self):
        # Listen for chat messages to track volume
        await self.bus.subscribe("chat.message.received", self._on_message)
        
        # Listen for timer changes to refresh local cache
        await self.bus.subscribe("timer.created", self._refresh_timers)
        await self.bus.subscribe("timer.updated", self._refresh_timers)
        await self.bus.subscribe("timer.deleted", self._refresh_timers)
        
        # Load initial timers
        await self._load_timers()
        
        # Schedule the check to run every minute
        self.scheduler.add_job("*/1 * * * *", self._check_timers, job_id="timer_executor")
        
        self.logger.info("[TimerExecutor] Started.")

    async def _load_timers(self):
        try:
            self.active_timers = await self.db.query(
                "SELECT * FROM timers WHERE enabled=1"
            )
        except Exception as e:
            self.logger.error(f"[TimerExecutor] Failed to load timers: {e}")

    async def _refresh_timers(self, event):
        await self._load_timers()

    async def _on_message(self, event):
        # Add current timestamp to the log
        now = time.time()
        msg_log = await self.state.get(MSG_LOG_KEY, default=[], namespace=NAMESPACE)
        msg_log.append(now)

        # Cleanup: only keep messages from the last 5 minutes
        five_min_ago = now - 300
        msg_log = [t for t in msg_log if t > five_min_ago]

        await self.state.set(MSG_LOG_KEY, msg_log, namespace=NAMESPACE)

    async def _check_timers(self):
        now_dt = datetime.now(timezone.utc)
        now_ts = now_dt.timestamp()

        # Get message log and cleanup
        msg_log = await self.state.get(MSG_LOG_KEY, default=[], namespace=NAMESPACE)
        five_min_ago = now_ts - 300
        msg_log = [t for t in msg_log if t > five_min_ago]
        await self.state.set(MSG_LOG_KEY, msg_log, namespace=NAMESPACE)
        
        for timer in self.active_timers:
            last_exec = timer.get("last_executed_at")
            interval = timer["interval_minutes"]
            min_lines = timer["min_lines"]
            
            last_exec_dt = None
            if last_exec and isinstance(last_exec, str):
                try:
                    # Parse SQLite datetime string and treat as UTC
                    last_exec_dt = datetime.fromisoformat(last_exec.replace(" ", "T")).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            # Condition 1: Time interval
            if last_exec_dt:
                next_run = last_exec_dt + timedelta(minutes=interval)
                if now_dt < next_run:
                    continue
            
            # Condition 2: Minimum lines AFTER last execution (and within last 5 min)
            last_exec_ts = last_exec_dt.timestamp() if last_exec_dt else 0
            eligible_lines = [t for t in msg_log if t > last_exec_ts]
            
            if len(eligible_lines) < min_lines:
                self.logger.info(f"[TimerExecutor] Timer '{timer['name']}' skipped: {len(eligible_lines)}/{min_lines} lines since last post.")
                continue
                
            # Execute!
            await self._trigger_timer(timer)

    async def _trigger_timer(self, timer: dict):
        try:
            session = self.twitch.get_session()
            if not session:
                return 
                
            self.logger.info(f"[TimerExecutor] Triggering timer: {timer['name']}")
            
            # Route message to broadcaster channel through platform sender plugins.
            await self.bus.publish("chat.message.send", {
                "platform": "twitch",
                "channel_id": session["broadcaster_id"],
                "channel_name": session["login"],
                "message": timer["message"],
            })
            
            # Update last_executed_at in DB
            await self.db.execute(
                "UPDATE timers SET last_executed_at=datetime('now') WHERE id=$1",
                [timer["id"]]
            )
            
            # Refresh local cache for this timer's state
            await self._load_timers()
            
        except Exception as e:
            self.logger.error(f"[TimerExecutor] Failed to trigger timer {timer['name']}: {e}")
