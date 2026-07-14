import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUTPUT_DIR = "generated_pdfs"


def generar_pdf_planeacion(planeacion_id: str, datos: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"planeacion_{planeacion_id}.pdf")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TituloPlaneacion", parent=styles["Title"], fontSize=18, spaceAfter=6)
    subtitle_style = ParagraphStyle("Subtitulo", parent=styles["Normal"], fontSize=11, textColor="#6B6558", spaceAfter=16)
    session_title_style = ParagraphStyle("SesionTitulo", parent=styles["Heading2"], fontSize=13, spaceBefore=14, spaceAfter=6)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=10, textColor="#3C5F91", spaceBefore=6)
    body_style = styles["Normal"]

    doc = SimpleDocTemplate(
        path, pagesize=letter,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )

    story = [
        Paragraph(f"Planeación — {datos['campo_formativo']}", title_style),
        Paragraph(f"{datos['grado']}° \"{datos['grupo']}\" · Contenido NEM: {datos['contenido']}", subtitle_style),
        Paragraph(f"<b>Tema u objetivo:</b> {datos['tema']}", body_style),
        Spacer(1, 12),
    ]

    for sesion in datos["sesiones"]:
        story.append(Paragraph(f"Sesión {sesion['numero']}", session_title_style))
        story.append(Paragraph(f"<b>Objetivo:</b> {sesion['objetivo']}", body_style))
        story.append(Paragraph("Actividades:", label_style))
        for act in sesion["actividades"]:
            story.append(Paragraph(f"• {act}", body_style))
        story.append(Paragraph("Materiales:", label_style))
        story.append(Paragraph(", ".join(sesion["materiales"]), body_style))
        story.append(Paragraph("Evaluación:", label_style))
        story.append(Paragraph(sesion["evaluacion"], body_style))
        story.append(Spacer(1, 8))

    doc.build(story)
    return path