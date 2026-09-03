import sys
import signal
import asyncio
from dotenv import load_dotenv
from microcoreos.kernel import Kernel

async def _main():
    load_dotenv()
    stop_event = asyncio.Event()
    app = Kernel()

    def stop_signal_handler():
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_signal_handler)

    try:
        await app.boot()
        print("\n🚀 [MicroCoreOS] System Online. (Ctrl+C to exit)")
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.shutdown()
        print("[MicroCoreOS] Shutdown complete. See you soon!")

def main():
    try:
        asyncio.run(_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass

if __name__ == "__main__":
    main()
