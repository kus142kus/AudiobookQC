"""High-legibility, printer-friendly Word reports for audiobook editorial QC.

This module presents *candidates for listening*, not proven narrator mistakes.
Source alignment / verified filtering happens in analysis and generate_report.
All important information remains in each part's saved JSON/CSV data.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .analysis import fmt_time, effective_severity

CATEGORIES = {
    'repeat': 'Повтор / дубль', 'omission': 'Пропуск текста',
    'replacement': 'Возможная оговорка', 'extra': 'Лишняя фраза',
    'uncertain': 'Проверить вручную', 'profanity': 'Возможная бранная реплика',
    'clipping': 'Возможная перегрузка', 'sound_event': 'Возможный посторонний звук',
    'hum': 'Возможный гул', 'hiss': 'Шипение или фон',
    'click': 'Возможный щелчок', 'level_jump': 'Перепад громкости',
    'stress': 'Возможное ударение', 'intonation': 'Возможная интонация',
}
C = {
    'navy': '18314B', 'blue': '1E5B92', 'text': '1E2F3F',
    'muted': '536577', 'rule': 'AEBECC', 'soft': 'E0E8EF',
    'stripe': 'F3F7FA', 'white': 'FFFFFF', 'amber': 'FFF2DB',
    'amber_text': '855116', 'green': '21644E', 'pale': 'E8F1F7',
}
ADVISORY_KINDS = {
    'clipping', 'sound_event', 'hum', 'hiss', 'click',
    'level_jump', 'stress', 'intonation',
}
READING_KINDS = {'replacement', 'omission', 'extra', 'repeat'}


def _font(run, size=10, *, bold=False, color='text'):
    run.font.name = 'Arial'
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(C.get(color, color))
    return run


def _shade(cell, color):
    tcpr = cell._tc.get_or_add_tcPr()
    sh = tcpr.find(qn('w:shd'))
    if sh is None:
        sh = OxmlElement('w:shd')
        tcpr.append(sh)
    sh.set(qn('w:fill'), C.get(color, color))


def _borders(table, color='rule', outer=8, inner=6):
    """Visible grid on both screen and paper, even after editing in Word."""
    pr = table._tbl.tblPr
    old = pr.find(qn('w:tblBorders'))
    if old is not None:
        pr.remove(old)
    b = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        line = OxmlElement('w:' + edge)
        line.set(qn('w:val'), 'single')
        line.set(qn('w:color'), C.get(color, color))
        line.set(qn('w:sz'), str(outer if edge in ('top','left','bottom','right') else inner))
        b.append(line)
    pr.append(b)


def _margins(cell, top=130, start=140, bottom=130, end=140):
    tcpr = cell._tc.get_or_add_tcPr()
    m = tcpr.find(qn('w:tcMar'))
    if m is None:
        m = OxmlElement('w:tcMar')
        tcpr.append(m)
    for key, val in [('top', top), ('start', start), ('bottom', bottom), ('end', end)]:
        tag = qn('w:' + key)
        el = m.find(tag)
        if el is None:
            el = OxmlElement('w:' + key)
            m.append(el)
        el.set(qn('w:w'), str(val))
        el.set(qn('w:type'), 'dxa')


def _nosplit(row):
    trpr = row._tr.get_or_add_trPr()
    if trpr.find(qn('w:cantSplit')) is None:
        trpr.append(OxmlElement('w:cantSplit'))


def _repeat_header(row):
    trpr = row._tr.get_or_add_trPr()
    if trpr.find(qn('w:tblHeader')) is None:
        h = OxmlElement('w:tblHeader')
        h.set(qn('w:val'), 'true')
        trpr.append(h)


def _brief(value, limit=155):
    value = re.sub(r'\s+', ' ', str(value or '—')).strip()
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def _p(container, text, size=9.5, *, color='text', bold=False, before=0, after=0, keep=False):
    p = container.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.keep_with_next = keep
    _font(p.add_run(str(text)), size, bold=bold, color=color)
    return p


def _cell(cell, text, *, size=9.7, bold=False, color='text', bg=None):
    cell.text = ''
    _margins(cell)
    if bg:
        _shade(cell, bg)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.14
    _font(p.add_run(_brief(text)), size, bold=bold, color=color)
    return p


def _extra(cell, text, *, size=8.3, color='muted', before=4, bold=False):
    p = cell.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.14
    _font(p.add_run(_brief(text, 190)), size, color=color, bold=bold)
    return p


def _section(doc, title, number):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(15)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    _font(p.add_run(f'{number:02d}  '), 11.2, bold=True, color='blue')
    _font(p.add_run(title), 11.2, bold=True, color='navy')


def _editorial_variant(item):
    a = ' '.join((item.get('spoken') or '').lower().split())
    b = ' '.join((item.get('expected') or '').lower().split())
    return item.get('kind') == 'replacement' and {a, b} in (
        {'мной', 'мною'}, {'тобой', 'тобою'}, {'ей', 'ею'})


def _status(review):
    return {'fixed': 'Исправлено', 'false': 'Не ошибка',
            'todo': 'Не проверено'}.get(review, 'Не проверено')


def _action(item):
    kind = item.get('kind')
    if _editorial_variant(item):
        return 'Редакционный вариант. Сверить требования к дословности.'
    if kind == 'repeat':
        return 'Возможный повтор. Прослушать начало и конец фрагмента.'
    if kind == 'omission':
        return 'Возможный пропуск. Проверить соседние предложения.'
    if kind == 'extra':
        return 'Лишние слова? Прослушать вместе с контекстом.'
    if kind == 'profanity':
        return 'Возможная реплика вне текста. Проверить на слух.'
    return 'Сравнить с рукописью и прослушать фрагмент.'


def _event_explanation(item):
    text = re.sub(r'\s+', ' ', (item.get('reason') or '').strip())
    if not text:
        text = 'Прослушать участок и проверить, присутствует ли дефект.'
    return _brief(text, 170)


def _new_table(doc, headers, widths):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Table Grid'
    t.autofit = False
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for col, w in zip(t.columns, widths):
        col.width = Cm(w)
    _borders(t)
    head = t.rows[0]
    _repeat_header(head)
    _nosplit(head)
    for i, (txt, width) in enumerate(zip(headers, widths)):
        c = head.cells[i]
        c.width = Cm(width)
        _cell(c, txt, size=8.6, color='white', bold=True, bg='navy')
    return t


def _append_reading(table, item, no):
    row = table.add_row()
    _nosplit(row)
    widths = (2.2, 4.4, 4.4, 6.3)
    tint = 'white' if no % 2 else 'stripe'
    for cell, width in zip(row.cells, widths):
        cell.width = Cm(width)
        _shade(cell, tint)
    _cell(row.cells[0], f'{no:02d}  /  {fmt_time(item.get("start", 0))}',
          size=9.1, bold=True, color='blue')
    _cell(row.cells[1], item.get('expected') or '—', size=10, bold=True)
    _cell(row.cells[2], item.get('spoken') or '—', size=10, bold=True)
    _cell(row.cells[3], CATEGORIES.get(item.get('kind'), 'Расхождение'),
          size=9.3, bold=True, color='navy')
    _extra(row.cells[3], _action(item), size=8.5)
    if item.get('review') in (None, 'todo'):
        _extra(row.cells[3], '□ Исправить    □ Не ошибка    □ Позже', size=8.2, before=6)
    else:
        _extra(row.cells[3], 'Отметка: ' + _status(item.get('review')),
               size=8.6, color='green', bold=True, before=6)


def _append_advisory(table, item, no):
    row = table.add_row()
    _nosplit(row)
    widths = (2.2, 4.5, 10.6)
    tint = 'white' if no % 2 else 'stripe'
    for cell, width in zip(row.cells, widths):
        cell.width = Cm(width)
        _shade(cell, tint)
    _cell(row.cells[0], f'{no:02d}  /  {fmt_time(item.get("start",0))}',
          size=9.1, bold=True, color='blue')
    _cell(row.cells[1], CATEGORIES.get(item.get('kind'), 'Проверить звук'),
          size=9.6, bold=True)
    _cell(row.cells[2], _event_explanation(item), size=8.8)
    _extra(row.cells[2], 'Не подтверждено — прослушать.', size=8.2,
           color='amber_text', bold=True, before=4)


def _build_doc(title: str, subtitle: str, rows: list[dict], metadata: dict,
               advisory: list[dict] | None = None) -> Document:
    d = Document()
    sec = d.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21)
    sec.left_margin = sec.right_margin = Cm(1.85)
    sec.top_margin, sec.bottom_margin = Cm(1.75), Cm(1.65)
    sec.header_distance, sec.footer_distance = Cm(.8), Cm(.85)

    normal = d.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(9.5)
    normal.font.color.rgb = RGBColor.from_string(C['text'])
    normal.paragraph_format.space_after = Pt(0)

    head = sec.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _font(head.add_run('AUDIOBOOK QC  ·  ПРОВЕРКА ОЗВУЧКИ'), 7.8,
          bold=True, color='muted')
    _p(d, title, 18, color='navy', bold=True, after=2)
    _p(d, subtitle, 10.1, color='blue', bold=True, after=11)

    # Clear, bordered metadata block: no cramped inline labels.
    meta = _new_table(d, ('АУДИО', 'РУКОПИСЬ', 'ДИАПАЗОН'), (5.7, 5.7, 5.9))
    mrow = meta.add_row()
    _nosplit(mrow)
    for c, w in zip(mrow.cells, (5.7, 5.7, 5.9)):
        c.width = Cm(w)
    _cell(mrow.cells[0], metadata.get('audio_name', '—'), size=9, bold=True, bg='stripe')
    _cell(mrow.cells[1], metadata.get('text_name', '—'), size=9, bold=True, bg='stripe')
    _cell(mrow.cells[2], metadata.get('range', ''), size=9, bold=True, bg='stripe')
    _extra(mrow.cells[2], f'Отобрано кандидатов: {len(rows)}', size=8.2, before=4)

    advisory = advisory or []
    rows = [x for x in rows if x.get('kind') not in ADVISORY_KINDS]
    primary = [x for x in rows if not _editorial_variant(x)]
    editorial = [x for x in rows if _editorial_variant(x)]
    _p(d, f'Основные находки: {len(primary)}   |   Редакционные варианты: {len(editorial)}   |   Звук и произношение: {len(advisory)}',
       size=9.1, color='navy', bold=True, before=8, after=5)
    _p(d, f'Всего автоматических кандидатов: {metadata.get("total", len(rows))}. '
       'Все замечания требуют проверки на слух.',
       size=8.4, color='muted', after=3)
    if metadata.get('dual_mode') and metadata.get('not_rechecked'):
        _p(d, f'Не прошли вторую речевую модель: {metadata["not_rechecked"]}. '
           'Они сохранены в полном журнале, не удалены.',
           size=8.3, color='amber_text', after=3)

    section_no = 1
    _section(d, 'Ошибки чтения, пропуски и повторы', section_no)
    if primary:
        table = _new_table(d,
            ('№ / ВРЕМЯ', 'ПО РУКОПИСИ', 'РАСПОЗНАНО', 'ТИП / ЧТО СДЕЛАТЬ'),
            (2.2, 4.4, 4.4, 6.3))
        for i, item in enumerate(primary, 1):
            _append_reading(table, item, i)
    else:
        _p(d, 'ПОВТОРЫ И ДУБЛИ: серьёзных кандидатов нет. Это не гарантирует отсутствия ошибок.',
           size=9.4, color='muted', before=3, after=4)

    if editorial:
        section_no += 1
        _section(d, 'Небольшие редакционные отличия', section_no)
        _p(d, 'Разные формы слов могут звучать естественно; проверяйте только при требовании дословности.',
           size=8.5, color='muted', after=5)
        table = _new_table(d,
            ('№ / ВРЕМЯ', 'ПО РУКОПИСИ', 'РАСПОЗНАНО', 'ТИП / ЧТО СДЕЛАТЬ'),
            (2.2, 4.4, 4.4, 6.3))
        for i, item in enumerate(editorial, 1):
            _append_reading(table, item, i)

    if advisory:
        section_no += 1
        _section(d, 'ЗВУК, УДАРЕНИЯ И ИНТОНАЦИЯ — ТОЛЬКО ДЛЯ ПРОСЛУШИВАНИЯ', section_no)
        _p(d, 'События ниже не являются установленным браком: это предположения алгоритмов.',
           size=8.6, color='amber_text', after=6)
        table = _new_table(d,
            ('№ / ВРЕМЯ', 'ВОЗМОЖНОЕ СОБЫТИЕ', 'ОБЪЯСНЕНИЕ / ДЕЙСТВИЕ'),
            (2.2, 4.5, 10.6))
        for i, item in enumerate(advisory[:35], 1):
            _append_advisory(table, item, i)
        if len(advisory) > 35:
            _p(d, f'Остальные {len(advisory)-35} предположений доступны в полном журнале.',
               size=8.5, color='muted', before=4)

    if metadata.get('uncertain'):
        _p(d, f'Не удалось надёжно сопоставить с рукописью: {metadata["uncertain"]} участков. '
           'Проверьте полный журнал.', size=8.4, color='amber_text', before=9)
    _p(d, 'Порядок работы: откройте таймкод в приложении → прослушайте контекст → '
       'отметьте «Исправить» или «Не ошибка».', size=8.2, color='muted', before=12)
    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _font(foot.add_run('Audiobook QC  ·  '+datetime.now().strftime('%d.%m.%Y')+'  ·  Стр. '),
          7.6, color='muted')
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    foot._p.append(field)
    return d


def generate_report(path: Path, project: dict, issues: list[dict], start: float, end: float,
                    final: bool = False, reviews: dict | None = None):
    reviews = reviews or {}
    rows = []
    for item in sorted(issues, key=lambda x: x['start']):
        if item.get('kind') in ADVISORY_KINDS or effective_severity(item) != 'high':
            continue
        if (project.get('report_policy') in ('second-pass', 'dual-gigaam')
                and item.get('kind') in READING_KINDS and not item.get('rechecked')):
            continue
        if (project.get('report_policy') == 'dual-gigaam'
                and item.get('kind') in READING_KINDS and not item.get('dual_confirmed')):
            continue
        row = item.copy()
        row['review'] = reviews.get(item['id'], 'todo')
        rows.append(row)

    advisory = []
    for item in sorted(issues, key=lambda x: x['start']):
        if item.get('kind') in ADVISORY_KINDS and reviews.get(item.get('id')) != 'false':
            row = item.copy()
            row['review'] = reviews.get(item.get('id'), 'todo')
            advisory.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    label = 'Вся запись' if final else f'Часть · {fmt_time(start)}–{fmt_time(end)}'
    doc = _build_doc('Проверка озвучки', label, rows, {
        'audio_name': project['audio_name'], 'text_name': project['text_name'],
        'range': f'{fmt_time(start)}–{fmt_time(end)}',
        'total': len(issues),
        'dual_mode': project.get('report_policy') == 'dual-gigaam',
        'not_rechecked': sum(i.get('kind') in READING_KINDS and not i.get('dual_checked') for i in issues),
        'uncertain': sum(i.get('kind') == 'uncertain' for i in issues),
    }, advisory=advisory)
    tmp = path.with_suffix('.pending.docx')
    doc.save(str(tmp))
    tmp.replace(path)
