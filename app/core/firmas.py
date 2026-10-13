"""
URLs firmadas para archivos.

Problema: una etiqueta <img src="..."> del navegador no puede mandar el
header Authorization, asi que una imagen protegida por sesion no se
mostraria. La solucion estandar es que el backend, al responder a un usuario
que SI tiene permiso, entregue una URL con una firma HMAC que caduca:

    /archivos/<nombre>?exp=<unix>&firma=<hmac>

- La firma se calcula con SECRET_KEY, asi que nadie puede fabricarla.
- Caduca (por defecto en 1 hora): si la URL se filtra, deja de servir.
- Cubre el nombre y la expiracion: cambiar cualquiera invalida la firma.

Costo: firmar y verificar son O(1) (un HMAC-SHA256 sobre ~60 bytes).
"""

import hashlib
import hmac
import time

from app.core.config import settings

# Prefijo de dominio: evita que una firma hecha para otro proposito
# (si algun dia se firma otra cosa con la misma llave) sirva aqui.
_DOMINIO = b"cuali:archivo:v1:"

TTL_POR_DEFECTO_SEGUNDOS = 3600


def _calcular_firma(nombre: str, exp: int) -> str:
    mensaje = _DOMINIO + f"{nombre}:{exp}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()


def url_firmada(nombre: str, ttl_segundos: int = TTL_POR_DEFECTO_SEGUNDOS) -> str:
    exp = int(time.time()) + ttl_segundos
    return f"/archivos/{nombre}?exp={exp}&firma={_calcular_firma(nombre, exp)}"


def firma_valida(nombre: str, exp: int | None, firma: str | None) -> bool:
    if exp is None or not firma:
        return False
    if exp < int(time.time()):
        return False
    # compare_digest compara en tiempo constante: no filtra por tiempos de
    # respuesta cuantos caracteres de la firma eran correctos.
    return hmac.compare_digest(_calcular_firma(nombre, exp), firma)
