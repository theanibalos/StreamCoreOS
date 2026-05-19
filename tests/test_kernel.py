import pytest
import asyncio
from core.kernel import Kernel
from core.base_plugin import BasePlugin
from core.base_tool import BaseTool

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return "asyncio"

class DummyTool(BaseTool):
    @property
    def name(self) -> str:
        return "dummy"
    
    async def setup(self):
        # Simulamos un proceso asíncrono para verificar paralelismo
        await asyncio.sleep(0.01)
        self.ready = True
        
    def get_interface_description(self) -> str:
        return "Dummy Tool"
        
    async def shutdown(self):
        pass
        
    async def on_boot_complete(self, container):
        self.boot_completed = True

class DummyPlugin(BasePlugin):
    def __init__(self, dummy: DummyTool):
        self.dummy = dummy
        self.booted = False
        
    async def on_boot(self):
        await asyncio.sleep(0.01)
        self.booted = True

@pytest.fixture
def kernel():
    return Kernel()

# ─── 1. Pruebas de Dependency Injection ──────────────────────────────────────

def test_resolve_dependencies_success(kernel):
    # Simulamos que el container ya tiene el tool cargado
    dummy_instance = DummyTool()
    kernel.container.register(dummy_instance)
    
    deps, missing = kernel._resolve_plugin_dependencies(DummyPlugin)
    
    assert "dummy" in deps
    assert not missing
    # Debería inyectarse el proxy del tool, no la instancia cruda
    assert deps["dummy"]._tool is dummy_instance

def test_resolve_dependencies_missing(kernel):
    # Intentamos resolver dependencias de un plugin cuyo tool no existe
    deps, missing = kernel._resolve_plugin_dependencies(DummyPlugin)
    
    assert "dummy" not in deps
    assert "dummy" in missing

# ─── 2. Pruebas de Ejecución (Call Maybe Async) ──────────────────────────────

async def test_call_maybe_async(kernel):
    def sync_fn(): 
        return "sync_result"
        
    async def async_fn(): 
        return "async_result"
        
    res_sync = await kernel._call_maybe_async(sync_fn)
    res_async = await kernel._call_maybe_async(async_fn)
    
    assert res_sync == "sync_result"
    assert res_async == "async_result"

# ─── 3. Pruebas del Ciclo de Vida (Boot) ─────────────────────────────────────

async def test_boot_success(kernel, monkeypatch):
    """Prueba que el kernel arranque tools y plugins correctamente cuando todo existe."""
    
    def fake_load_modules(directory, base_class):
        if base_class == BaseTool:
            return [(DummyTool, None)]
        elif base_class == BasePlugin:
            return [(DummyPlugin, "dummy_domain")]
        return []

    monkeypatch.setattr(kernel, "_load_modules_from_dir", fake_load_modules)
    
    await kernel.boot()
    
    # Verificamos tool
    assert kernel.container.has_tool("dummy")
    assert kernel.container.get("dummy").ready is True
    assert kernel.container.get("dummy").boot_completed is True
    assert kernel.container.registry.get_tool_status("dummy") == "OK"
    
    # Verificamos plugin
    assert "DummyPlugin" in kernel.plugins
    assert kernel.plugins["DummyPlugin"].booted is True
    assert kernel.container.registry.get_system_dump()["plugins"]["DummyPlugin"]["status"] == "READY"

async def test_boot_missing_dependencies(kernel, monkeypatch):
    """Prueba que si falta un tool requerido por un plugin, este último se marca DEAD y no bloquea el boot."""
    
    def fake_load_modules(directory, base_class):
        # No cargamos tools, pero sí el plugin
        if base_class == BaseTool:
            return []
        elif base_class == BasePlugin:
            return [(DummyPlugin, "dummy_domain")]
        return []

    monkeypatch.setattr(kernel, "_load_modules_from_dir", fake_load_modules)
    
    await kernel.boot()
    
    assert "DummyPlugin" not in kernel.plugins
    status = kernel.container.registry.get_system_dump()["plugins"]["DummyPlugin"]["status"]
    assert status == "DEAD"
    
    # Debería haber un error registrado en el registry
    dump = kernel.container.registry.get_system_dump()
    assert "Missing tools: dummy" in dump["plugins"]["DummyPlugin"]["error"]