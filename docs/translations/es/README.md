# MicroCoreOS: Arquitectura de Micro-kernel Atómico optimizada para Desarrollo Guiado por IA

> Cada vez que le pedía a mi IA que agregara un endpoint CRUD,  
> intentaba crear 6 u 8 archivos. Me cansé de eso.

**1 archivo = 1 funcionalidad.** Esa es toda la idea.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## El Problema

Los asistentes de IA como Cursor y Claude necesitan entender tu arquitectura para agregar funcionalidades.

En las arquitecturas tradicionales de capas, eso significa explicar:
- Dónde poner la entidad
- Cómo conectar el repositorio
- Qué fábrica crea el caso de uso
- Cómo el controlador se mapea a la ruta
- Qué DTOs crear

**Esos son de 6 a 8 archivos y más de 200 líneas de código para un solo endpoint.**

## La Solución: Micro-kernel Atómico

```python
# domains/products/plugins/create_product_plugin.py
from microcoreos.base_plugin import BasePlugin

class CreateProductPlugin(BasePlugin):
    def __init__(self, http_server, db, logger, event_bus):
        self.http = http_server
        self.db = db
        self.logger = logger
        self.bus = event_bus

    def on_boot(self):
        self.http.add_endpoint("/products", "POST", self.execute)

    def execute(self, data: dict):
        product_id = self.db.execute(
            "INSERT INTO products (name, price) VALUES (?, ?)",
            (data["name"], data["price"])
        )
        self.bus.publish("product.created", {"id": product_id})
        return {"success": True, "id": product_id}
```

**48 líneas. Un archivo. Funcionalidad completa.**

- ✅ Registro del Endpoint
- ✅ Operación de Base de Datos
- ✅ Publicación de Eventos
- ✅ Autodescubrimiento por el kernel
- ✅ Dependencias inyectadas automáticamente

---

## Para el Desarrollo Guiado por IA

La arquitectura genera `AI_CONTEXT.md` automáticamente—un manifiesto con todas las herramientas disponibles y sus firmas. Tu asistente de IA siempre sabe qué está disponible sin explorar el código.

**Uso medido de tokens por funcionalidad:**

| Arquitectura | Archivos | Líneas | Tokens Est. |
|--------------|----------|--------|-------------|
| **MicroCoreOS** | 1 | ~50 | ~1,000 |
| Vertical Slice | 2-3 | ~100 | ~1,500 |
| N-Capas | 4-5 | ~150 | ~2,500 |
| Hexagonal | 5-7 | ~200 | ~3,500 |
| Clean Architecture | 6-8 | ~250 | ~4,000 |

---

## Inicio Rápido

```bash
git clone https://github.com/theanibalos/MicroCoreOS.git
cd MicroCoreOS
uv run main.py
# Visita http://localhost:5000/docs
```

---

## Estructura del Proyecto

```
MicroCoreOS/
├── core/                    # El micro-kernel (~240 líneas total)
│   ├── kernel.py           # Orquestador con autodescubrimiento
│   ├── container.py        # Contenedor DI seguro para hilos
│   ├── base_plugin.py      # Contrato de Plugin (13 líneas)
│   └── base_tool.py        # Contrato de Tool (23 líneas)
├── tools/                   # Infraestructura (sin estado)
│   ├── http_server/        # Wrapper para FastAPI
│   ├── sqlite/             # Abstracción de Base de Datos
│   └── event_bus/          # Comunicación desacoplada
├── domains/                 # Lógica de negocio
│   └── {domain}/
│       ├── plugins/        # Casos de uso (1 archivo = 1 funcionalidad)
│       └── models/         # Modelos de dominio
└── AI_CONTEXT.md           # Generado auto. para asistentes IA
```

---

## Principios Fundamentales

| Principio | Descripción |
|-----------|-------------|
| **Kernel Ciego** | El kernel no sabe nada sobre la lógica de negocio |
| **Tool = Sin Estado** | Las herramientas (Tools) brindan capacidades técnicas |
| **Plugin = Con Estado** | Los plugins contienen la lógica de negocio |
| **Orientado a Eventos**| Los plugins se comunican vía EventBus únicamente |
| **DI Declarativo** | Declara las dependencias en el constructor, el kernel las entrega |

---

## Herramientas Disponibles

| Tool | Descripción |
|------|-------------|
| `http_server` | Endpoints REST con OpenAPI autogenerado |
| `db` | Abstracción de SQLite (consultas y ejecuciones) |
| `event_bus` | Patrones pub/sub y request/response |
| `logger` | Logging estructurado |
| `state` | Almacén en memoria de clave-valor |
| `config` | Configuración de entorno |

---

## Decisiones Avanzadas de Diseño

### Tool vs Plugin: ¿Cómo Decidir?

```text
¿Es un Tool o un Plugin?
├── ¿Tiene un estado de dominio?            → Plugin
├── ¿Es reutilizable entre dominios?        → Tool  
└── ¿Implementa reglas de negocio?          → Plugin
```

**Ejemplo - Autenticación:**  
- Verificación de la firma del token (criptografía) → **Tool** (técnico, sin estado)  
- Administración de usuarios y permisos → **Plugin** (estado de dominio, reglas de negocio)

### Eventos: Síncronos vs Asíncronos

| Método | Cuándo usarlo | Ejemplo |
|--------|---------------|---------|
| `publish(event, data)` | Disparar y olvidar (no ocupa confirmación) | Notificaciones, logs, side-effects |
| `request(event, data)` | Necesitas una respuesta para continuar (RPC)| Validaciones cruzadas, consultas |

> [!WARNING]
> Abusar de `request()` reintroduce acoplamientos. Si un Plugin hace demasiadas peticiones a otro, probablemente pertenecen al mismo dominio.

### Ciclo de Arranque (Boot Lifecycle)

```text
Secuencia de Arranque:
1. Tool.setup()            → Inicialización interna
2. Plugin.__init__()       → Inyección de dependencias
3. Plugin.on_boot()        → Registro de endpoints, suscripciones
4. Tool.on_boot_complete() → Acciones que requieren el sistema completo
5. Sistema en línea        → Listo para peticiones
6. Tool.shutdown()         → Limpieza y liberación de recursos
```

---

## Antipatrones (Los "No hacer")

| ❌ Antipatrón | ✅ Solución |
|----------------|------------|
| Un plugin importa a otro Plugin | Comuníquense por el EventBus |
| El plugin usa directamente al Container | Exige la Tool al Kernel en el `__init__` |
| Tool con lógica de negocio | Muévelo a un Plugin en el dominio correcto |
| Compartir una tabla estado global | Usa el Tool `state` para caché / sincronización |

---

## Alto Rendimiento y Producción

Si tu implementación demanda velocidades extremas (motores de juegos, video 4K o HFT):

### 1. Arquitectura Zero-Copy (Cero Copias)
Para mover mucha información entre plugins sin retrasos:
* Usa gestores de memoria (ej. apuntadores atómicos en Rust) para que diversos plugins lean la **misma memoria física** simultáneamente.

### 2. Static Dispatch (Enrutamiento estático)
La Inyección dinámica cuesta microsegundos. Para velocidad instantánea:
* Usa generadores de código para compilar el ruteo interno (elimina todo overhead de ejecución).

### 3. Selección por Latencia

| Lenguaje | Perfil | Ideal para... |
|----------|--------|---------------|
| **Python** | Confort de contexto | Prototipado, IA, APIs y Microservicios CRUD |
| **Go** | Rendimiento escalado | APIs masivas de altísimo concurrente tráfico |
| **Rust** | Latencia Extrema | Motores en tiempo real, procesado físico y de video |

---

## Hoja de Ruta (Roadmap)

MicroCoreOS avanza hacia un ecosistema descentralizado inspirado en "marketplaces":

- 🏗️ **Tienda atómica de Tools**: Ecosistema para integrar módulos con carpetas (ej. conectar Redis, WebSockets, Postgres o IAs) sin configuraciones pesadas.
- 🔍 **Herramienta Trace/Observabilidad**: Trazado visual de qué eventos desencadenan qué plugins bajo el cofre.
- 🌐 **Kernel Políglota**: Integración de plugins adyacentes ("sidecars") bajo gRPC O WASM.
- 📦 **Instalación de 1 Click**: Integra nuevas Tools al ecosistema vía comandos tipo CLI o simplemente copiando carpetas nativas a `/tools`.

---

## ¿Por qué no usar algo ya existente?

MicroCoreOS implementa su propio Inyector (DI) intencionalmente:
* **¿Por qué no usar FastAPI desde la raíz?**: Reducimos el nivel cognitivo de los Agentes y Asistentes. Tu código visible para la IA y tú es puramente atómico. El "Framework" es `/core`.
* **¿Por qué no inyectores de terceros?**: La simplicidad. En menos de un minuto puedes leer y entender el `core/kernel.py` y ver exactamente la magia.

---

## Ventajas para Equipos Formales 

En sistemas convencionales cada característica requiere alta coordinación:
- Un developer es el encargado de la "Domain Layer".
- Otro debe configurar un puerto en "Infraestructura".
- Alguien inyecta las dependencias al arrancar.
- Finalmente revisión estructural minuciosa.

**En MicroCoreOS: 1 persona, 1 archivo, 1 revisión (PR).**

### ¿Por qué los "Tools" no tienen estado?

Los conectores técnicos no retienen estado de tu negocio. Esto implica:

- **Efecto de Despegue (Quickstart):** Sin dependencias como Docker arrancas SQLite y bases de eventos al instante.
- **Microservicio infinito horizontal**: Cámbiale a `redis_event_bus` la tool y escalar a 20 nodos será igual. El Kernel detectará los cambios. **Tus plugins quedan 100% inalterados.**

---

## Lenguaje y Traducciones

- [English](../../../README.md)

---

## Licencia

[MIT](LICENSE) - Simple y permisiva.

Para soporte o consultar empresarial: theanibalos@gmail.com

---

## Autor

**AnibalOS** ([@theanibalos](https://github.com/theanibalos))

Construido porque estaba cansado de explicarle mi arquitectura una y otra vez a Claude.

---
