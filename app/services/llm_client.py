"""
Cliente compartido hacia el servidor OpenAI-compatible (LM Studio, UNAM).

Todas las llamadas al LLM del proyecto pasan por chat_completion /
chat_completion_json, asi que las reglas de abajo protegen todo.

CONCURRENCIA:
LM Studio atiende muy pocas peticiones en paralelo. El semaforo deja pasar
maximo `llm_max_concurrent` llamadas simultaneas; el resto espera en cola
hasta `llm_queue_timeout_seconds` y, si no alcanza turno, recibe HTTP 503.

UN SOLO REINTENTO, CON ESPERA:
El SDK de OpenAI reintenta 2 veces seguidas ante un 502/503. Con un
servidor ya saturado eso triplica la carga justo cuando menos aguanta, asi
que se desactiva (max_retries=0). En su lugar, ante un error pasajero
(429/502/503) se espera unos segundos y se intenta UNA vez mas; si vuelve a
fallar, el docente decide si reintentar.

FORMATO JSON:
El servidor de la UNAM acepta response_format "json_schema" (no
"json_object"). Se puede activar con LLM_JSON_SCHEMA=true, pero esta
apagado por defecto: con gpt-oss produjo textos de relleno al forzar el
numero de elementos. Sin esquema, el codigo valida y repara el JSON.

RESPUESTAS VACIAS:
gpt-oss razona antes de responder y ese razonamiento consume los mismos
tokens que la respuesta. En tareas largas puede acabarse el limite antes de
escribir nada: llega content="" con finish_reason="length". En ese caso se
reintenta UNA vez con el doble de tokens (con tope); si vuelve a salir vacio
se responde 503. Nunca se devuelve una respuesta vacia como si fuera valida.

ERRORES:
Cualquier falla del servidor del LLM se convierte en HTTP 503 con un mensaje
claro. Los routers no guardan nada cuando eso pasa.
"""

import logging
import threading
import time
from contextlib import contextmanager
from functools import lru_cache

import httpx
from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, OpenAI

from app.core.config import settings

logger = logging.getLogger("cuali.llm")

_llm_semaphore = threading.BoundedSemaphore(settings.llm_max_concurrent)

# Si el servidor rechaza el parametro reasoning_effort, se deja de mandar
# para el resto de la vida del proceso. Escritura de un bool: segura entre hilos.
_reasoning_soportado = {"valor": bool(settings.llm_reasoning_effort)}
# Igual con los formatos de salida: LM Studio de la UNAM rechaza
# "json_object" y acepta "json_schema". Lo rechazado se deja de intentar.
_formato_soportado = {"json_schema": True, "json_object": True}

# Errores pasajeros del servidor o del proxy (saturado, reiniciando). Se
# reintenta UNA vez tras una espera corta; no tres seguidas como el SDK.
_ESTADOS_PASAJEROS = {429, 502, 503}
ESPERA_REINTENTO_SEGUNDOS = 3.0

MENSAJE_NO_DISPONIBLE = "Cuali no está disponible en este momento. Intenta de nuevo en unos segundos."


def llm_no_disponible(detalle: str = MENSAJE_NO_DISPONIBLE) -> HTTPException:
    return HTTPException(status_code=503, detail=detalle, headers={"Retry-After": "15"})


@contextmanager
def _llm_slot():
    acquired = _llm_semaphore.acquire(timeout=settings.llm_queue_timeout_seconds)
    if not acquired:
        raise llm_no_disponible(
            "Cuali está atendiendo muchas solicitudes en este momento. Intenta de nuevo en unos segundos."
        )
    try:
        yield
    finally:
        _llm_semaphore.release()


@lru_cache
def get_llm_client() -> OpenAI:
    timeout = httpx.Timeout(float(settings.llm_timeout_seconds), connect=10.0)
    # Con API key (servidor actual) se usa directo; sin ella, se cae al
    # HTTP Basic Auth viejo por compatibilidad.
    if settings.llm_api_key:
        return OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=timeout,
            max_retries=0,
        )
    http_client = httpx.Client(
        auth=(settings.llm_user, settings.llm_password) if settings.llm_user else None,
        timeout=timeout,
    )
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.openai_api_key or "not-needed",
        http_client=http_client,
        max_retries=0,
    )


def _crear(client: OpenAI, **kwargs):
    """Una llamada al servidor, con el esfuerzo de razonamiento si se soporta."""
    if _reasoning_soportado["valor"]:
        try:
            return client.chat.completions.create(
                extra_body={"reasoning_effort": settings.llm_reasoning_effort}, **kwargs
            )
        except BadRequestError as e:
            if "reasoning" not in str(e).lower():
                raise
            logger.warning("El servidor no acepta reasoning_effort; se deja de enviar.")
            _reasoning_soportado["valor"] = False
    return client.chat.completions.create(**kwargs)


def _completar(
    messages: list[dict], temperature: float, max_tokens: int, propagar_400: bool = False, **extra
) -> str:
    """
    Llama al LLM y regresa el texto final, sin el razonamiento interno.
    Reintenta una vez si la respuesta se corto antes de escribir algo, o si
    el servidor respondio con un error pasajero (429, 502, 503).
    Costo: a lo mas 2 llamadas al servidor.
    """
    client = get_llm_client()
    tokens = max_tokens
    choice = None
    with _llm_slot():
        for intento in range(2):
            try:
                response = _crear(
                    client,
                    model=settings.llm_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=tokens,
                    **extra,
                )
            except BadRequestError as e:
                if propagar_400:
                    raise
                logger.error("El LLM rechazo la peticion: %s", e)
                raise llm_no_disponible()
            except (APIConnectionError, APITimeoutError) as e:
                logger.error("No se pudo contactar al LLM: %s", e)
                raise llm_no_disponible()
            except APIStatusError as e:
                if intento == 0 and e.status_code in _ESTADOS_PASAJEROS:
                    logger.warning("El LLM respondio HTTP %s; se reintenta en %ss.", e.status_code, ESPERA_REINTENTO_SEGUNDOS)
                    time.sleep(ESPERA_REINTENTO_SEGUNDOS)
                    continue
                logger.error("El LLM respondio HTTP %s", e.status_code)
                raise llm_no_disponible()

            choice = response.choices[0]
            contenido = (choice.message.content or "").strip()
            if contenido:
                return contenido

            se_corto = choice.finish_reason == "length"
            if intento == 0 and se_corto and tokens < settings.llm_max_tokens_reintento:
                tokens = min(tokens * 2, settings.llm_max_tokens_reintento)
                logger.warning("Respuesta vacia por limite de tokens; se reintenta con %s.", tokens)
                continue
            break

    logger.error("El LLM devolvio una respuesta vacia (finish_reason=%s).", getattr(choice, "finish_reason", None))
    raise llm_no_disponible("Cuali no pudo terminar la respuesta. Intenta de nuevo.")


def chat_completion(messages: list[dict], temperature: float = 0.4, max_tokens: int = 1500) -> str:
    """Llamada simple de chat. Regresa solo el texto final (sin razonamiento oculto)."""
    return _completar(messages, temperature, max_tokens)


def chat_completion_json(
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 2500,
    schema: dict | None = None,
    nombre: str = "respuesta",
) -> str:
    """
    Llamada de chat pidiendo JSON, del formato mas estricto al mas libre:
      1. json_schema (si se da `schema`): el servidor obliga a respetar la
         estructura, asi casi no hay JSON roto ni llamadas de reparacion.
      2. json_object.
      3. Texto libre.
    Un formato que el servidor rechaza con 400 se deja de intentar. El
    llamador valida el JSON de todas formas.
    """
    formatos = []
    if schema is not None and settings.llm_json_schema and _formato_soportado["json_schema"]:
        formatos.append(("json_schema", {
            "type": "json_schema",
            "json_schema": {"name": nombre, "strict": True, "schema": schema},
        }))
    if _formato_soportado["json_object"]:
        formatos.append(("json_object", {"type": "json_object"}))

    for clave, formato in formatos:
        try:
            return _completar(messages, temperature, max_tokens, propagar_400=True, response_format=formato)
        except BadRequestError as e:
            mensaje = str(e).lower()
            if "response_format" not in mensaje and "json" not in mensaje and "schema" not in mensaje:
                # Otro tipo de 400 (por ejemplo, contexto demasiado largo).
                logger.error("El LLM rechazo la peticion: %s", e)
                raise llm_no_disponible()
            logger.warning("El servidor no acepta response_format=%s: %s", clave, str(e)[:200])
            _formato_soportado[clave] = False
    return _completar(messages, temperature, max_tokens)
