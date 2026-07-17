"""
rag_chat.py
-----------
Punto de integración con tu backend. Expone la función `ask()`, que:
  1. Recupera los chunks más relevantes de los libros de la SEP (retrieval híbrido)
  2. Arma un prompt con ese contexto + reglas anti-alucinación
  3. Llama al modelo de chat y regresa la respuesta junto con las fuentes citadas

Por default, ask() crea su propio cliente de OpenAI (útil para correr este
módulo de forma standalone, como en test_rag_chat.py). Si tu proyecto ya
tiene su propio cliente probado y funcionando (como app.services.llm_client
en Cuali, que sabe manejar el HTTP Basic Auth del servidor de la UNAM),
pásalo como `chat_fn` -- una función que reciba `messages` y regrese el
texto de la respuesta -- y ask() lo usa en vez de crear uno nuevo.
"""
import base64
import importlib
import re

try:
    openai_module = importlib.import_module("openai")
    OpenAI = openai_module.OpenAI
except Exception:  # pragma: no cover - fallback defensivo
    OpenAI = None

from . import config
from .retrieval import hybrid_search

SYSTEM_PROMPT_BASE = """Eres un asistente pedagógico para docentes de educación primaria \
pública en México. Apoyas en la planeación de clases, dudas sobre contenidos \
y estrategias didácticas.

Responde ÚNICAMENTE con base en los fragmentos de los libros de la SEP que se \
te proporcionan a continuación. Sigue estas reglas estrictamente:

1. Responde ÚNICAMENTE con información que esté en los fragmentos de abajo. \
   Si sabes algo por tu cuenta pero NO aparece en los fragmentos (nombres, \
   fechas, datos, ejemplos), NO lo agregues -- ni siquiera con frases como \
   "generalmente se menciona", "suele hablarse de" o "es sabido que". Si el \
   fragmento no trae un dato específico que la pregunta pide, dilo \
   explícitamente ("No encontré esto en los materiales de la SEP que tengo \
   disponibles") en vez de completarlo con lo que tú ya sabes.
2. Cita el libro cuando uses información de un fragmento, así: (Libro, pág. X). \
   Trata la página como una referencia orientativa, no como un dato exacto \
   que el docente vaya a verificar carácter por carácter -- puede variar \
   ligeramente entre ediciones del mismo libro.
3. NO uses tablas para presentar la información, sin importar cómo las \
   organices (por fragmento, por concepto, por lo que sea) -- una tabla \
   fragmenta la lectura en vez de ayudarla. SINTETIZA la información en \
   párrafos, como si tú entendieras el tema y se lo explicaras al docente \
   con tus propias palabras, apoyado en lo que dicen los libros. Desarrolla \
   las ideas con el detalle necesario para que sean útiles y aplicables.
4. Si la pregunta requiere una sugerencia pedagógica, apóyate solo en lo que \
   dicen los fragmentos recuperados; no agregues opiniones o metodologías que \
   no estén respaldadas por ellos.
5. Sé claro y práctico, con el desarrollo que la pregunta necesite -- una \
   clase completa merece extensión y detalle; una duda puntual puede \
   responderse más corto. No sacrifiques claridad ni utilidad por brevedad.
6. Si los fragmentos recuperados vienen de libros de distintos grados, \
   NO asumas ni le asignes un solo grado a tu respuesta -- acláralo \
   explícitamente (ej. "Encontré esto en libros de 2° y 5° grado, ajústalo \
   al grado de tu grupo") en vez de presentarlo como si fuera de un grado \
   específico que no se pidió.
{regla_formato}
8. Antes de decir "no encontré información", revisa con cuidado si los \
   fragmentos traen datos, ejemplos o contexto que sí se puedan aprovechar \
   para responder de forma útil, aunque no usen exactamente las mismas \
   palabras de la pregunta -- no rechaces una pregunta razonable solo \
   porque el fragmento no la contesta de forma literal.
"""

_REGLA_FORMATO_PLANO = """7. Formato: escribe como si le estuvieras platicando a otro maestro por \
   chat, no como si armaras un documento o reporte formal. NO uses \
   encabezados (###), líneas divisorias (---), ni negritas en casi cada \
   línea -- son señales de que te estás sobre-estructurando en vez de \
   simplemente explicar. Usa párrafos normales y, si hace falta, una lista \
   corta (por ejemplo los pasos de una actividad en orden) -- sin abusar de \
   ella ni de otros elementos de formato."""

_REGLA_FORMATO_MARKDOWN = """7. Formato: tu respuesta se renderiza como Markdown real. Puedes usar \
   **negritas**, listas con "-" o "1.", y encabezados con "##" cuando \
   ayuden a organizar la respuesta -- con moderación, no abuses. Si \
   necesitas mostrar una tabla, usa SIEMPRE sintaxis de tabla Markdown \
   válida y completa, con la fila separadora de encabezado, por ejemplo:
   | Columna A | Columna B |
   |---|---|
   | dato 1 | dato 2 |
   Nunca escribas una tabla a medias ni mezcles el formato de tabla con \
   texto suelto entre celdas. No pongas asteriscos sueltos que no formen \
   parte de una negrita real."""


def _build_context(chunks):
    parts = []
    for c in chunks:
        pages = f"pág. {c['page_start']}" if c["page_start"] == c["page_end"] \
            else f"págs. {c['page_start']}-{c['page_end']}"
        header = f"[{c['libro']} | {c['section_title']} | {pages}]"
        parts.append(f"{header}\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def _limpiar_formato_markdown(texto):
    """
    Red de seguridad determinística para los chats en texto plano (ej. el
    chat de planeación de Cuali): el prompt le pide al modelo no usar
    encabezados/tablas/etc, pero gpt-oss-20b no siempre obedece. En vez de
    depender de que el modelo se porte bien, esto limpia el resultado con
    código -- garantizado. NO se aplica cuando allow_markdown=True (el
    consumidor sí sabe renderizar markdown real).
    """
    if not texto:
        return texto

    lineas = texto.split("\n")
    resultado = []

    for linea in lineas:
        stripped = linea.strip()

        if re.fullmatch(r"[-*_]{3,}", stripped):
            continue

        if re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", stripped) and "-" in stripped:
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            celdas = [c.strip() for c in stripped.strip("|").split("|")]
            celdas = [c for c in celdas if c]
            if celdas:
                resultado.append(" — ".join(celdas))
            continue

        linea = re.sub(r"^#{1,6}\s*", "", linea)
        linea = re.sub(r"^>\s*", "", linea)

        resultado.append(linea)

    texto_limpio = "\n".join(resultado)
    texto_limpio = re.sub(r"\*\*(.+?)\*\*", r"\1", texto_limpio)
    texto_limpio = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"\1", texto_limpio)
    texto_limpio = re.sub(r"\n{3,}", "\n\n", texto_limpio)

    return texto_limpio.strip()


def _extra_headers():
    """Solo se usa en el cliente OpenAI por default (chat_fn=None). Si tu
    servidor pide HTTP Basic Auth y estás usando ask() de forma standalone
    (sin pasar chat_fn), define LLM_USER/LLM_PASSWORD."""
    if config.LLM_USER and config.LLM_PASSWORD:
        token = base64.b64encode(f"{config.LLM_USER}:{config.LLM_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return None


def _default_chat_fn(messages):
    if OpenAI is None:
        raise RuntimeError("El paquete 'openai' no está instalado.")
    client = OpenAI(api_key=config.OPENAI_API_KEY or "placeholder", base_url=config.OPENAI_BASE_URL)
    response = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=messages,
        temperature=0.2,
        extra_headers=_extra_headers(),
    )
    return response.choices[0].message.content


def ask(question, grado=None, materia=None, history=None, extra_context=None,
        chat_fn=None, retrieval_query=None, allow_markdown=False):
    """
    question: pregunta del docente (se muestra completa al modelo)
    grado: opcional, ej. "3" -> filtra la búsqueda a libros de ese grado
    materia: opcional, ej. "Proyectos de Aula" (el "tipo de libro", no el
             campo formativo NEM -- ver nota en README)
    history: lista opcional de mensajes previos [{"role": "user"/"assistant", "content": "..."}]
             para mantener contexto conversacional
    extra_context: opcional, texto libre que se agrega al system prompt sin
             quitar las reglas de anti-alucinación -- útil para inyectar
             contexto de la app (ej. grado/grupo/PDA/tema de una planeación
             específica en Cuali).
    chat_fn: opcional, función personalizada (messages: list[dict]) -> str
             para usar un cliente de chat ya configurado en tu proyecto en
             vez del cliente OpenAI por default de este módulo.
    retrieval_query: opcional, texto alternativo a usar SOLO para la
             búsqueda (embeddings + BM25), distinto de `question` que ve el
             modelo. Útil para no diluir la búsqueda con menciones de grado
             o con contenido de archivos adjuntos pegado a la pregunta.
    allow_markdown: si el chat que consume esto SÍ renderiza Markdown real
             (como el chat libre de Cuali), pon True -- el modelo puede usar
             negritas/encabezados/tablas válidas, y NO se limpia el
             resultado. Si False (default), se le pide texto plano y se
             limpia cualquier markdown que se cuele de todas formas.

    Regresa: {"answer": str, "sources": list[dict]}
    """
    chunks = hybrid_search(retrieval_query or question, grado=grado, materia=materia)

    if not chunks:
        return {
            "answer": "No encontré información relacionada en los libros de la SEP disponibles. "
                      "¿Puedes darme más contexto (grado, materia) o reformular la pregunta?",
            "sources": [],
        }

    context = _build_context(chunks)

    regla_formato = _REGLA_FORMATO_MARKDOWN if allow_markdown else _REGLA_FORMATO_PLANO
    system_prompt = SYSTEM_PROMPT_BASE.format(regla_formato=regla_formato)
    if extra_context:
        system_prompt = f"{system_prompt}\n\nContexto adicional de esta conversación:\n{extra_context}"

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({
        "role": "user",
        "content": f"Fragmentos recuperados de los libros de la SEP:\n\n{context}\n\n"
                    f"Pregunta del docente: {question}",
    })

    fn = chat_fn or _default_chat_fn
    answer = fn(messages)
    if not allow_markdown:
        answer = _limpiar_formato_markdown(answer)

    sources = [{
        "libro": c["libro"],
        "materia": c["materia"],
        "seccion": c["section_title"],
        "paginas": f"{c['page_start']}-{c['page_end']}",
    } for c in chunks]

    return {"answer": answer, "sources": sources}


if __name__ == "__main__":
    # Ejemplo rápido de uso por consola (usa el cliente OpenAI por default)
    pregunta = input("Pregunta del docente: ")
    resultado = ask(pregunta)
    print("\n--- RESPUESTA ---")
    print(resultado["answer"])
    print("\n--- FUENTES ---")
    for s in resultado["sources"]:
        print(f"- {s['libro']} | {s['seccion']} | págs. {s['paginas']}")