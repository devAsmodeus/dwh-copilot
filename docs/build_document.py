"""Сборка итогового документа по техническому заданию.

Запуск:
    python docs/build_document.py

Результат: docs/Кругликовский_ДЗ_ИИ-инженер.pdf

Оформление деловое, чёрно-белое, гарнитура Times New Roman. Цветовых выделений
нет намеренно: документ рассчитан на печать и на чтение с экрана в равной мере.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT = Path(__file__).parent / "Кругликовский_ДЗ_ИИ-инженер.pdf"

AUTHOR = "Кругликовский Павел Александрович"
TITLE = "MVP ИИ-аналитики корпоративного хранилища данных"
SUBTITLE = "Архитектура, план реализации и перспективные инициативы"

BLACK = colors.black
GREY = colors.Color(0.45, 0.45, 0.45)
HAIRLINE = colors.Color(0.75, 0.75, 0.75)
SHADE = colors.Color(0.93, 0.93, 0.93)


def register_fonts() -> tuple[str, str, str]:
    """Подключает гарнитуру с поддержкой кириллицы."""
    candidates = [
        ("Times", "C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf",
         "C:/Windows/Fonts/timesi.ttf"),
        ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
    ]
    for name, regular, bold, italic in candidates:
        if Path(regular).exists():
            pdfmetrics.registerFont(TTFont(name, regular))
            pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
            pdfmetrics.registerFont(TTFont(f"{name}-Italic", italic))
            pdfmetrics.registerFontFamily(
                name, normal=name, bold=f"{name}-Bold", italic=f"{name}-Italic"
            )
            break
    else:
        raise SystemExit("Не найдена гарнитура с поддержкой кириллицы")

    mono = "Mono"
    for path in ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont(mono, path))
            break
    else:
        mono = "Courier"

    return name, f"{name}-Bold", mono


FONT, FONT_BOLD, FONT_MONO = register_fonts()


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=FONT_BOLD, fontSize=22,
            leading=27, alignment=TA_CENTER, textColor=BLACK, spaceAfter=6),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=FONT, fontSize=13, leading=17,
            alignment=TA_CENTER, textColor=GREY, spaceAfter=4),
        "cover_meta": ParagraphStyle(
            "cover_meta", fontName=FONT, fontSize=11, leading=16,
            alignment=TA_CENTER, textColor=BLACK),
        "h1": ParagraphStyle(
            "h1", fontName=FONT_BOLD, fontSize=16, leading=20, textColor=BLACK,
            spaceBefore=2, spaceAfter=10),
        "h2": ParagraphStyle(
            "h2", fontName=FONT_BOLD, fontSize=12.5, leading=16, textColor=BLACK,
            spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle(
            "h3", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=BLACK,
            spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle(
            "body", fontName=FONT, fontSize=10.5, leading=14.5,
            alignment=TA_JUSTIFY, textColor=BLACK, spaceAfter=6),
        "bullet": ParagraphStyle(
            "bullet", fontName=FONT, fontSize=10.5, leading=14.5,
            alignment=TA_JUSTIFY, textColor=BLACK, leftIndent=14,
            bulletIndent=4, spaceAfter=3),
        "note": ParagraphStyle(
            "note", fontName=FONT, fontSize=9.5, leading=13, alignment=TA_JUSTIFY,
            textColor=GREY, leftIndent=10, spaceBefore=4, spaceAfter=8),
        "cell": ParagraphStyle(
            "cell", fontName=FONT, fontSize=9, leading=12, textColor=BLACK),
        "cell_head": ParagraphStyle(
            "cell_head", fontName=FONT_BOLD, fontSize=9, leading=12, textColor=BLACK),
        "caption": ParagraphStyle(
            "caption", fontName=FONT, fontSize=9, leading=12, textColor=GREY,
            alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
        "mono": ParagraphStyle(
            "mono", fontName=FONT_MONO, fontSize=7.4, leading=9.6, textColor=BLACK),
    }


S = build_styles()


# ─────────────────────────────────────────────────────────────────────────────
# Помощники построения
# ─────────────────────────────────────────────────────────────────────────────

def p(text: str, style: str = "body"):
    return Paragraph(text, S[style])


def h1(text: str):
    return Paragraph(text, S["h1"])


def h2(text: str):
    return Paragraph(text, S["h2"])


def h3(text: str):
    return Paragraph(text, S["h3"])


def bullets(items: list[str]):
    return [Paragraph(item, S["bullet"], bulletText="\u2013") for item in items]


def numbered(items: list[str]):
    return [
        Paragraph(item, S["bullet"], bulletText=f"{index}.")
        for index, item in enumerate(items, start=1)
    ]


def table(rows: list[list[str]], widths: list[float], header: bool = True):
    """Строит таблицу в чёрно-белом оформлении."""
    data = []
    for row_index, row in enumerate(rows):
        style_name = "cell_head" if (header and row_index == 0) else "cell"
        data.append([Paragraph(str(cell), S[style_name]) for cell in row])

    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), SHADE),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, BLACK),
        ]
    return Table(data, colWidths=widths, style=TableStyle(style), repeatRows=1 if header else 0)


def figure(text: str, caption: str = ""):
    """Схема, набранная моноширинным шрифтом."""
    block = Preformatted(text, S["mono"])
    framed = Table(
        [[block]],
        colWidths=[168 * mm],
        style=TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ]),
    )
    parts = [framed]
    if caption:
        parts.append(p(caption, "caption"))
    return parts


def keep(*flowables):
    flat = []
    for item in flowables:
        flat.extend(item if isinstance(item, list) else [item])
    return KeepTogether(flat)


# ─────────────────────────────────────────────────────────────────────────────
# Шаблон страницы
# ─────────────────────────────────────────────────────────────────────────────

def draw_cover(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BLACK)
    canvas.setLineWidth(1.2)
    canvas.line(25 * mm, 232 * mm, 185 * mm, 232 * mm)
    canvas.line(25 * mm, 96 * mm, 185 * mm, 96 * mm)
    canvas.restoreState()


def draw_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(GREY)
    canvas.drawString(25 * mm, 287 * mm, TITLE)
    canvas.drawRightString(185 * mm, 287 * mm, AUTHOR)
    canvas.setStrokeColor(HAIRLINE)
    canvas.setLineWidth(0.4)
    canvas.line(25 * mm, 284 * mm, 185 * mm, 284 * mm)
    canvas.line(25 * mm, 17 * mm, 185 * mm, 17 * mm)
    canvas.setFillColor(BLACK)
    canvas.drawCentredString(105 * mm, 11 * mm, str(doc.page))
    canvas.restoreState()


def make_document() -> BaseDocTemplate:
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=25 * mm, rightMargin=25 * mm,
        topMargin=25 * mm, bottomMargin=22 * mm,
        title=TITLE, author=AUTHOR, subject=SUBTITLE,
    )
    frame_cover = Frame(25 * mm, 22 * mm, 160 * mm, 250 * mm, id="cover")
    frame_body = Frame(25 * mm, 22 * mm, 160 * mm, 253 * mm, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_cover], onPage=draw_cover),
        PageTemplate(id="body", frames=[frame_body], onPage=draw_page),
    ])
    return doc


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    from document_content import build_story  # noqa: E402

    document = make_document()
    document.build(build_story())
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"Документ собран: {OUTPUT}")
    print(f"Размер: {size_kb:.0f} КБ")
