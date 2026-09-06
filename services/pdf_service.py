import os
import base64
import tempfile
import datetime
import io
import logging
import matplotlib
matplotlib.use('Agg') # Thread-safe non-GUI backend
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from models.database import db_instance

_logger = logging.getLogger(__name__)

def base64_to_tempfile(b64_str):
    try:
        if not b64_str: return None
        if ',' in b64_str:
            b64_str = b64_str.split(',')[1]
        img_data = base64.b64decode(b64_str)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        temp_file.write(img_data)
        temp_file.close()
        return temp_file.name
    except Exception as e:
        print(f"Error decoding image: {e}")
        return None

def generate_pdf_report(results, report_settings, user_id):
    # 1. Fetch metadata from DB
    profile = db_instance.get_institution_profile(user_id) or {}
    departments = db_instance.get_departments(user_id) or []
    
    # Identify HOD Name, Coordinator Name, and Department Logo
    hod_name = "Head of Department"
    coordinator_name = "Co-ordinator"
    dept_logo_path = None
    dept_name = report_settings.get('department') if report_settings else ""
    for d in departments:
        if d.get('name') == dept_name:
            hod_name = d.get('hod_name', "Head of Department")
            coordinator_name = d.get('coordinator_name', "Co-ordinator")
            dept_logo_path = base64_to_tempfile(d.get('logo_url'))
            break
            
    # Resolve logos
    college_logo_path = base64_to_tempfile(profile.get('logo_url'))
    
    # 2. Compile stats
    valid_results = [r for r in results if r.get('status') in ['Pass', 'Fail']]
    total_students = len(results)
    appeared = len(valid_results)
    passed = sum(1 for r in valid_results if r.get('status') == 'Pass')
    failed = appeared - passed
    pass_percentage = round((passed / appeared) * 100, 2) if appeared > 0 else 0.0
    
    sgpas = [float(r.get('sgpa', 0)) for r in valid_results if float(r.get('sgpa', 0)) > 0]
    avg_sgpa = round(sum(sgpas) / len(sgpas), 2) if sgpas else 0.0
    highest_sgpa = max(sgpas) if sgpas else 0.0
    lowest_sgpa = min(sgpas) if sgpas else 0.0
    
    class_dist = { 'distinction': 0, 'first': 0, 'second': 0, 'pass': 0, 'fail': 0 }
    for r in valid_results:
        status = r.get('status')
        sgpa = float(r.get('sgpa', 0))
        if status == 'Pass':
            if sgpa >= 7.75: class_dist['distinction'] += 1
            elif sgpa >= 6.75: class_dist['first'] += 1
            elif sgpa >= 5.75: class_dist['second'] += 1
            else: class_dist['pass'] += 1
        else:
            class_dist['fail'] += 1

    # Extract all subject codes and names
    subject_stats = {}
    for r in valid_results:
        for sub_code, sub_data in r.get('subjects', {}).items():
            if sub_code not in subject_stats:
                # Credits and faculty come from sub_data enriched by calculate_sgpa_and_map_faculty.
                # If not yet enriched (e.g. results loaded from DB before the fix),
                # fall back to sub_data fields, then to safe defaults with a warning.
                credits_val = sub_data.get('credits')
                faculty_val = sub_data.get('faculty')

                if credits_val is None:
                    _logger.warning(
                        f"[PDF] Subject '{sub_code}' has no credits in enriched data. "
                        f"Defaulting to 4. Re-run the analysis or add it to Institution Hub."
                    )
                    credits_val = 4
                if not faculty_val:
                    _logger.warning(
                        f"[PDF] Subject '{sub_code}' has no faculty in enriched data. "
                        f"Defaulting to 'Unassigned'. Re-run the analysis or add it to Institution Hub."
                    )
                    faculty_val = 'Unassigned'

                subject_stats[sub_code] = {
                    'name': sub_data.get('name', 'N/A'),
                    'appeared': 0,
                    'passed': 0,
                    'credits': credits_val,
                    'faculty': faculty_val,
                    'is_internal_only': sub_data.get('is_internal_only', False),
                    'sum_marks': 0
                }
            
            res_flag = str(sub_data.get('result', '')).upper()
            subject_stats[sub_code]['appeared'] += 1
            if res_flag in ['P', 'PASS']:
                subject_stats[sub_code]['passed'] += 1
            subject_stats[sub_code]['sum_marks'] += int(sub_data.get('total', 0))

    # Calculate subject-wise pass percentages
    for sc, info in subject_stats.items():
        info['pass_percent'] = round((info['passed'] / info['appeared']) * 100, 2) if info['appeared'] > 0 else 0.0
        info['avg_score'] = round(info['sum_marks'] / info['appeared'], 1) if info['appeared'] > 0 else 0.0

    # 3. Generate visual graphs using Matplotlib
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    
    # Graph 1: Pass vs Fail (Pie Chart)
    fig, ax = plt.subplots(figsize=(5, 2.5))
    ax.pie([passed, failed], labels=['Pass', 'Fail'], autopct='%1.1f%%', colors=['#10b981', '#ef4444'], startangle=90)
    ax.axis('equal')
    plt.title('Overall Pass vs Fail Distribution', fontsize=10, fontweight='bold', pad=10)
    plt.tight_layout()
    pie_buf = io.BytesIO()
    plt.savefig(pie_buf, format='png', dpi=200)
    plt.close()
    pie_buf.seek(0)
    
    # Graph 2: Grade distribution
    grade_bins = {'O': 0, 'A+': 0, 'A': 0, 'B+': 0, 'B': 0, 'C': 0, 'P': 0, 'F': 0}
    for r in valid_results:
        for s in r.get('subjects', {}).values():
            res = str(s.get('result', '')).upper()
            tot = s.get('total', 0)
            is_int_only = s.get('is_internal_only', False)
            if res == 'F':
                grade_bins['F'] += 1
            else:
                # Grade thresholds by total marks (same for all subject types)
                if tot >= 90: grade_bins['O'] += 1
                elif tot >= 80: grade_bins['A+'] += 1
                elif tot >= 70: grade_bins['A'] += 1
                elif tot >= 60: grade_bins['B+'] += 1
                elif tot >= 50: grade_bins['B'] += 1
                elif tot >= 45: grade_bins['C'] += 1
                else: grade_bins['P'] += 1
                
    fig, ax = plt.subplots(figsize=(6, 2.5))
    ax.bar(grade_bins.keys(), grade_bins.values(), color='#7c3aed', edgecolor='#581c87')
    ax.set_title('Subject Grade Distribution', fontsize=10, fontweight='bold')
    ax.set_ylabel('Number of Grades')
    plt.tight_layout()
    bar_buf = io.BytesIO()
    plt.savefig(bar_buf, format='png', dpi=200)
    plt.close()
    bar_buf.seek(0)

    # Graph 3: Subject-wise Pass Percentage
    fig, ax = plt.subplots(figsize=(6, 2.5))
    codes = list(subject_stats.keys())
    percs = [info['pass_percent'] for info in subject_stats.values()]
    ax.barh(codes, percs, color='#06b6d4', edgecolor='#0891b2')
    ax.set_xlim(0, 100)
    ax.set_title('Subject-wise Pass Percentage', fontsize=10, fontweight='bold')
    ax.set_xlabel('Passing %')
    plt.tight_layout()
    sub_buf = io.BytesIO()
    plt.savefig(sub_buf, format='png', dpi=200)
    plt.close()
    sub_buf.seek(0)

    # Convert figures to reportlab flowables
    pie_img = Image(pie_buf, width=220, height=110)
    bar_img = Image(bar_buf, width=240, height=110)
    sub_img = Image(sub_buf, width=240, height=110)

    # 4. Generate ReportLab PDF Document
    pdf_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buf,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Palette styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1e1b4b'),
        alignment=1, # Center
        spaceAfter=15
    )
    
    college_title_style = ParagraphStyle(
        'CollegeTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#581c87'),
        alignment=1 # Center
    )
    
    college_sub_style = ParagraphStyle(
        'CollegeSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#475569'),
        alignment=1
    )
    
    section_heading = ParagraphStyle(
        'SecHeading',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#1e1b4b'),
        spaceBefore=10,
        spaceAfter=5
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.white,
        alignment=1
    )
    
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=0
    )
    
    table_cell_center = ParagraphStyle(
        'TableCellCenter',
        parent=table_cell_style,
        alignment=1
    )
    
    table_cell_bold_center = ParagraphStyle(
        'TableCellBoldCenter',
        parent=table_cell_style,
        fontName='Helvetica-Bold',
        alignment=1
    )

    elements = []
    
    # --- HEADER WITH BRANDING ---
    college_logo = None
    if college_logo_path and os.path.exists(college_logo_path):
        college_logo = Image(college_logo_path, width=55, height=55)
        
    dept_logo = None
    if dept_logo_path and os.path.exists(dept_logo_path):
        dept_logo = Image(dept_logo_path, width=55, height=55)
    
    inst_name = profile.get('college_name', 'AcadFusion AI Enabled College')
    inst_addr = f"{profile.get('address','')}, {profile.get('city','')}, {profile.get('state','')}-{profile.get('pincode','')}"
    inst_contact = f"Website: {profile.get('website','N/A')} | Email: {profile.get('email','N/A')} | Phone: {profile.get('phone','N/A')}"
    accreditations_str = " | ".join(profile.get('accreditations', ['VTU Affiliated']))
    
    col_text = [
        Paragraph(inst_name, college_title_style),
        Spacer(1, 2),
        Paragraph(inst_addr, college_sub_style),
        Paragraph(inst_contact, college_sub_style),
        Paragraph(f"<b>Accreditation:</b> {accreditations_str}", college_sub_style)
    ]
    
    if college_logo and dept_logo:
        info_table = Table([[college_logo, col_text, dept_logo]], colWidths=[60, 400, 60])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (2,0), (2,0), 'RIGHT'),
        ]))
        elements.append(info_table)
    elif college_logo:
        info_table = Table([[college_logo, col_text]], colWidths=[65, 455])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (0,0), (0,0), 'LEFT'),
        ]))
        elements.append(info_table)
    elif dept_logo:
        info_table = Table([[col_text, dept_logo]], colWidths=[455, 65])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ]))
        elements.append(info_table)
    else:
        info_table = Table([[col_text]], colWidths=[520])
        info_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ]))
        elements.append(info_table)
        
    elements.append(Spacer(1, 10))
    elements.append(Table([['']], colWidths=[520], rowHeights=[2], style=TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#7c3aed')),
    ])))
    elements.append(Spacer(1, 15))
    
    # --- DOCUMENT INFO TABLE ---
    elements.append(Paragraph("VTU Result Analysis Report", title_style))
    
    doc_info_data = [
        [
            Paragraph("<b>Department:</b>", table_cell_style), 
            Paragraph(dept_name or 'N/A', table_cell_style),
            Paragraph("<b>Academic Year:</b>", table_cell_style), 
            Paragraph(report_settings.get('academic_year', 'N/A') if report_settings else 'N/A', table_cell_style)
        ],
        [
            Paragraph("<b>Semester:</b>", table_cell_style), 
            Paragraph(str(report_settings.get('semester', 'N/A')) if report_settings else 'N/A', table_cell_style),
            Paragraph("<b>Scheme:</b>", table_cell_style), 
            Paragraph(report_settings.get('scheme', 'N/A') if report_settings else 'N/A', table_cell_style)
        ],
        [
            Paragraph("<b>Examination:</b>", table_cell_style), 
            Paragraph(report_settings.get('examination', 'N/A') if report_settings else 'N/A', table_cell_style),
            Paragraph("<b>Report Date:</b>", table_cell_style), 
            Paragraph(datetime.datetime.now().strftime('%d-%b-%Y %I:%M %p'), table_cell_style)
        ]
    ]
    
    doc_info_table = Table(doc_info_data, colWidths=[80, 180, 90, 170])
    doc_info_table.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f8fafc')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f8fafc')),
    ]))
    elements.append(doc_info_table)
    elements.append(Spacer(1, 15))
    
    # --- OVERALL RESULT SUMMARY ---
    elements.append(Paragraph("1. Overall Result Summary", section_heading))
    
    summary_headers = ["Metric", "Value", "Class Breakdown", "Count"]
    summary_rows = [
        [Paragraph("Total Students Scraped", table_cell_style), Paragraph(str(total_students), table_cell_center), Paragraph("Distinction (>= 7.75 SGPA)", table_cell_style), Paragraph(str(class_dist['distinction']), table_cell_center)],
        [Paragraph("Students Appeared", table_cell_style), Paragraph(str(appeared), table_cell_center), Paragraph("First Class (6.75 - 7.74 SGPA)", table_cell_style), Paragraph(str(class_dist['first']), table_cell_center)],
        [Paragraph("Passed Students", table_cell_style), Paragraph(str(passed), table_cell_center), Paragraph("Second Class (5.75 - 6.74 SGPA)", table_cell_style), Paragraph(str(class_dist['second']), table_cell_center)],
        [Paragraph("Failed Students", table_cell_style), Paragraph(str(failed), table_cell_center), Paragraph("Pass Class (5.00 - 5.74 SGPA)", table_cell_style), Paragraph(str(class_dist['pass']), table_cell_center)],
        [Paragraph("<b>Passing Percentage</b>", table_cell_style), Paragraph(f"<b>{pass_percentage}%</b>", table_cell_center), Paragraph("Fail (Backlogs)", table_cell_style), Paragraph(str(class_dist['fail']), table_cell_center)],
        [Paragraph("SGPA (Avg / Max / Min)", table_cell_style), Paragraph(f"{avg_sgpa} / {highest_sgpa} / {lowest_sgpa}", table_cell_center), Paragraph("", table_cell_style), Paragraph("", table_cell_center)]
    ]
    
    table_data = [[Paragraph(h, table_header_style) for h in summary_headers]] + summary_rows
    summary_table = Table(table_data, colWidths=[140, 120, 170, 90])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e1b4b')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 15))
    
    # --- SUBJECT-WISE ANALYSIS TABLE ---
    elements.append(Paragraph("2. Subject-wise Analysis", section_heading))
    sub_headers = ["Sub Code", "Subject Name", "Credits", "Faculty Assigned", "Appeared", "Passed", "Passing %"]
    sub_rows = []
    
    for code, info in subject_stats.items():
        sub_rows.append([
            Paragraph(code, table_cell_bold_center),
            Paragraph(info['name'], table_cell_style),
            Paragraph(str(info['credits']), table_cell_center),
            Paragraph(info['faculty'], table_cell_style),
            Paragraph(str(info['appeared']), table_cell_center),
            Paragraph(str(info['passed']), table_cell_center),
            Paragraph(f"<b>{info['pass_percent']}%</b>", table_cell_center)
        ])
        
    table_data = [[Paragraph(h, table_header_style) for h in sub_headers]] + sub_rows
    sub_table = Table(table_data, colWidths=[65, 145, 45, 105, 55, 50, 55])
    sub_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e1b4b')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(sub_table)
    
    elements.append(PageBreak())
    
    # --- STUDENT MARKSHEET LIST ---
    elements.append(Paragraph("3. Detailed Student Roster", section_heading))
    student_headers = ["USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "SGPA", "Result"]
    student_rows = []
    
    student_table_styles = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e1b4b')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]
    
    for idx, r in enumerate(results):
        sub_cnt = len(r.get('subjects', {}))
        max_marks = r.get('max_marks')
        if not max_marks:
            max_marks = sub_cnt * 100
        
        total_marks = r.get('total_marks', 0)
        percentage = r.get('percentage')
        if percentage is None:
            percentage = (total_marks / max_marks * 100) if max_marks > 0 else 0.0
            
        student_rows.append([
            Paragraph(r.get('usn', ''), table_cell_bold_center),
            Paragraph(r.get('name', 'N/A'), table_cell_style),
            Paragraph(str(max_marks), table_cell_center),
            Paragraph(str(total_marks), table_cell_center),
            Paragraph(f"{percentage:.2f}%", table_cell_center),
            Paragraph(f"{float(r.get('sgpa',0)):.2f}", table_cell_center),
            Paragraph(f"<b>{r.get('status')}</b>", table_cell_bold_center if r.get('status') == 'Pass' else table_cell_center)
        ])
        
        # Check for backlogs.
        # Internal-only subjects with result='P' must NOT be counted as backlogs.
        # Only subjects whose result is genuinely F/A/ABSENT/FAIL are backlogs.
        has_backlog = (r.get('status') == 'Fail')
        for sub_data in r.get('subjects', {}).values():
            sub_res = str(sub_data.get('result', '')).upper()
            if sub_res in ['F', 'A', 'ABSENT', 'FAIL']:
                has_backlog = True
                break
        if has_backlog:
            row_idx = idx + 1 # 1-based index (header is 0)
            student_table_styles.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#FEF08A')))
        
    table_data = [[Paragraph(h, table_header_style) for h in student_headers]] + student_rows
    student_table = Table(table_data, colWidths=[80, 140, 60, 70, 60, 50, 60])
    student_table.setStyle(TableStyle(student_table_styles))
    elements.append(student_table)
    
    elements.append(PageBreak())
    
    # --- CLASS TOPPERS LIST ---
    elements.append(Paragraph("4. Class Toppers List", section_heading))
    elements.append(Spacer(1, 5))
    
    # Table 1: Top 5 wrt Marks Secured
    elements.append(Paragraph("<b>Table 1: Top 5 wrt Marks Secured (SGPA Excluded)</b>", table_cell_style))
    elements.append(Spacer(1, 5))
    
    valid_student_marks = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                tm = int(r.get('total_marks', 0))
            except:
                tm = 0
            valid_student_marks.append((tm, r))
    valid_student_marks.sort(key=lambda x: x[0], reverse=True)
    
    toppers_marks_rows = []
    current_rank = 0
    prev_marks = None
    for tm, r in valid_student_marks:
        if prev_marks is None or tm != prev_marks:
            current_rank += 1
        if current_rank > 5:
            break
        prev_marks = tm
        
        sub_cnt = len(r.get('subjects', {}))
        max_m = r.get('max_marks')
        if not max_m:
            max_m = sub_cnt * 100
        pct = r.get('percentage')
        if pct is None:
            pct = (tm / max_m * 100) if max_m > 0 else 0.0
            
        toppers_marks_rows.append([
            Paragraph(str(current_rank), table_cell_bold_center),
            Paragraph(r.get('usn'), table_cell_bold_center),
            Paragraph(r.get('name', 'N/A'), table_cell_style),
            Paragraph(str(max_m), table_cell_center),
            Paragraph(str(tm), table_cell_center),
            Paragraph(f"{pct:.2f}%", table_cell_center),
            Paragraph(r.get('status'), table_cell_bold_center if r.get('status') == 'Pass' else table_cell_center)
        ])
        
    t1_headers = [Paragraph(h, table_header_style) for h in ["Rank", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "Result"]]
    t1_table = Table([t1_headers] + toppers_marks_rows, colWidths=[40, 80, 160, 60, 70, 60, 50])
    t1_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e1b4b')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t1_table)
    elements.append(Spacer(1, 15))
    
    # Table 2: Top 5 wrt SGPA
    elements.append(Paragraph("<b>Table 2: Top 5 wrt SGPA (Percentage Excluded)</b>", table_cell_style))
    elements.append(Spacer(1, 5))
    
    valid_student_sgpa = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                sg = float(r.get('sgpa', 0.0))
            except:
                sg = 0.0
            valid_student_sgpa.append((sg, r))
    valid_student_sgpa.sort(key=lambda x: x[0], reverse=True)
    
    toppers_sgpa_rows = []
    current_rank = 0
    prev_sgpa = None
    for sg, r in valid_student_sgpa:
        if prev_sgpa is None or sg != prev_sgpa:
            current_rank += 1
        if current_rank > 5:
            break
        prev_sgpa = sg
        
        tm = 0
        try:
            tm = int(r.get('total_marks', 0))
        except:
            pass
        sub_cnt = len(r.get('subjects', {}))
        max_m = r.get('max_marks')
        if not max_m:
            max_m = sub_cnt * 100
            
        toppers_sgpa_rows.append([
            Paragraph(str(current_rank), table_cell_bold_center),
            Paragraph(r.get('usn'), table_cell_bold_center),
            Paragraph(r.get('name', 'N/A'), table_cell_style),
            Paragraph(f"{sg:.2f}", table_cell_center),
            Paragraph(str(max_m), table_cell_center),
            Paragraph(str(tm), table_cell_center),
            Paragraph(r.get('status'), table_cell_bold_center if r.get('status') == 'Pass' else table_cell_center)
        ])
        
    t2_headers = [Paragraph(h, table_header_style) for h in ["Rank", "USN", "Student Name", "SGPA", "Total Marks", "Marks Secured", "Result"]]
    t2_table = Table([t2_headers] + toppers_sgpa_rows, colWidths=[40, 80, 160, 50, 60, 80, 50])
    t2_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e1b4b')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t2_table)
    
    elements.append(PageBreak())
    
    # --- VISUAL ANALYTICS SECTION ---
    elements.append(Paragraph("5. Visual Performance Charts", section_heading))
    
    charts_data = [
        [pie_img, bar_img],
        [sub_img, '']
    ]
    charts_table = Table(charts_data, colWidths=[260, 260], rowHeights=[140, 140])
    charts_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(charts_table)
    elements.append(Spacer(1, 20))
    
    # --- REMARKS & SIGNATURES ---
    elements.append(Paragraph("<b>Remarks / Findings:</b>", table_cell_style))
    elements.append(Spacer(1, 5))
    elements.append(Table([['']], colWidths=[520], rowHeights=[40], style=TableStyle([
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#94a3b8')),
    ])))
    elements.append(Spacer(1, 40))
    
    # Signatures
    principal_name = profile.get('principal_name') or profile.get('short_name') or 'Principal'
    sig_data = [
        [
            Paragraph("Co-ordinator<br/><b>" + coordinator_name + "</b>", table_cell_style),
            Paragraph("Head of Department<br/><b>" + hod_name + "</b>", table_cell_center),
            Paragraph("Principal<br/><b>" + principal_name + "</b>", table_cell_center),
        ],
        [
            Paragraph("Signature: ________________<br/><br/><br/><br/>Date: ________________", table_cell_style),
            Paragraph("Signature: ________________", table_cell_center),
            Paragraph("Signature: ________________", table_cell_center),
        ]
    ]
    sig_table = Table(sig_data, colWidths=[180, 170, 170])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 15),
    ]))
    elements.append(KeepTogether(sig_table))
    elements.append(Spacer(1, 15))
    
    # Centered branding below everything
    branding_style = ParagraphStyle(
        'CenterBranding',
        parent=table_cell_center,
        fontName='Helvetica-Bold',
        fontSize=8,
        textColor=colors.HexColor('#64748b'),
        alignment=1 # Center aligned
    )
    elements.append(Paragraph("Prepared using AcadFusion AI", branding_style))

    # Build document
    doc.build(elements)
    
    # Cleanup temp files
    if college_logo_path and os.path.exists(college_logo_path):
        try: os.remove(college_logo_path)
        except: pass
    if dept_logo_path and os.path.exists(dept_logo_path):
        try: os.remove(dept_logo_path)
        except: pass
        
    pdf_bytes = pdf_buf.getvalue()
    return pdf_bytes
