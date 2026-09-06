import pandas as pd
import openpyxl
import logging
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import Paragraph, ParagraphProperties, CharacterProperties
from openpyxl.drawing.colors import ColorChoice
from openpyxl.utils import get_column_letter
import io
import json
import datetime
from models.database import db_instance

_logger = logging.getLogger(__name__)

def classify_grade(total, internal=0, external=0, is_internal_only=False):
    """
    Categorizes marks into VTU grades.

    For internal-only subjects (no external exam), the external-marks fail rule
    is skipped. Pass/fail is determined by total marks alone (>= 40).
    """
    try:
        total = int(total)
        internal = int(internal)
        external = int(external)
    except:
        return "F"

    if not is_internal_only:
        # Standard VTU rule: external must be >= 18
        if external < 18:
            return "F"

    # Grade thresholds apply to total marks (same for all subject types)
    if total >= 90: return "O"
    if total >= 80: return "A+"
    if total >= 70: return "A"
    if total >= 60: return "B+"
    if total >= 50: return "B"
    if total >= 45: return "C"
    if total >= 40: return "P"
    return "F"

def generate_excel_report(results, report_settings=None, user_id=None):
    """
    Generates a professional 7-sheet Excel report including Institution Profile details,
    Faculty Master roster, Subject Master roster, and comprehensive visual analyzer tables.
    """
    if report_settings is None:
        report_settings = {}
    # Initialize metadata from database if possible
    profile = {}
    departments = []
    faculty_members = []
    subject_mappings_db = []
    
    if user_id:
        try:
            profile = db_instance.get_institution_profile(user_id) or {}
            departments = db_instance.get_departments(user_id) or []
            faculty_members = db_instance.get_faculty_members(user_id) or []
            if report_settings:
                subject_mappings_db = db_instance.get_subjects(user_id, {
                    'academic_year': report_settings.get('academic_year'),
                    'scheme': report_settings.get('scheme'),
                    'semester': report_settings.get('semester'),
                    'department': report_settings.get('department')
                })
        except Exception as e:
            print(f"Error fetching metadata for Excel report: {e}")

    # Build DB subjects lookup map
    sub_map = {}
    for sm in subject_mappings_db:
        sub_map[sm.get('subject_code', '').upper()] = sm

    valid_results = [r for r in results if r.get('status') in ['Pass', 'Fail']]
    
    # 1. Compile Analytics Data
    total_students = len(results)
    summary_data = {
        'FCD': 0, 'FC': 0, 'SC': 0, 'PassClass': 0, 'Fail': 0, 'Absent': 0
    }
    sgpas = []
    
    for r in results:
        status = r.get('status') or ""
        sgpa = float(r.get('sgpa', 0.0))
        if sgpa > 0.1:
            sgpas.append(sgpa)
            
        if status == "Pass":
            if sgpa >= 7.75: summary_data['FCD'] += 1
            elif sgpa >= 6.75: summary_data['FC'] += 1
            elif sgpa >= 5.75: summary_data['SC'] += 1
            else: summary_data['PassClass'] += 1
        elif status == "Fail":
            summary_data['Fail'] += 1
        elif "Absent" in status or "No Res" in status or "Init Error" in status:
            summary_data['Absent'] += 1

    total_pass = summary_data['FCD'] + summary_data['FC'] + summary_data['SC'] + summary_data['PassClass']
    appeared = total_pass + summary_data['Fail']
    pass_percentage = round((total_pass / appeared) * 100, 2) if appeared > 0 else 0.0
    
    avg_sgpa = round(sum(sgpas) / len(sgpas), 2) if sgpas else 0.0
    highest_sgpa = max(sgpas) if sgpas else 0.0
    lowest_sgpa = min(sgpas) if sgpas else 0.0

    output = io.BytesIO()
    
    # Create Workbook
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    wb.remove(default_sheet)
    
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
    header_fill = PatternFill(start_color='1E1B4B', end_color='1E1B4B', fill_type='solid') # Deep Indigo
    sub_header_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid') # Slate 100
    green_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    red_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
    yellow_fill = PatternFill(start_color='FEF08A', end_color='FEF08A', fill_type='solid') # Yellow 200
    
    # Custom Rank Color Fills
    gold_fill = PatternFill(start_color='FDE047', end_color='FDE047', fill_type='solid') # Gold (Yellow 300)
    silver_fill = PatternFill(start_color='CBD5E1', end_color='CBD5E1', fill_type='solid') # Silver (Slate 300)
    bronze_fill = PatternFill(start_color='FDBA74', end_color='FDBA74', fill_type='solid') # Bronze (Orange 300)
    rank4_fill = PatternFill(start_color='E9D5FF', end_color='E9D5FF', fill_type='solid') # Rank 4 (Lavender)
    rank5_fill = PatternFill(start_color='CCFBF1', end_color='CCFBF1', fill_type='solid') # Rank 5 (Mint)

    # --- SHEET 1: OVERALL RESULT ---
    ws_sum = wb.create_sheet('Overall Result')
    ws_sum.views.sheetView[0].showGridLines = True
    
    ws_sum.cell(row=1, column=1, value=profile.get('college_name', 'AcadFusion AI Enabled College')).font = Font(bold=True, size=14, color='581C87')
    ws_sum.cell(row=2, column=1, value=f"{report_settings.get('department', 'N/A')} - Semester {report_settings.get('semester','N/A')} ({report_settings.get('academic_year','N/A')})").font = Font(bold=True, size=11, color='475569')
    ws_sum.cell(row=3, column=1, value=f"VTU Result Analysis - {report_settings.get('examination', 'N/A')}").font = Font(bold=True, size=10, color='475569')
    
    ws_sum.cell(row=5, column=1, value="Report Metrics Summary").font = Font(bold=True, size=12, color='1E1B4B')
    
    metrics = [
        ("Total Scraped Students", total_students),
        ("Students Appeared", appeared),
        ("Passed Students", total_pass),
        ("Failed Students", summary_data['Fail']),
        ("Passing Percentage", f"{pass_percentage}%"),
        ("Average Semester SGPA", avg_sgpa),
        ("Highest SGPA", highest_sgpa),
        ("Lowest SGPA", lowest_sgpa),
        ("Distinction Class Count", summary_data['FCD']),
        ("First Class Count", summary_data['FC']),
        ("Second Class Count", summary_data['SC']),
        ("Pass Class Count", summary_data['PassClass'])
    ]
    
    row_idx = 7
    for name, val in metrics:
        ws_sum.cell(row=row_idx, column=1, value=name).font = Font(bold=True)
        ws_sum.cell(row=row_idx, column=2, value=val).alignment = Alignment(horizontal='center')
        ws_sum.cell(row=row_idx, column=1).border = thin_border
        ws_sum.cell(row=row_idx, column=2).border = thin_border
        row_idx += 1
        
    ws_sum.column_dimensions['A'].width = 30
    ws_sum.column_dimensions['B'].width = 15

    # --- SHEET 2: SUBJECT ANALYSIS ---
    ws_sub = wb.create_sheet('Subject Analysis')
    ws_sub.views.sheetView[0].showGridLines = True
    ws_sub.cell(row=1, column=1, value="Subject-wise Result Analysis").font = Font(bold=True, size=14, color='1E1B4B')
    
    headers_sa = [
        "Subject", "Subject Code", "Credits", "Faculty Assigned",
        "FCD (70–100%)", "FC (60–69%)", "SC (35–59%)", "Fail", "Absent",
        "Total Students Appeared", "No. of Students Passed", "Passing %"
    ]
    for c_idx, h in enumerate(headers_sa, 1):
        cell = ws_sub.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
        cell.border = thin_border
        
    all_subjects_meta = {}
    import re as _re
    # Build wildcard subject list for fallback matching against sub_map
    sub_map_wildcards = [(k, v) for k, v in sub_map.items() if '**' in k]

    for r in valid_results:
        for sub_code, sub_data in r.get('subjects', {}).items():
            if sub_code not in all_subjects_meta:
                # Priority 1: Use credits/faculty already enriched by calculate_sgpa_and_map_faculty
                # These values come directly from Institution Hub and are the most accurate.
                credits_val = sub_data.get('credits')   # set by analyzer if Institution Hub matched
                faculty_val = sub_data.get('faculty')   # set by analyzer if Institution Hub matched

                # Priority 2: Fallback to sub_map (direct DB query result)
                if credits_val is None or faculty_val is None:
                    norm_code = sub_code.strip().upper()
                    mapped = sub_map.get(norm_code)
                    if not mapped:
                        for wc_code, wc_sub in sub_map_wildcards:
                            escaped = _re.escape(wc_code)
                            pattern = '^' + escaped.replace('\\*\\*', '[A-Z0-9]{2}') + '$'
                            if _re.match(pattern, norm_code):
                                mapped = wc_sub
                                break
                    if mapped:
                        if credits_val is None:
                            try:
                                credits_val = int(mapped.get('credits', 4))
                            except (ValueError, TypeError):
                                credits_val = 4
                        if faculty_val is None:
                            faculty_val = mapped.get('faculty_name') or 'Unassigned'

                # Priority 3: Last resort defaults – log a warning so the admin knows
                if credits_val is None:
                    _logger.warning(
                        f"[Excel] Subject '{sub_code}' has no credits in enriched data or "
                        f"Institution Hub. Defaulting to 4. Add it to Institution Hub Settings."
                    )
                    credits_val = 4
                if not faculty_val:
                    _logger.warning(
                        f"[Excel] Subject '{sub_code}' has no faculty in enriched data or "
                        f"Institution Hub. Defaulting to 'Unassigned'. Add it to Institution Hub Settings."
                    )
                    faculty_val = 'Unassigned'

                all_subjects_meta[sub_code] = {
                    'name': sub_data.get('name', 'N/A'),
                    'credits': credits_val,
                    'faculty': faculty_val,
                    'is_internal_only': sub_data.get('is_internal_only', False),
                    'distinction': 0, 'first': 0, 'second': 0, 'failed': 0, 'absent': 0,
                    'appeared': 0, 'passed': 0
                }
            
            info = all_subjects_meta[sub_code]
            res = str(sub_data.get('result', '')).upper()
            total = int(sub_data.get('total', 0))
            
            if res in ['A', 'ABSENT']:
                info['absent'] += 1
            else:
                info['appeared'] += 1
                if res in ['P', 'PASS']:
                    if total >= 70:
                        info['distinction'] += 1  # FCD: 70–100
                        info['passed'] += 1
                    elif total >= 60:
                        info['first'] += 1        # FC: 60–69
                        info['passed'] += 1
                    elif total >= 35:
                        info['second'] += 1       # SC: 35–59 (inclusive)
                        info['passed'] += 1
                    else:
                        info['failed'] += 1       # Fail: 0–34
                else:
                    info['failed'] += 1

    row_idx = 4
    for code, info in all_subjects_meta.items():
        pass_rate = round((info['passed'] / info['appeared']) * 100, 2) if info['appeared'] > 0 else 0.0
        
        ws_sub.cell(row=row_idx, column=1, value=info['name']).border = thin_border
        ws_sub.cell(row=row_idx, column=2, value=code).border = thin_border
        ws_sub.cell(row=row_idx, column=3, value=info['credits']).border = thin_border
        ws_sub.cell(row=row_idx, column=4, value=info['faculty']).border = thin_border
        ws_sub.cell(row=row_idx, column=5, value=info['distinction']).border = thin_border
        ws_sub.cell(row=row_idx, column=6, value=info['first']).border = thin_border
        ws_sub.cell(row=row_idx, column=7, value=info['second']).border = thin_border
        ws_sub.cell(row=row_idx, column=8, value=info['failed']).border = thin_border
        ws_sub.cell(row=row_idx, column=9, value=info['absent']).border = thin_border
        ws_sub.cell(row=row_idx, column=10, value=info['appeared']).border = thin_border
        ws_sub.cell(row=row_idx, column=11, value=info['passed']).border = thin_border
        
        cell_pct = ws_sub.cell(row=row_idx, column=12, value=pass_rate)
        cell_pct.border = thin_border
        cell_pct.font = Font(bold=True)
        cell_pct.fill = green_fill if pass_rate >= 80 else red_fill
        
        row_idx += 1
        
    for col in ws_sub.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_sub.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 35)

    # Add Chart to Sheet 1 referencing Sheet 2 Passing %
    if all_subjects_meta:
        chart = BarChart()
        chart.type = "col"
        chart.style = 10
        chart.title = "Subject wise Passing Percentage"
        chart.y_axis.title = 'Percentage'
        chart.x_axis.title = 'Subjects'
        chart.height = 12
        chart.width = 20
        chart.x_axis.labelRotation = 4500
        
        chart.dataLabels = DataLabelList()
        chart.dataLabels.showVal = True
        
        data_ref = Reference(ws_sub, min_col=12, min_row=3, max_row=ws_sub.max_row)
        cats_ref = Reference(ws_sub, min_col=2, min_row=4, max_row=ws_sub.max_row)
        
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.legend = None
        
        if chart.series:
            chart.series[0].graphical_properties = GraphicalProperties(solidFill=ColorChoice(srgbClr="7C3AED"))
            
        ws_sum.add_chart(chart, "D7")

    # --- SHEET 3: ALL STUDENTS ---
    ws_all = wb.create_sheet('All Students')
    ws_all.views.sheetView[0].showGridLines = True
    ws_all.cell(row=1, column=1, value="Detailed Student Marksheet").font = Font(bold=True, size=14, color='1E1B4B')
    
    all_subject_codes = sorted(list(all_subjects_meta.keys()))
    skyblue_fill = PatternFill(start_color='BAE6FD', end_color='BAE6FD', fill_type='solid') # Sky 200 / Sky Blue
    
    # Multi-row Headers
    ws_all.cell(row=3, column=1, value="SL.No")
    ws_all.merge_cells("A3:A4")
    ws_all.cell(row=3, column=2, value="USN")
    ws_all.merge_cells("B3:B4")
    ws_all.cell(row=3, column=3, value="Student Name")
    ws_all.merge_cells("C3:C4")
    
    curr_col = 4
    for code in all_subject_codes:
        ws_all.cell(row=3, column=curr_col, value=code)
        ws_all.merge_cells(start_row=3, start_column=curr_col, end_row=3, end_column=curr_col+3)
        
        # Subheaders in row 4
        ws_all.cell(row=4, column=curr_col, value="INT")
        ws_all.cell(row=4, column=curr_col+1, value="EXT")
        ws_all.cell(row=4, column=curr_col+2, value="TOT")
        ws_all.cell(row=4, column=curr_col+3, value="RES")
        curr_col += 4
        
    ws_all.cell(row=3, column=curr_col, value="Grand Total")
    ws_all.merge_cells(start_row=3, start_column=curr_col, end_row=4, end_column=curr_col)
    
    ws_all.cell(row=3, column=curr_col+1, value="Percentage")
    ws_all.merge_cells(start_row=3, start_column=curr_col+1, end_row=4, end_column=curr_col+1)
    
    ws_all.cell(row=3, column=curr_col+2, value="SGPA")
    ws_all.merge_cells(start_row=3, start_column=curr_col+2, end_row=4, end_column=curr_col+2)
    
    ws_all.cell(row=3, column=curr_col+3, value="No. of B/L")
    ws_all.merge_cells(start_row=3, start_column=curr_col+3, end_row=4, end_column=curr_col+3)
    
    ws_all.cell(row=3, column=curr_col+4, value="Class Classify")
    ws_all.merge_cells(start_row=3, start_column=curr_col+4, end_row=4, end_column=curr_col+4)
    
    ws_all.cell(row=3, column=curr_col+5, value="RESULT")
    ws_all.merge_cells(start_row=3, start_column=curr_col+5, end_row=4, end_column=curr_col+5)
    
    # Fill headers background & borders
    for row in range(3, 5):
        for col in range(1, curr_col + 6):
            cell = ws_all.cell(row=row, column=col)
            cell.font = Font(bold=True, size=9)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.fill = sub_header_fill
            cell.border = thin_border
            
    # Add Student Marks
    student_row_idx = 5
    for i, r in enumerate(results):
        res_status = r.get('status', 'Fail')
        ws_all.cell(row=student_row_idx, column=1, value=i+1).border = thin_border
        ws_all.cell(row=student_row_idx, column=2, value=r.get('usn')).border = thin_border
        ws_all.cell(row=student_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
        
        has_backlog = False
        backlog_count = 0
        failed_subject_cols = []
        
        c_col = 4
        for code in all_subject_codes:
            sub_data = r.get('subjects', {}).get(code)
            if sub_data:
                internal = int(sub_data.get('internal', 0))
                external = int(sub_data.get('external', 0))
                total = int(sub_data.get('total', 0))
                res_flag = str(sub_data.get('result', '')).upper().strip()
                is_int_only = sub_data.get('is_internal_only', False) or \
                    all_subjects_meta.get(code, {}).get('is_internal_only', False)

                # A subject counts as a backlog only when it genuinely failed.
                # Internal-only subjects with result='F' still count as backlog.
                # The key fix: internal-only subjects with result='P' should NOT be
                # misclassified as failed just because external=0.
                is_failed_subject = res_flag in ['F', 'FAIL', 'A', 'ABSENT', 'NE', 'NOT ELIGIBLE', 'N']

                if is_failed_subject:
                    backlog_count += 1
                    has_backlog = True
                    failed_subject_cols.append((c_col, c_col + 3))
                
                ws_all.cell(row=student_row_idx, column=c_col, value=internal).border = thin_border
                ws_all.cell(row=student_row_idx, column=c_col+1, value=external).border = thin_border
                ws_all.cell(row=student_row_idx, column=c_col+2, value=total).border = thin_border
                ws_all.cell(row=student_row_idx, column=c_col+3, value=res_flag).border = thin_border
            else:
                for off in range(4):
                    ws_all.cell(row=student_row_idx, column=c_col+off, value="-").border = thin_border
            c_col += 4
            
        grand_total = r.get('total_marks', 0) if res_status in ['Pass', 'Fail'] else '-'
        sgpa_val = float(r.get('sgpa', 0.0))
        
        # Calculate percentage
        try:
            tm_val = int(r.get('total_marks', 0))
        except:
            tm_val = 0
        sub_cnt = len(r.get('subjects', {}))
        max_m = r.get('max_marks')
        if not max_m:
            max_m = sub_cnt * 100
        pct_val = r.get('percentage')
        if pct_val is None:
            pct_val = (tm_val / max_m * 100) if max_m > 0 else 0.0
        pct_str = f"{pct_val:.2f}%" if res_status in ['Pass', 'Fail'] else '-'
        
        # Determine class category and result status string
        if res_status == "Pass":
            if sgpa_val >= 7.75: class_cat = "Distinction"
            elif sgpa_val >= 6.75: class_cat = "First Class"
            elif sgpa_val >= 5.75: class_cat = "Second Class"
            else: class_cat = "Pass Class"
            result_status_str = "Fail" if has_backlog else "Pass"
        elif res_status == "Fail":
            class_cat = "Fail Class"
            result_status_str = "Fail"
        else:
            class_cat = res_status
            result_status_str = res_status
            
        ws_all.cell(row=student_row_idx, column=c_col, value=grand_total).border = thin_border
        ws_all.cell(row=student_row_idx, column=c_col+1, value=pct_str).border = thin_border
        ws_all.cell(row=student_row_idx, column=c_col+2, value=sgpa_val if res_status in ['Pass', 'Fail'] else '-').border = thin_border
        ws_all.cell(row=student_row_idx, column=c_col+3, value=backlog_count if res_status in ['Pass', 'Fail'] else '-').border = thin_border
        ws_all.cell(row=student_row_idx, column=c_col+4, value=class_cat).border = thin_border
        ws_all.cell(row=student_row_idx, column=c_col+5, value=result_status_str).border = thin_border
        
        # Center align overall metric columns (6 columns)
        for offset in range(6):
            ws_all.cell(row=student_row_idx, column=c_col+offset).alignment = Alignment(horizontal='center')
            
        # Apply conditional coloring
        if res_status not in ['Pass', 'Fail']:
            ws_all.cell(row=student_row_idx, column=c_col+5).fill = yellow_fill
        elif has_backlog:
            for col in range(1, c_col + 6):
                # Check if column falls inside any failed subject range
                is_failed_col = False
                for start_col, end_col in failed_subject_cols:
                    if start_col <= col <= end_col:
                        is_failed_col = True
                        break
                
                if col == c_col + 5: # RESULT column
                    ws_all.cell(row=student_row_idx, column=col).fill = red_fill
                elif is_failed_col:
                    ws_all.cell(row=student_row_idx, column=col).fill = skyblue_fill
                else:
                    ws_all.cell(row=student_row_idx, column=col).fill = yellow_fill
        else:
            # All subjects pass
            ws_all.cell(row=student_row_idx, column=c_col+5).fill = green_fill
            
        student_row_idx += 1

    ws_all.column_dimensions['A'].width = 8
    ws_all.column_dimensions['B'].width = 15
    ws_all.column_dimensions['C'].width = 25

    # --- SHEET 4: TOP 5 MARKS SECURED ---
    ws_top_marks = wb.create_sheet('Top 5 Marks Secured')
    ws_top_marks.views.sheetView[0].showGridLines = True
    ws_top_marks.cell(row=1, column=1, value="Top 5 Students (Marks Secured)").font = Font(bold=True, size=14, color='1E1B4B')
    
    valid_student_marks = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                tm = int(r.get('total_marks', 0))
            except:
                tm = 0
            valid_student_marks.append((tm, r))
    valid_student_marks.sort(key=lambda x: x[0], reverse=True)
    
    headers_top_marks = ["Rank", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "Result"]
    for c_idx, h in enumerate(headers_top_marks, 1):
        cell = ws_top_marks.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
        
    top_row_idx = 4
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
            
        ws_top_marks.cell(row=top_row_idx, column=1, value=current_rank).border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=2, value=r.get('usn')).border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=4, value=max_m).border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=5, value=tm).border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=6, value=f"{pct:.2f}%").border = thin_border
        ws_top_marks.cell(row=top_row_idx, column=7, value=r.get('status')).border = thin_border
        
        for c in [1, 2, 4, 5, 6, 7]:
            ws_top_marks.cell(row=top_row_idx, column=c).alignment = Alignment(horizontal='center')
            
        # Determine rank fill
        rank_fill = None
        if current_rank == 1: rank_fill = gold_fill
        elif current_rank == 2: rank_fill = silver_fill
        elif current_rank == 3: rank_fill = bronze_fill
        elif current_rank == 4: rank_fill = rank4_fill
        elif current_rank == 5: rank_fill = rank5_fill

        if rank_fill:
            for col_idx in range(1, 8):
                ws_top_marks.cell(row=top_row_idx, column=col_idx).fill = rank_fill
                
        top_row_idx += 1
        
    for col in ws_top_marks.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_top_marks.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # --- SHEET 5: TOP 5 SGPA ---
    ws_top_sgpa = wb.create_sheet('Top 5 SGPA')
    ws_top_sgpa.views.sheetView[0].showGridLines = True
    ws_top_sgpa.cell(row=1, column=1, value="Top 5 Students (SGPA)").font = Font(bold=True, size=14, color='1E1B4B')
    
    valid_student_sgpa = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                sg = float(r.get('sgpa', 0.0))
            except:
                sg = 0.0
            valid_student_sgpa.append((sg, r))
    valid_student_sgpa.sort(key=lambda x: x[0], reverse=True)
    
    headers_top_sgpa = ["Rank", "USN", "Student Name", "SGPA", "Total Marks", "Marks Secured", "Result"]
    for c_idx, h in enumerate(headers_top_sgpa, 1):
        cell = ws_top_sgpa.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
        
    top_row_idx = 4
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
            
        ws_top_sgpa.cell(row=top_row_idx, column=1, value=current_rank).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=2, value=r.get('usn')).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=4, value=sg).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=5, value=max_m).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=6, value=tm).border = thin_border
        ws_top_sgpa.cell(row=top_row_idx, column=7, value=r.get('status')).border = thin_border
        
        for c in [1, 2, 4, 5, 6, 7]:
            ws_top_sgpa.cell(row=top_row_idx, column=c).alignment = Alignment(horizontal='center')
            
        # Determine rank fill
        rank_fill = None
        if current_rank == 1: rank_fill = gold_fill
        elif current_rank == 2: rank_fill = silver_fill
        elif current_rank == 3: rank_fill = bronze_fill
        elif current_rank == 4: rank_fill = rank4_fill
        elif current_rank == 5: rank_fill = rank5_fill

        if rank_fill:
            for col_idx in range(1, 8):
                ws_top_sgpa.cell(row=top_row_idx, column=col_idx).fill = rank_fill
                
        top_row_idx += 1
        
    for col in ws_top_sgpa.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_top_sgpa.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # --- SHEET 6: STUDENTS WITH BACKLOGS ---
    ws_backlogs = wb.create_sheet('Students with Backlogs')
    ws_backlogs.views.sheetView[0].showGridLines = True
    ws_backlogs.cell(row=1, column=1, value="Students with Backlogs").font = Font(bold=True, size=14, color='991B1B')
    
    headers_bl = ["SL.No", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "SGPA", "No. of Backlogs", "Result"]
    for c_idx, h in enumerate(headers_bl, 1):
        cell = ws_backlogs.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
        
    bl_row_idx = 4
    sl_no_bl = 1
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            backlog_count = 0
            for sub_code, sub_data in r.get('subjects', {}).items():
                res_flag = str(sub_data.get('result', '')).upper()
                if res_flag in ['F', 'A', 'ABSENT', 'FAIL']:
                    backlog_count += 1
            
            if backlog_count > 0:
                try:
                    tm = int(r.get('total_marks', 0))
                except:
                    tm = 0
                sub_cnt = len(r.get('subjects', {}))
                max_m = r.get('max_marks')
                if not max_m:
                    max_m = sub_cnt * 100
                pct = r.get('percentage')
                if pct is None:
                    pct = (tm / max_m * 100) if max_m > 0 else 0.0
                    
                ws_backlogs.cell(row=bl_row_idx, column=1, value=sl_no_bl).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=2, value=r.get('usn')).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=4, value=max_m).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=5, value=tm).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=6, value=f"{pct:.2f}%").border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=7, value=float(r.get('sgpa', 0))).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=8, value=backlog_count).border = thin_border
                ws_backlogs.cell(row=bl_row_idx, column=9, value=r.get('status')).border = thin_border
                
                for col in range(1, 10):
                    ws_backlogs.cell(row=bl_row_idx, column=col).fill = yellow_fill
                    ws_backlogs.cell(row=bl_row_idx, column=col).border = thin_border
                    if col != 3:
                        ws_backlogs.cell(row=bl_row_idx, column=col).alignment = Alignment(horizontal='center')
                    
                bl_row_idx += 1
                sl_no_bl += 1
                
    for col in ws_backlogs.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_backlogs.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # --- SHEET 7: STUDENT RANKINGS (MARKS) ---
    ws_rank_marks = wb.create_sheet('Student Rankings (Marks)')
    ws_rank_marks.views.sheetView[0].showGridLines = True
    ws_rank_marks.cell(row=1, column=1, value="Student Rankings (by Marks Secured)").font = Font(bold=True, size=14, color='1E1B4B')
    
    valid_student_marks_all = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                tm = int(r.get('total_marks', 0))
            except:
                tm = 0
            valid_student_marks_all.append((tm, r))
    valid_student_marks_all.sort(key=lambda x: x[0], reverse=True)
    
    headers_rank_marks = ["Rank", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "Result"]
    for c_idx, h in enumerate(headers_rank_marks, 1):
        cell = ws_rank_marks.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
        
    rank_row_idx = 4
    current_rank = 0
    prev_marks = None
    for tm, r in valid_student_marks_all:
        if prev_marks is None or tm != prev_marks:
            current_rank += 1
        prev_marks = tm
        
        sub_cnt = len(r.get('subjects', {}))
        max_m = r.get('max_marks')
        if not max_m:
            max_m = sub_cnt * 100
            
        pct = r.get('percentage')
        if pct is None:
            pct = (tm / max_m * 100) if max_m > 0 else 0.0
            
        ws_rank_marks.cell(row=rank_row_idx, column=1, value=current_rank).border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=2, value=r.get('usn')).border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=4, value=max_m).border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=5, value=tm).border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=6, value=f"{pct:.2f}%").border = thin_border
        ws_rank_marks.cell(row=rank_row_idx, column=7, value=r.get('status')).border = thin_border
        
        for c in [1, 2, 4, 5, 6, 7]:
            ws_rank_marks.cell(row=rank_row_idx, column=c).alignment = Alignment(horizontal='center')
            
        rank_fill = None
        if current_rank == 1: rank_fill = gold_fill
        elif current_rank == 2: rank_fill = silver_fill
        elif current_rank == 3: rank_fill = bronze_fill
        elif current_rank == 4: rank_fill = rank4_fill
        elif current_rank == 5: rank_fill = rank5_fill

        if rank_fill:
            for col_idx in range(1, 8):
                ws_rank_marks.cell(row=rank_row_idx, column=col_idx).fill = rank_fill
                
        rank_row_idx += 1
        
    for col in ws_rank_marks.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_rank_marks.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # --- SHEET 8: STUDENT RANKINGS (SGPA) ---
    ws_rank_sgpa = wb.create_sheet('Student Rankings (SGPA)')
    ws_rank_sgpa.views.sheetView[0].showGridLines = True
    ws_rank_sgpa.cell(row=1, column=1, value="Student Rankings (by SGPA)").font = Font(bold=True, size=14, color='1E1B4B')
    
    valid_student_sgpa_all = []
    for r in results:
        if r.get('status') in ['Pass', 'Fail']:
            try:
                sg = float(r.get('sgpa', 0.0))
            except:
                sg = 0.0
            valid_student_sgpa_all.append((sg, r))
    valid_student_sgpa_all.sort(key=lambda x: x[0], reverse=True)
    
    headers_rank_sgpa = ["Rank", "USN", "Student Name", "SGPA", "Total Marks", "Marks Secured", "Result"]
    for c_idx, h in enumerate(headers_rank_sgpa, 1):
        cell = ws_rank_sgpa.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border
        
    rank_row_idx = 4
    current_rank = 0
    prev_sgpa = None
    for sg, r in valid_student_sgpa_all:
        if prev_sgpa is None or sg != prev_sgpa:
            current_rank += 1
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
            
        ws_rank_sgpa.cell(row=rank_row_idx, column=1, value=current_rank).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=2, value=r.get('usn')).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=3, value=r.get('name', 'N/A')).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=4, value=sg).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=5, value=max_m).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=6, value=tm).border = thin_border
        ws_rank_sgpa.cell(row=rank_row_idx, column=7, value=r.get('status')).border = thin_border
        
        for c in [1, 2, 4, 5, 6, 7]:
            ws_rank_sgpa.cell(row=rank_row_idx, column=c).alignment = Alignment(horizontal='center')
            
        rank_fill = None
        if current_rank == 1: rank_fill = gold_fill
        elif current_rank == 2: rank_fill = silver_fill
        elif current_rank == 3: rank_fill = bronze_fill
        elif current_rank == 4: rank_fill = rank4_fill
        elif current_rank == 5: rank_fill = rank5_fill

        if rank_fill:
            for col_idx in range(1, 8):
                ws_rank_sgpa.cell(row=rank_row_idx, column=col_idx).fill = rank_fill
                
        rank_row_idx += 1
        
    for col in ws_rank_sgpa.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_rank_sgpa.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # --- END OF EXCEL GENERATION ---

    # Save to buffer
    wb.save(output)
    output.seek(0)
    return output

def generate_csv_report(results, report_settings=None, user_id=None):
    """
    Generates a flattened CSV data report containing all student subject scores
    mapped to faculty and credit counts with report settings metadata.

    Credits and Faculty are sourced in priority order:
      1. Enriched sub_data (populated by calculate_sgpa_and_map_faculty from Institution Hub).
      2. Direct Institution Hub DB query (sub_map fallback).
      3. Last-resort defaults with a logged warning.
    """
    # Fetch Institution Hub subject mappings as a supplementary fallback source.
    # The primary source is sub_data already enriched by the analyzer.
    subject_mappings_db = []
    if user_id and report_settings:
        try:
            subject_mappings_db = db_instance.get_subjects(user_id, {
                'academic_year': report_settings.get('academic_year'),
                'scheme': report_settings.get('scheme'),
                'semester': report_settings.get('semester'),
                'department': report_settings.get('department')
            })
        except Exception as e:
            _logger.warning(f"[CSV] Could not fetch Institution Hub subjects: {e}")

    sub_map = {sm.get('subject_code', '').upper(): sm for sm in subject_mappings_db}
    
    rows = []
    for r in results:
        usn = r.get('usn')
        name = r.get('name', 'N/A')
        overall_sgpa = r.get('sgpa', 0.0)
        overall_status = r.get('status', 'Fail')
        
        subjects = r.get('subjects', {})
        if not subjects:
            rows.append({
                'USN': usn,
                'Student Name': name,
                'Department': report_settings.get('department', 'N/A') if report_settings else 'N/A',
                'Academic Year': report_settings.get('academic_year', 'N/A') if report_settings else 'N/A',
                'Semester': report_settings.get('semester', 'N/A') if report_settings else 'N/A',
                'Scheme': report_settings.get('scheme', 'N/A') if report_settings else 'N/A',
                'Examination': report_settings.get('examination', 'N/A') if report_settings else 'N/A',
                'Subject Code': '-',
                'Subject Name': '-',
                'Subject Type': '-',
                'Credits': 0,
                'Faculty Name': '-',
                'Internal': 0,
                'External': 0,
                'Total': 0,
                'Subject Result': '-',
                'SGPA': 0.0,
                'Student Status': overall_status
            })
        else:
            for code, sub_data in subjects.items():
                credits = sub_data.get('credits')
                faculty = sub_data.get('faculty')

                if credits is None or faculty is None:
                    mapped = sub_map.get(code.upper(), {})
                    if credits is None:
                        raw_c = mapped.get('credits')
                        if raw_c is not None:
                            try:
                                credits = int(raw_c)
                            except (ValueError, TypeError):
                                credits = None
                    if faculty is None:
                        faculty = mapped.get('faculty_name') or None

                if credits is None:
                    credits = 4
                if not faculty:
                    faculty = 'Unassigned'

                sub_name = sub_data.get('name', 'N/A')
                is_internal_only = sub_data.get('is_internal_only', False)

                rows.append({
                    'USN': usn,
                    'Student Name': name,
                    'Department': report_settings.get('department', 'N/A') if report_settings else 'N/A',
                    'Academic Year': report_settings.get('academic_year', 'N/A') if report_settings else 'N/A',
                    'Semester': report_settings.get('semester', 'N/A') if report_settings else 'N/A',
                    'Scheme': report_settings.get('scheme', 'N/A') if report_settings else 'N/A',
                    'Examination': report_settings.get('examination', 'N/A') if report_settings else 'N/A',
                    'Subject Code': code,
                    'Subject Name': sub_name,
                    'Subject Type': 'Internal Only' if is_internal_only else 'Regular',
                    'Credits': credits,
                    'Faculty Name': faculty,
                    'Internal': sub_data.get('internal', 0),
                    'External': sub_data.get('external', 0),
                    'Total': sub_data.get('total', 0),
                    'Subject Result': sub_data.get('result', ''),
                    'SGPA': overall_sgpa,
                    'Student Status': overall_status
                })
            
    df = pd.DataFrame(rows)
    return df.to_csv(index=False)
