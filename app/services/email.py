"""
Envío de correo real vía Gmail SMTP.

Requiere que en tu .env tengas:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465
    SMTP_USER=tu_correo@gmail.com
    SMTP_PASSWORD=contraseña_de_aplicacion_de_16_caracteres
    SMTP_FROM=tu_correo@gmail.com

Cómo generar la contraseña de aplicación:
https://myaccount.google.com/apppasswords
(requiere tener activada la verificación en 2 pasos en esa cuenta de Gmail)
"""

import smtplib
from email.mime.text import MIMEText

from app.core.config import settings


def enviar_codigo_verificacion(destinatario: str, codigo: str) -> None:
    if not settings.smtp_user or not settings.smtp_password:
        # Sin credenciales configuradas todavía: seguimos con el stub para
        # no tronar el flujo completo, pero avisamos claramente en consola.
        print(
            f"\nSMTP no configurado - código para {destinatario}: {codigo}\n"
            f"Configura SMTP_USER y SMTP_PASSWORD en tu .env para mandar correos reales.\n"
        )
        return

    asunto = "Verifica tu cuenta de Cuali"
    cuerpo = f"""Hola,

Tu código de verificación de Cuali es:

    {codigo}

Este código expira en 15 minutos.

Si tú no creaste esta cuenta, puedes ignorar este correo.
"""

    msg = MIMEText(cuerpo)
    msg["Subject"] = asunto
    msg["From"] = settings.smtp_from
    msg["To"] = destinatario

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        print(f"Correo de verificación enviado a {destinatario}")
    except smtplib.SMTPAuthenticationError:
        print(
            "Gmail rechazó las credenciales. Revisa que SMTP_USER y SMTP_PASSWORD "
            "en tu .env sean correctos, y que la contraseña sea de tipo 'aplicación' "
            "(no tu contraseña normal de Gmail)."
        )
        raise
    except Exception as e:
        print(f"Error enviando correo: {e}")
        raise