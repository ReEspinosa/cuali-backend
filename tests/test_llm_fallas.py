"""
Comportamiento ante fallas del LLM: respuestas vacias, servidor caido y
JSON invalido. Ninguna falla debe guardarse como si fuera respuesta de Cuali.
"""

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from openai import APIStatusError

from app.models.conversacion import ChatMensaje
from app.models.planeacion import Mensaje, Planeacion
from app.services import llm_client
from app.services.llm_client import llm_no_disponible


def _respuesta(contenido: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=contenido), finish_reason=finish_reason)]
    )


class ClienteFalso:
    """Imita client.chat.completions.create devolviendo respuestas en orden."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _con_cliente(cliente):
    return patch.object(llm_client, "get_llm_client", return_value=cliente)


def test_respuesta_vacia_por_limite_se_reintenta_con_mas_tokens():
    cliente = ClienteFalso([_respuesta("", "length"), _respuesta("hola")])
    with _con_cliente(cliente):
        assert llm_client.chat_completion([{"role": "user", "content": "x"}], max_tokens=1000) == "hola"
    assert [c["max_tokens"] for c in cliente.llamadas] == [1000, 2000]


def test_respuesta_vacia_dos_veces_es_503_y_no_texto_vacio():
    cliente = ClienteFalso([_respuesta("", "length"), _respuesta("", "length")])
    with _con_cliente(cliente), pytest.raises(HTTPException) as exc:
        llm_client.chat_completion([{"role": "user", "content": "x"}])
    assert exc.value.status_code == 503
    assert len(cliente.llamadas) == 2  # nunca mas de 2 llamadas


def _error_http(codigo: int):
    peticion = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")
    return APIStatusError(str(codigo), response=httpx.Response(codigo, request=peticion), body=None)


def test_servidor_caido_es_503_con_un_solo_reintento(monkeypatch):
    monkeypatch.setattr(llm_client, "ESPERA_REINTENTO_SEGUNDOS", 0)
    cliente = ClienteFalso([_error_http(503), _error_http(503)])
    with _con_cliente(cliente), pytest.raises(HTTPException) as exc:
        llm_client.chat_completion([{"role": "user", "content": "x"}])
    assert exc.value.status_code == 503
    assert len(cliente.llamadas) == 2  # nunca mas de 2


def test_un_503_pasajero_se_recupera(monkeypatch):
    monkeypatch.setattr(llm_client, "ESPERA_REINTENTO_SEGUNDOS", 0)
    cliente = ClienteFalso([_error_http(503), _respuesta("hola")])
    with _con_cliente(cliente):
        assert llm_client.chat_completion([{"role": "user", "content": "x"}]) == "hola"


def test_un_500_no_se_reintenta():
    cliente = ClienteFalso([_error_http(500)])
    with _con_cliente(cliente), pytest.raises(HTTPException):
        llm_client.chat_completion([{"role": "user", "content": "x"}])
    assert len(cliente.llamadas) == 1


def test_json_schema_y_si_lo_rechaza_se_deja_de_pedir(monkeypatch):
    from openai import BadRequestError

    monkeypatch.setattr(llm_client.settings, "llm_json_schema", True)
    monkeypatch.setitem(llm_client._formato_soportado, "json_schema", True)
    monkeypatch.setitem(llm_client._formato_soportado, "json_object", True)
    peticion = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")
    rechazo = BadRequestError(
        "'response_format.type' must be 'json_schema' or 'text'",
        response=httpx.Response(400, request=peticion), body=None,
    )
    esquema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    # El servidor acepta json_schema: una sola llamada con el esquema.
    cliente = ClienteFalso([_respuesta('{"ok": true}')])
    with _con_cliente(cliente):
        assert llm_client.chat_completion_json([{"role": "user", "content": "x"}], schema=esquema) == '{"ok": true}'
    assert cliente.llamadas[0]["response_format"]["type"] == "json_schema"

    # Sin esquema, json_object es rechazado: se marca y se sigue en texto.
    cliente = ClienteFalso([rechazo, _respuesta('{"ok": true}')])
    with _con_cliente(cliente):
        llm_client.chat_completion_json([{"role": "user", "content": "x"}])
    assert llm_client._formato_soportado["json_object"] is False
    assert "response_format" not in cliente.llamadas[1]


def test_se_pide_razonamiento_bajo():
    cliente = ClienteFalso([_respuesta("ok")])
    with _con_cliente(cliente):
        llm_client.chat_completion([{"role": "user", "content": "x"}])
    assert cliente.llamadas[0]["extra_body"] == {"reasoning_effort": "low"}


def test_si_el_llm_falla_no_se_guarda_ningun_mensaje(client, db, crear_docente):
    _, headers = crear_docente()
    conv_id = client.post("/conversaciones", headers=headers).json()["id"]

    with patch("app.routers.conversaciones.generar_respuesta_general", side_effect=llm_no_disponible()):
        r = client.post(f"/conversaciones/{conv_id}/mensajes", headers=headers, json={"content": "hola"})

    assert r.status_code == 503
    assert "no está disponible" in r.json()["detail"]
    assert db.query(ChatMensaje).filter(ChatMensaje.conversacion_id == conv_id).count() == 0
    detalle = client.get(f"/conversaciones/{conv_id}", headers=headers).json()
    assert detalle["titulo"] == "Nueva conversación"


def test_json_invalido_al_generar_es_502_y_no_500(client, db, crear_docente):
    docente, headers = crear_docente()
    p = Planeacion(
        owner_id=docente.id, grado=6, campo_formativo="Lenguajes", contenido="c",
        pda="p", grupo="A", sesiones=2, tema="t",
    )
    db.add(p)
    db.commit()
    db.add(Mensaje(planeacion_id=p.id, role="user", content="m"))
    db.commit()

    with patch("app.services.planeacion_nem.generador.chat_completion_json", return_value="esto no es json"), \
         patch("app.services.planeacion_nem.generador.buscar_fuentes", return_value=([], "")):
        r = client.post(f"/planeaciones/{p.id}/generar", headers=headers)

    assert r.status_code == 502
    assert "Intenta" in r.json()["detail"]
