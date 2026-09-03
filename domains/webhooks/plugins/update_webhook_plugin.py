from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

# No usamos el request_model en add_endpoint para este plugin
# porque queremos recibir un diccionario crudo y procesarlo dinámicamente.

class UpdateWebhookResponse(BaseModel):
    success: bool
    error: Optional[str] = None

class UpdateWebhookPlugin(BasePlugin):
    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/webhooks/{webhook_id}", "PUT", self.execute,
            tags=["Webhooks"],
            response_model=UpdateWebhookResponse,
        )

    async def execute(self, data: dict, context=None):
        raw_id = data.get("webhook_id")
        if raw_id is None:
            return {"success": False, "error": "Missing webhook_id"}
            
        try:
            webhook_id = int(raw_id)
            
            # 1. Extraer SOLO los campos que vienen en el cuerpo de la petición
            # y que no son el webhook_id de la URL.
            body_fields = {k: v for k, v in data.items() if k != "webhook_id"}
            
            if not body_fields:
                return {"success": True}

            fields = []
            values = []
            
            # 2. Construir la consulta SQL dinámicamente SOLO con los campos recibidos
            for key, value in body_fields.items():
                # Conversión de booleanos para SQLite
                if key == "enabled":
                    value = 1 if value else 0
                
                # Mapear strings vacíos a NULL solo para campos opcionales
                if value == "" and key in ["filter_field", "filter_value", "body_template"]:
                    value = None
                    
                fields.append(f"{key} = ${len(fields) + 1}")
                values.append(value)
            
            # 3. Añadir el ID al final para el WHERE
            values.append(webhook_id)
            query = f"UPDATE webhooks SET {', '.join(fields)}, updated_at = datetime('now') WHERE id = ${len(values)}"
            
            # 4. Ejecutar
            await self.db.execute(query, values)
            return {"success": True}
            
        except Exception as e:
            self.logger.error(f"Failed to update webhook {raw_id}: {e}")
            return {"success": False, "error": str(e)}
