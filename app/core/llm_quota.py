"""
Cuota de generaciones con LLM por usuario.

Se aplica a nivel router (en main.py) sobre los routers que llaman al LLM:
conversaciones, planeaciones y recursos. Solo cuenta peticiones POST
(las que disparan generaciones); los GET (listar, descargar .docx/.pptx)
no consumen cuota.

Con esto, aunque un bot consiga un JWT valido (por ejemplo registrando una
cuenta con un correo real), no puede hacer "mil peticiones al back": tras
`llm_user_max_per_hour` generaciones en una hora recibe 429 y ya no toca
el LLM.
"""

from fastapi import Depends, Request

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.models.user import User


def llm_user_quota(
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    if request.method != "POST":
        return

    limiter.check(
        key=f"llm:user:{user.id}",
        max_hits=settings.llm_user_max_per_hour,
        window_seconds=3600,
        error_detail=(
            "Alcanzaste el límite de generaciones por hora. "
            "Espera un poco antes de generar más contenido."
        ),
    )