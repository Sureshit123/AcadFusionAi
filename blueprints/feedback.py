import io
import datetime
import pandas as pd
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from models.database import db_instance

feedback_bp = Blueprint('feedback', __name__)

@feedback_bp.route('/feedback')
def index():
    """Student feedback submission form page."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('feedback/index.html')

@feedback_bp.route('/feedback/admin')
def admin_dashboard():
    """Admin feedback analytics & management dashboard."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('feedback/admin.html')

@feedback_bp.route('/api/feedback/form_data')
def get_form_data():
    """
    Dynamically loads subjects & faculty assigned from Subject Mapping (Institution Hub).
    Returns subjects grouped with Subject Code, Subject Name, Faculty Name, Credits, Semester, Department.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    subjects_db = db_instance.get_subjects(user_id)
    departments = db_instance.get_departments(user_id)
    
    formatted_subjects = []
    for sub in subjects_db:
        faculty = sub.get('faculty_name', '').strip()
        if not faculty:
            faculty = 'Unassigned'
        formatted_subjects.append({
            'subject_code': sub.get('subject_code', '').strip().upper(),
            'subject_name': sub.get('subject_name', '').strip(),
            'faculty_name': faculty,
            'credits': sub.get('credits', 4),
            'semester': sub.get('semester', 1),
            'department': sub.get('department', 'General'),
            'subject_type': sub.get('subject_type', 'Regular')
        })

    return jsonify({
        'subjects': formatted_subjects,
        'departments': departments
    })

@feedback_bp.route('/api/feedback/submit', methods=['POST'])
def submit_feedback():
    """
    Validates and stores student feedback.
    Prevents duplicate submissions for the same student USN, subject, and semester.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    user_id = session['user_id']
    data = request.json or {}

    student_usn = str(data.get('student_usn', '')).strip().upper()
    subject_code = str(data.get('subject_code', '')).strip().upper()
    faculty_name = str(data.get('faculty_name', '')).strip()
    semester = data.get('semester')
    department = str(data.get('department', 'General')).strip()

    if not subject_code or not faculty_name:
        return jsonify({'error': 'Subject code and faculty name are required.'}), 400

    # Rating validation (1-5 range for each component)
    rating_keys = ['overall_rating', 'teaching_rating', 'communication_rating', 'knowledge_rating', 'doubt_rating', 'practical_rating']
    validated_ratings = {}
    for key in rating_keys:
        val = data.get(key)
        try:
            val_int = int(val)
            if not (1 <= val_int <= 5):
                return jsonify({'error': f'Rating for {key} must be between 1 and 5 stars.'}), 400
            validated_ratings[key] = val_int
        except (ValueError, TypeError):
            return jsonify({'error': f'Invalid rating value for {key}.'}), 400

    # Duplicate check
    is_anon = bool(data.get('anonymous', True))
    if not is_anon and student_usn:
        if db_instance.check_existing_feedback(user_id, student_usn, subject_code, semester):
            return jsonify({'error': f'Feedback has already been submitted for USN {student_usn} on subject {subject_code}.'}), 400

    feedback_doc = {
        'student_usn': 'ANONYMOUS' if is_anon else (student_usn or 'STUDENT'),
        'subject_code': subject_code,
        'subject_name': str(data.get('subject_name', '')).strip(),
        'faculty_name': faculty_name,
        'credits': data.get('credits', 4),
        'semester': semester,
        'department': department,
        'overall_rating': validated_ratings['overall_rating'],
        'teaching_rating': validated_ratings['teaching_rating'],
        'communication_rating': validated_ratings['communication_rating'],
        'knowledge_rating': validated_ratings['knowledge_rating'],
        'doubt_rating': validated_ratings['doubt_rating'],
        'practical_rating': validated_ratings['practical_rating'],
        'comments': str(data.get('comments', '')).strip(),
        'anonymous': is_anon
    }

    feedback_id = db_instance.save_feedback(user_id, feedback_doc)
    if feedback_id:
        return jsonify({'success': True, 'feedback_id': feedback_id, 'message': 'Feedback submitted successfully!'})
    return jsonify({'error': 'Failed to save feedback.'}), 500

@feedback_bp.route('/api/feedback/admin_data')
def admin_data():
    """Returns analytics, teacher scores, department breakdowns, and chart metrics for Admin Dashboard."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = session['user_id']
    dept = request.args.get('department')
    sem = request.args.get('semester')
    sub = request.args.get('subject_code')

    filters = {}
    if dept: filters['department'] = dept
    if sem: filters['semester'] = sem
    if sub: filters['subject_code'] = sub

    analytics = db_instance.get_feedback_analytics(user_id, filters)
    raw_feedbacks = db_instance.get_feedbacks(user_id, filters)

    return jsonify({
        'analytics': analytics,
        'feedbacks': raw_feedbacks
    })

@feedback_bp.route('/api/feedback/export/excel')
def export_excel():
    """Generates and downloads an Excel report of teacher feedback analytics."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    analytics = db_instance.get_feedback_analytics(user_id)
    teacher_stats = analytics.get('teacher_stats', [])

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Teacher Feedback Analytics"
    ws.views.sheetView[0].showGridLines = True

    # Title
    ws.cell(row=1, column=1, value="AcadFusion AI - Teacher Feedback Analytics Report").font = Font(bold=True, size=14, color='1E1B4B')
    ws.cell(row=2, column=1, value=f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font = Font(size=10, color='64748B')

    headers = [
        "Faculty Name", "Subject Code", "Subject Name", "Department", "Semester",
        "Feedback Count", "Overall Avg", "Teaching Avg", "Comm. Avg", "Knowledge Avg", "Doubt Avg", "Practical Avg"
    ]

    header_fill = PatternFill(start_color='1E1B4B', end_color='1E1B4B', fill_type='solid')
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
        cell.border = thin_border

    row_idx = 5
    for t in teacher_stats:
        ws.cell(row=row_idx, column=1, value=t.get('faculty_name')).border = thin_border
        ws.cell(row=row_idx, column=2, value=t.get('subject_code')).border = thin_border
        ws.cell(row=row_idx, column=3, value=t.get('subject_name')).border = thin_border
        ws.cell(row=row_idx, column=4, value=t.get('department')).border = thin_border
        ws.cell(row=row_idx, column=5, value=t.get('semester')).border = thin_border
        ws.cell(row=row_idx, column=6, value=t.get('count')).border = thin_border
        ws.cell(row=row_idx, column=7, value=t.get('avg_overall')).border = thin_border
        ws.cell(row=row_idx, column=8, value=t.get('avg_teaching')).border = thin_border
        ws.cell(row=row_idx, column=9, value=t.get('avg_communication')).border = thin_border
        ws.cell(row=row_idx, column=10, value=t.get('avg_knowledge')).border = thin_border
        ws.cell(row=row_idx, column=11, value=t.get('avg_doubt')).border = thin_border
        ws.cell(row=row_idx, column=12, value=t.get('avg_practical')).border = thin_border
        row_idx += 1

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f'Teacher_Feedback_Report_{datetime.date.today()}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@feedback_bp.route('/api/feedback/export/csv')
def export_csv():
    """Generates and downloads a CSV report of teacher feedback records."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    analytics = db_instance.get_feedback_analytics(user_id)
    teacher_stats = analytics.get('teacher_stats', [])

    rows = []
    for t in teacher_stats:
        rows.append({
            'Faculty Name': t.get('faculty_name'),
            'Subject Code': t.get('subject_code'),
            'Subject Name': t.get('subject_name'),
            'Department': t.get('department'),
            'Semester': t.get('semester'),
            'Feedback Count': t.get('count'),
            'Overall Rating (Avg)': t.get('avg_overall'),
            'Teaching Quality (Avg)': t.get('avg_teaching'),
            'Communication (Avg)': t.get('avg_communication'),
            'Subject Knowledge (Avg)': t.get('avg_knowledge'),
            'Doubt Solving (Avg)': t.get('avg_doubt'),
            'Practical Knowledge (Avg)': t.get('avg_practical')
        })

    df = pd.DataFrame(rows)
    csv_data = df.to_csv(index=False)

    return send_file(
        io.BytesIO(csv_data.encode('utf-8')),
        as_attachment=True,
        download_name=f'Teacher_Feedback_Report_{datetime.date.today()}.csv',
        mimetype='text/csv'
    )

@feedback_bp.route('/api/feedback/export/pdf')
def export_pdf():
    """Generates and downloads a PDF summary report of teacher feedback analytics."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    analytics = db_instance.get_feedback_analytics(user_id)
    teacher_stats = analytics.get('teacher_stats', [])

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()

    elements = []
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=colors.HexColor('#1E1B4B'), fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('DocNormal', parent=styles['Normal'], fontSize=9, leading=12)

    elements.append(Paragraph("AcadFusion AI - Teacher Feedback Summary Report", title_style))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Total Feedbacks: <b>{analytics.get('total_feedbacks', 0)}</b> | Overall Avg Rating: <b>{analytics.get('overall_average', 0.0)} / 5.0</b>", normal_style))
    elements.append(Spacer(1, 15))

    table_data = [
        ["Faculty", "Code", "Subject", "Dept", "Sem", "Count", "Avg Rating"]
    ]

    for t in teacher_stats:
        table_data.append([
            t.get('faculty_name', ''),
            t.get('subject_code', ''),
            t.get('subject_name', ''),
            t.get('department', ''),
            str(t.get('semester', '')),
            str(t.get('count', 0)),
            f"{t.get('avg_overall', 0.0)} / 5"
        ])

    t_table = Table(table_data, colWidths=[110, 60, 140, 60, 40, 45, 60])
    t_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E1B4B')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,1), (2,-1), 'LEFT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')])
    ]))

    elements.append(t_table)
    doc.build(elements)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f'Teacher_Feedback_Summary_{datetime.date.today()}.pdf',
        mimetype='application/pdf'
    )
