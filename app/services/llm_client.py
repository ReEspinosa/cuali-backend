"""
Cliente compartido hacia el servidor OpenAI-compatible (LM Studio, UNAM).

El servidor está detrás de un proxy que pide HTTP Basic Auth
(llm_user / llm_password). El SDK de OpenAI no soporta basic auth de forma
nativa, así que le pasamos un httpx.Client propio con el auth configurado.

CONCURRENCIA:
LM Studio con gpt-oss-20b atiende muy pocas peticiones en paralelo. Sin
control, 15 maestros generando a la vez encolan peticiones de 60s cada una
y TODO empieza a hacer timeout ("nos tiran la página"). El semáforo de abajo
deja pasar máximo `llm_max_concurrent` llamadas simultáneas; el resto espera
en cola hasta `llm_queue_timeout_seconds` y, si no alcanza turno, regresa
HTTP 503 con un mensaje amable en lugar de colgar el servidor.

Todas las llamadas al LLM del proyecto pasan por chat_completion /
chat_completion_json, así que proteger estas dos funciones protege todo.
"""

import threading
from contextlib import contextmanager
from functools import lru_cache

import httpx
from fastapi import HTTPException
from openai import OpenAI

from app.core.config import settings

_llm_semaphore = threading.BoundedSemaphore(settings.llm_max_concurrent)


@contextmanager
def _llm_slot():
    acquired = _llm_semaphore.acquire(timeout=settings.llm_queue_timeout_seconds)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cuali está atendiendo muchas solicitudes en este momento. "
                "Intenta de nuevo en unos segundos."
            ),
            headers={"Retry-After": "15"},
        )
    try:
        yield
    finally:
        _llm_semaphore.release()


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
    with _llm_slot():
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
    with _llm_slot():
        try:
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except HTTPException:
            raise
        except Exception:
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
    return response.choices[0].message.content or ""