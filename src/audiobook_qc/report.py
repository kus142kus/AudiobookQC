"""Word reports modelled on the supplied 'Ключевые ошибки озвучки' example."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from .analysis import fmt_time, effective_severity

CATEGORIES = {
    "repeat": "Повтор / дубль", "omission": "Пропуск фрагмента",
    "replacement": "Замена / оговорка", "extra": "Лишняя фраза",
    "uncertain": "Проверить вручную", "profanity": "Возможная бранная реплика", "clipping": "Возможная перегрузка звука"
}
COLORS = {"navy": "173C60", "blue": "236B9F", "light": "EAF2F8", "muted": "536679"}


def cell_shading(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def set_cell_text(cell, value: str, is_header=False):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(value)
    run.font.name = "Arial"
    run.font.size = Pt(8 if not is_header else 8.3)
    run.font.bold = is_header
    if is_header:
        run.font.color.rgb = RGBColor(255, 255, 255)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _build_doc(title: str, subtitle: str, rows: list[dict], metadata: dict) -> Document:
    d = Document()
    sec = d.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.left_margin = Cm(1.6)
    sec.right_margin = Cm(1.6)
    sec.top_margin = Cm(1.8)
    sec.bottom_margin = Cm(1.5)
    normal = d.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9)
    normal.font.color.rgb = RGBColor.from_string(COLORS["navy"])
    normal.paragraph_format.space_after = Pt(5)
    p = d.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(title)
    r.bold = True
    r.font.size = Pt(17)
    r.font.color.rgb = RGBColor.from_string(COLORS["navy"])
    p = d.add_paragraph(subtitle)
    p.style = normal
    summary = d.add_table(rows=1, cols=1)
    summary.autofit = False
    cell = summary.cell(0,0)
    cell_shading(cell, COLORS["light"])
    cell.text = f"Файл: {metadata['audio_name']}    |    Текст: {metadata['text_name']}\n" + (
        f"Диапазон: {metadata['range']}    |    Отобрано кандидатов: {len(rows)} из {metadata['total']}\n"
        f"Возможные повторы: {sum(r['kind']=='repeat' for r in rows)}    |    "
        f"Отмечены как ложные: {sum(r.get('review')=='false' for r in rows)}"
    )
    if metadata.get('dual_mode') and metadata.get('not_rechecked'):
        cell.text += (f"\nНе проверены второй моделью: {metadata['not_rechecked']}. "
                      "Они сохранены в полном журнале, а не удалены.")
    for p in cell.paragraphs:
        p.paragraph_format.space_after = Pt(1)
        for r in p.runs:
            r.font.size = Pt(9)
    d.add_paragraph()
    def issue_table(items: list[dict]):
        tbl = d.add_table(rows=1, cols=4)
        tbl.style = "Table Grid"
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False
        widths = [Cm(2.0), Cm(5.2), Cm(5.2), Cm(5.2)]
        for i,(head,width) in enumerate(zip(["Таймкод", "Произнесено", "По тексту", "Причина / действие"], widths)):
            tbl.columns[i].width = width
            set_cell_text(tbl.cell(0,i), head, True)
            cell_shading(tbl.cell(0,i), COLORS["blue"])
        trPr = tbl.rows[0]._tr.get_or_add_trPr()
        repeating = OxmlElement("w:tblHeader")
        repeating.set(qn("w:val"), "true")
        trPr.append(repeating)
        if not items:
            empty = tbl.add_row().cells
            for i in range(4): empty[i].width = widths[i]
            for c, val in zip(empty,["—", "Серьёзных расхождений пока не найдено", "—", "Это не гарантия отсутствия ошибок"]):
                set_cell_text(c,val)
        for idx, issue in enumerate(items):
            cells = tbl.add_row().cells
            for i in range(4): cells[i].width = widths[i]
            timestamp = fmt_time(issue["start"])
            if issue["end"] > issue["start"] + 3:
                timestamp += "\n– " + fmt_time(issue["end"])
            status = {"fixed": "Исправлено", "false": "Отклонено", "todo": "Не проверено"}.get(issue.get("review"), "Не проверено")
            fields = [timestamp, issue.get("spoken", ""), issue.get("expected", ""),
                      f"{CATEGORIES.get(issue['kind'], issue['kind'])}\n{issue.get('reason','')}\nСтатус: {status}"]
            for i, content in enumerate(fields):
                set_cell_text(cells[i], content)
                if idx % 2:
                    cell_shading(cells[i], "F3F7FA")
        return tbl

    repeating_issues = [r for r in rows if r["kind"] == "repeat"]
    audio_issues = [r for r in rows if r["kind"] == "clipping"]
    profanity_issues = [r for r in rows if r["kind"] == "profanity"]
    text_issues = [r for r in rows if r["kind"] not in ("repeat", "clipping", "profanity")]
    sections = [("1. ПОВТОРЫ И ДУБЛИ", repeating_issues),
                ("2. ОШИБКИ ЧТЕНИЯ И ПРОПУСКИ", text_issues)]
    if audio_issues:
        sections.append(("3. ТЕХНИЧЕСКИЕ ЗАМЕЧАНИЯ", audio_issues))
    if profanity_issues:
        sections.append(("4. ВОЗМОЖНЫЕ БРАННЫЕ РЕПЛИКИ", profanity_issues))
    for heading, items in sections:
        h = d.add_paragraph()
        h.paragraph_format.space_before = Pt(10)
        r = h.add_run(heading)
        r.font.bold = True
        r.font.color.rgb = RGBColor.from_string(COLORS["blue"])
        r.font.size = Pt(11)
        issue_table(items)
    if metadata.get("uncertain", 0):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(9)
        p.add_run(f"Внимание: {metadata['uncertain']} участок(ов) не удалось надёжно сопоставить с рукописью. ").bold=True
        p.add_run("Они не включены в краткий список подтверждённых кандидатов. Проверьте эти места в приложении.")
    note = d.add_paragraph()
    note.paragraph_format.space_before = Pt(8)
    note.add_run("Важно: ").bold = True
    note.add_run("это автоматические предположения на основе распознавания речи. "
                 "Все значимые находки нужно прослушать. Ударения и все фоновые звуки "
                 "в этой версии не проверяются надёжно.")
    note.runs[-1].font.size = Pt(8)
    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(f"Audiobook QC · Локальный анализ · {datetime.now():%d.%m.%Y}").font.size = Pt(8)
    return d


def generate_report(path: Path, project: dict, issues: list[dict], start: float, end: float, final: bool=False, reviews: dict | None = None):
    reviews = reviews or {}
    rows = []
    for item in sorted(issues, key=lambda x: x["start"]):
        if effective_severity(item) != "high":
            continue
        if (project.get('report_policy') in ('second-pass', 'dual-gigaam')
                and item.get('kind') in ('replacement', 'omission', 'extra', 'repeat')
                and not item.get('rechecked')):
            continue  # Unverified initial ASR hypothesis remains only in the full log.
        if (project.get('report_policy') == 'dual-gigaam'
                and item.get('kind') in ('replacement', 'omission', 'extra', 'repeat')
                and not item.get('dual_confirmed')):
            continue  # Do not call a single-ASR hypothesis a serious finding.
        row = item.copy()
        row["review"] = reviews.get(item["id"], "todo")
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    label = "ВЕСЬ АУДИОФАЙЛ" if final else f"ЧАСТЬ · {fmt_time(start)}–{fmt_time(end)}"
    d = _build_doc("ПОДОЗРИТЕЛЬНЫЕ МЕСТА ДЛЯ ПРОВЕРКИ", label,
                   rows, {"audio_name":project["audio_name"], "text_name":project["text_name"],
                          "range": f"{fmt_time(start)}–{fmt_time(end)}",
                          "total":len(issues),
                          "dual_mode":project.get('report_policy') == 'dual-gigaam',
                          "not_rechecked":sum(i.get('kind') in ('replacement','omission','extra','repeat')
                                                and not i.get('dual_checked') for i in issues),
                          "uncertain":sum(i["kind"] == "uncertain" for i in issues)})
    # Atomic replacement so a download never sees a half-written DOCX.
    tmp = path.with_suffix(".pending.docx")
    d.save(str(tmp))
    tmp.replace(path)
