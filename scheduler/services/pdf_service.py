"""
PDF export for the generated schedule.

Important: this module never recalculates D/U/S itself. It is always
handed the same list of ScheduledTask objects that the schedule page
displays, so the PDF can never drift from what the user sees on screen.
"""

from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def render_schedule_pdf(username: str, scheduled_tasks) -> HttpResponse:
    """Build a PDF of the given schedule and return it as a download response."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    elements = [
        Paragraph('Task Schedule', styles['Title']),
        Paragraph(f'Generated for: {username}', styles['Normal']),
        Spacer(1, 10 * mm),
    ]

    header = ['#', 'Task', 'Priority', 'Deadline', 'Duration (min)']
    rows = [header]
    for index, item in enumerate(scheduled_tasks, start=1):
        # Order, priority and deadline still come straight from the same
        # ScheduledTask the web page uses, so the PDF's row ORDER still
        # matches the schedule exactly — only the Urgency/Score columns
        # are hidden from this printed view.
        rows.append([
            str(index),
            item.task.title,
            str(item.task.priority),
            timezone.localtime(item.task.deadline).strftime('%Y-%m-%d %H:%M'),
            str(item.task.duration_minutes),
        ])

    if len(rows) == 1:
        elements.append(Paragraph('No tasks to schedule.', styles['Normal']))
    else:
        table = Table(rows, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)

    doc.build(elements)

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="schedule.pdf"'
    return response
