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
    if not isinstance(results, list):
        results = []

    valid_results = [
        r for r in results 
        if isinstance(r, dict) and r.get('status') in ['Pass', 'Fail']
    ]
    
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
        
        for r in results:
            if not isinstance(r, dict): continue
            status = str(r.get('status', ''))
            if status == "Pass":
                total = r.get('total_marks', 0) or 0
                max_m = r.get('max_marks', 1) or 1
                per = (total / max_m) * 100 if max_m > 0 else 0
                if per >= 70: summary_data['FCD'] += 1
                elif per >= 60: summary_data['FC'] += 1
                elif per >= 40: summary_data['SC'] += 1
                else: summary_data['Fail'] += 1
            elif status == "Fail":
                summary_data['Fail'] += 1
            elif "Absent" in status or "No Res" in status:
                summary_data['Absent'] += 1

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

        # --- SHEET 2: SUBJECT-WISE RESULT ANALYSIS ---
        all_subjects_meta = {} # code -> name
        for r in valid_results:
            subjects = r.get('subjects') or {}
            if isinstance(subjects, dict):
                for sub_code, sub_data in subjects.items():
                    if sub_code not in all_subjects_meta:
                        if isinstance(sub_data, dict):
                            all_subjects_meta[sub_code] = sub_data.get('name', 'N/A')
                        else:
                            all_subjects_meta[sub_code] = 'N/A'

        if valid_results:
            sub_analysis = []
            for sub_code, sub_name in all_subjects_meta.items():
                stats = {'fcd': 0, 'fc': 0, 'sc': 0, 'fail': 0, 'absent': 0, 'appeared': 0}
                for r in valid_results:
                    subjects = r.get('subjects') or {}
                    sub_data = subjects.get(sub_code) if isinstance(subjects, dict) else None
                    if isinstance(sub_data, dict):
                        res = str(sub_data.get('result', '')).upper()
                        total = sub_data.get('total', 0) or 0
                        if res in ['A', 'ABSENT']:
                            stats['absent'] += 1
                        else:
                            stats['appeared'] += 1
                            if res in ['P', 'PASS']:
                                if total >= 70: stats['fcd'] += 1
                                elif total >= 60: stats['fc'] += 1
                                elif total >= 40: stats['sc'] += 1
                                else: stats['fail'] += 1
                            else:
                                stats['fail'] += 1
                
                passed = stats['fcd'] + stats['fc'] + stats['sc']
                p_per = round((passed / stats['appeared']) * 100, 2) if stats['appeared'] > 0 else 0
                
                sub_analysis.append({
                    'Subjects': sub_name,
                    'Sub code': sub_code,
                    'FCD (70-100%)': stats['fcd'],
                    'FC (60-69%)': stats['fc'],
                    'SC (40-59%)': stats['sc'],
                    'Fail': stats['fail'],
                    'Absent': stats['absent'],
                    'Total students Appeared': stats['appeared'],
                    'No of student passed': passed,
                    'Passing %age': p_per
                })
            
            info = all_subjects_meta[sub_code]
            res = str(sub_data.get('result', '')).upper()
            total = int(sub_data.get('total', 0))
            
            # Title for Sheet 2
            ws_sub.merge_cells('A1:J1')
            ws_sub['A1'] = "SUBJECT-WISE RESULT ANALYSIS"
            ws_sub['A1'].font = Font(bold=True, size=12)
            ws_sub['A1'].alignment = Alignment(horizontal='center')

            # Formatting & Conditional Color
            green_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
            red_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
            
            for row_idx, row in enumerate(ws_sub.iter_rows(min_row=4, max_row=ws_sub.max_row, min_col=1, max_col=10), 4):
                for cell in row:
                    cell.border = thin_border
                    if row_idx == 4:
                        cell.font = Font(bold=True)
                        cell.alignment = Alignment(horizontal='center', wrap_text=True)
                    else:
                        if cell.column >= 3:
                            cell.alignment = Alignment(horizontal='center')
                        if cell.column == 10:
                            if isinstance(cell.value, (int, float)):
                                cell.fill = green_fill if cell.value >= 80 else red_fill

            # Auto-adjust column widths
            for col in ws_sub.columns:
                max_length = 0
                column_letter = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except: pass
                ws_sub.column_dimensions[column_letter].width = min(max_length + 2, 40)

            # --- ADD CHART TO SHEET 1 (Referencing Sheet 2 data) ---
            if ws_sub.max_row > 4:
                chart = BarChart()
                chart.type = "col"
                chart.style = 10
                chart.title = "Subject wise %"
                chart.y_axis.title = 'PERCENTAGE'
                chart.x_axis.title = 'SUBJECTS'
                chart.height = 12
                chart.width = 25
                chart.x_axis.labelRotation = 4500 

                chart.dataLabels = DataLabelList()
                chart.dataLabels.showVal = True
                
                data = Reference(ws_sub, min_col=10, min_row=4, max_row=ws_sub.max_row)
                cats = Reference(ws_sub, min_col=1, min_row=5, max_row=ws_sub.max_row)
                
                chart.add_data(data, titles_from_data=True)
                chart.set_categories(cats)
                chart.legend = None
                
                if chart.series:
                    chart.series[0].graphical_properties = GraphicalProperties(solidFill=ColorChoice(srgbClr="4F81BD"))

                ws_sum.add_chart(chart, "A12")

        # --- SHEET 3: All Students ---
        all_subject_codes = sorted(list(all_subjects_meta.keys()))
        df_all = []
        for i, r in enumerate(results):
            if not isinstance(r, dict): continue
            res_status = str(r.get('status', 'N/A'))
            student_name = r.get('name') if r.get('name') else res_status
            row = {'SL.No': i + 1, 'USN': r.get('usn', 'N/A'), 'NAME': student_name}
            backlog_count = 0
            subjects = r.get('subjects') or {}
            for sub_code in all_subject_codes:
                if res_status in ['Pass', 'Fail']:
                    sub_data = subjects.get(sub_code) if isinstance(subjects, dict) else None
                    if isinstance(sub_data, dict):
                        row[f'{sub_code}_INT'] = sub_data.get('internal', 0)
                        row[f'{sub_code}_EXT'] = sub_data.get('external', 0)
                        row[f'{sub_code}_TOT'] = sub_data.get('total', 0)
                        res_flag = str(sub_data.get('result', '')).upper()
                        row[f'{sub_code}_PT'] = res_flag
                        if res_flag in ['F', 'A', 'ABSENT', 'FAIL']: backlog_count += 1
                    else:
                        for k in ['INT', 'EXT', 'TOT', 'PT']: row[f'{sub_code}_{k}'] = '-'
                else:
                    for k in ['INT', 'EXT', 'TOT', 'PT']: row[f'{sub_code}_{k}'] = '-'
            row['Grand Total'] = r.get('total_marks', 0) if res_status in ['Pass', 'Fail'] else '-'
            row['No.of B/L'] = backlog_count if res_status in ['Pass', 'Fail'] else '-'
            df_all.append(row)
        
        df_all_final = pd.DataFrame(df_all)
        df_all_final.to_excel(writer, sheet_name='All Students', index=False, startrow=1)
        ws_all = writer.sheets['All Students']
        
        # Advanced Merged Headers for Sheet 3
        header_fill = PatternFill(start_color='FFF2CC', end_color='FFF2CC', fill_type='solid')
        yellow_row_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
        
        for col_idx, col_name in enumerate(['SL.No', 'USN', 'NAME'], 1):
            ws_all.cell(row=1, column=col_idx, value=col_name)
            ws_all.merge_cells(start_row=1, start_column=col_idx, end_row=2, end_column=col_idx)

        # Dynamic Subject Headers (Horizontal Merge)
        curr_col = 4
        for sub_code in all_subject_codes:
            sub_name = all_subjects_meta.get(sub_code, "N/A")
            ws_all.cell(row=1, column=curr_col, value=f"{sub_code} - {sub_name}")
            ws_all.merge_cells(start_row=1, start_column=curr_col, end_row=1, end_column=curr_col+3)
            # Row 2 sub-headers
            for off, txt in enumerate(['INT', 'EXT', 'TOT', 'PT']):
                ws_all.cell(row=2, column=curr_col+off, value=txt)
            curr_col += 4
            
        # Summary columns vertical merge
        for col_name in ['Grand Total', 'No.of B/L']:
            ws_all.cell(row=1, column=curr_col, value=col_name)
            ws_all.merge_cells(start_row=1, start_column=curr_col, end_row=2, end_column=curr_col)
            curr_col += 1

        # Apply Styling & Backlog Highlighting
        for row_idx, row in enumerate(ws_all.iter_rows(min_row=1, max_row=ws_all.max_row, min_col=1, max_col=ws_all.max_column), 1):
            if row_idx <= 2:
                for cell in row:
                    cell.font = Font(bold=True, size=9)
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    cell.border = thin_border
            else:
                bl_cell = row[-1]
                has_backlog = False
                try:
                    if isinstance(bl_cell.value, int) and bl_cell.value > 0: has_backlog = True
                except: pass
                
                for cell in row:
                    cell.border = thin_border
                    cell.alignment = Alignment(horizontal='center')
                    if has_backlog:
                        cell.fill = yellow_row_fill
        
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

        # --- SHEET 4: Top 5 Toppers ---
        toppers = sorted(
            [r for r in valid_results if r.get('status') == 'Pass'], 
            key=lambda x: x.get('total_marks', 0) or 0, 
            reverse=True
        )[:5]

        df_toppers = pd.DataFrame([{
            'Rank': i+1, 
            'USN': t.get('usn', 'N/A'), 
            'Name': t.get('name', 'N/A'), 
            'Total': t.get('total_marks', 0)
        } for i, t in enumerate(toppers)])
        df_toppers.to_excel(writer, sheet_name='Top 5 Toppers', index=False)

        # Ensure all sheets are visible and active sheet is valid BEFORE ExcelWriter closes & saves
        try:
            wb = writer.book
            if wb and wb.worksheets:
                for ws in wb.worksheets:
                    ws.sheet_state = 'visible'
                wb.active = 0
        except Exception:
            pass

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
