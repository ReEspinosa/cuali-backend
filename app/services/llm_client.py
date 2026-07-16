"""
Cliente compartido hacia el servidor OpenAI-compatible (LM Studio, UNAM).

El servidor está detrás de un proxy que pide HTTP Basic Auth
(llm_user / llm_password). El SDK de OpenAI no soporta basic auth de forma
nativa, así que le pasamos un httpx.Client propio con el auth configurado.

Si en tu servidor NO se usa Basic Auth (por ejemplo si llm_user/llm_password
son en realidad un usuario/contraseña de otra cosa, como VPN o SSH, y el
LLM no pide nada), simplemente no pases `auth=` al construir httpx.Client y
usa openai_api_key si LM Studio sí valida un Bearer token.
"""

from functools import lru_cache

import httpx
from openai import OpenAI

from app.core.config import settings


@lru_cache
def get_llm_client() -> OpenAI:
    http_client = httpx.Client(
        auth=(settings.llm_user, settings.llm_password) if settings.llm_user else None,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.openai_api_key or "not-needed",
        http_client=http_client,
    )


def chat_completion(messages: list[dict], temperature: float = 0.4, max_tokens: int = 1500) -> str:
    """Llamada simple de chat. Regresa solo el texto final (sin razonamiento oculto)."""
    client = get_llm_client()
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def chat_completion_json(messages: list[dict], temperature: float = 0.2, max_tokens: int = 2500) -> str:
    """
    Llamada de chat pidiendo JSON. gpt-oss-20b vía LM Studio puede o no soportar
    response_format=json_schema estricto según la versión del backend
    (llama.cpp/grammar). Se intenta con json_object y, si el servidor lo
    rechaza, se cae a una llamada normal con instrucciones estrictas en el
    prompt (el llamador debe validar/reparar el JSON de todas formas).
    """
    client = get_llm_client()
    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    return response.choices[0].message.content or ""