"""
Prueba rápida de conectividad con el servidor LM Studio (UNAM).

Uso:
    cd cuali-backend
    python scripts/test_llm_connection.py

Requiere las variables de entorno reales en tu .env:
    LLM_BASE_URL / OPENAI_BASE_URL
    LLM_MODEL / OPENAI_MODEL
    LLM_USER
    LLM_PASSWORD

Si esto falla con 401/403, el problema es la autenticación (revisar si de
verdad es HTTP Basic Auth). Si falla con timeout/connection error, el
problema es de red (VPN, firewall, o el servidor apagado). Si responde
pero con contenido raro (tags <|channel|> etc.), es el formato "harmony"
de gpt-oss sin procesar del lado del backend — avísame y ajustamos el parseo.
"""

import sys
from pathlib import Path

# Agrega la raíz del proyecto (el directorio que contiene la carpeta app/)
# al path de búsqueda, para que "from app...." funcione sin importar desde
# dónde se corra este script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm_client import chat_completion

if __name__ == "__main__":
    try:
        respuesta = chat_completion(
            messages=[
                {"role": "system", "content": "Eres un asistente de prueba. Responde en una sola oración."},
                {"role": "user", "content": "Confirma que estás funcionando y di qué modelo eres."},
            ],
            temperature=0.2,
            max_tokens=100,
        )
        print("Conexión exitosa. Respuesta del modelo:")
        print(respuesta)
    except Exception as exc:
        print("Error al conectar con el LLM:", repr(exc))
        sys.exit(1)