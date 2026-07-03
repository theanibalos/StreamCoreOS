import asyncio
import pytest
from tools.event_bus.event_bus_tool import EventBusTool, EventEnvelope

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend(): return "asyncio"

@pytest.fixture
async def bus():
    b = EventBusTool()
    await b.setup()
    yield b
    await b.shutdown()

async def test_consumer_groups_load_balancing(bus):
    """
    Verifies that when two subscribers share the same group,
    only one receives each message (round-robin).
    Uses disjoint sets to verify workload distribution without assuming order.
    """
    received_a = set()
    received_b = set()

    async def handler_a(event: EventEnvelope): received_a.add(event.payload["i"])
    async def handler_b(event: EventEnvelope): received_b.add(event.payload["i"])

    await bus.subscribe("job.new", handler_a, group="workers")
    await bus.subscribe("job.new", handler_b, group="workers")

    # Enviamos 4 mensajes
    for i in range(4):
        await bus.publish("job.new", {"i": i})
    
    await asyncio.sleep(0.1)

    # Cada uno debe haber recibido 2
    assert len(received_a) == 2
    assert len(received_b) == 2
    
    # Verify the sets are disjoint (no one received what the other did)
    assert received_a.isdisjoint(received_b)
    # Verificamos que entre ambos tienen todos los mensajes
    assert received_a | received_b == {0, 1, 2, 3}

async def test_different_groups_receive_all(bus):
    """
    Verifies that when subscribers are in different groups,
    both receive the message (fan-out).
    """
    received_a = []
    received_b = []

    async def handler_a(event: EventEnvelope): received_a.append(event.payload)
    async def handler_b(event: EventEnvelope): received_b.append(event.payload)

    await bus.subscribe("event", handler_a, group="group-1")
    await bus.subscribe("event", handler_b, group="group-2")

    await bus.publish("event", {"msg": "hello"})
    await asyncio.sleep(0.1)

    assert len(received_a) == 1
    assert len(received_b) == 1
