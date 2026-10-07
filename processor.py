import pandas as pd
import openpyxl
import logging
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.colors import ColorChoice
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule, FormulaRule
import io
import json
import datetime
from typing import List, Dict, Any, Union
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
    except Exception:
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


def update_revaluation_result(
    results: List[Dict[str, Any]],
    usn: str,
    subject_code: str,
    new_external: Union[int, float, str]
) -> Dict[str, Any]:
    """
    Authoritative VTU Revaluation Updater:
    - Finds student by USN and subject by subject_code
    - STRICT BUSINESS RULE: Internal marks NEVER change through revaluation.
    - Explicitly asserts that subject['internal'] remains identical.
    - Recalculates:
        1. Subject total marks
        2. Subject result ('P' / 'F' / 'A')
        3. Subject grade & grade point
        4. Student backlog count
        5. Student total marks secured & percentage
        6. Student SGPA
        7. Student overall result / status ('Pass' / 'Fail')
        8. Student class classification
    """
    target_student = None
    target_usn = str(usn).strip().upper()
    target_sub = str(subject_code).strip().upper()

    for r in results:
        if str(r.get('usn', '')).strip().upper() == target_usn:
            target_student = r
            break

    if not target_student:
        raise ValueError(f"Student with USN '{usn}' not found in results dataset.")

    subjects = target_student.setdefault('subjects', {})
    matching_key = None
    for k in subjects.keys():
        if k.strip().upper() == target_sub:
            matching_key = k
            break

    if not matching_key:
        raise ValueError(f"Subject '{subject_code}' not found for student '{usn}'.")

    sub = subjects[matching_key]

    # SECTION 5: INTERNAL MARK PROTECTION
    old_internal = sub.get('internal', 0)

    # Parse and update ONLY external mark
    try:
        new_ext_val = int(new_external)
    except (ValueError, TypeError):
        new_ext_val = str(new_external).strip().upper()

    sub['external'] = new_ext_val

    # Strict Assertion: Internal mark must NOT change
    assert sub['internal'] == old_internal, (
        f"Critical Security Violation: Internal marks changed during revaluation! "
        f"Expected {old_internal}, got {sub.get('internal')}"
    )

    # Recalculate subject total
    is_int_only = sub.get('is_internal_only', False)
    try:
        int_num = int(old_internal) if str(old_internal).isdigit() else int(float(old_internal))
    except Exception:
        int_num = 0

    try:
        ext_num = int(new_ext_val) if str(new_ext_val).isdigit() else int(float(new_ext_val))
    except Exception:
        ext_num = 0

    tot_num = int_num + ext_num
    sub['total'] = tot_num

    # Recalculate subject result
    ext_str = str(new_ext_val).strip().upper()
    int_str = str(old_internal).strip().upper()

    if ext_str in ['A', 'ABSENT'] or int_str in ['A', 'ABSENT']:
        sub['result'] = 'A'
        sub['grade_point'] = 0
    elif is_int_only:
        sub['result'] = 'P' if tot_num >= 40 else 'F'
    else:
        sub['result'] = 'P' if (ext_num >= 18 and tot_num >= 40) else 'F'

    # Recalculate grade point
    if sub['result'] == 'P':
        if tot_num >= 90: gp = 10
        elif tot_num >= 80: gp = 9
        elif tot_num >= 70: gp = 8
        elif tot_num >= 60: gp = 7
        elif tot_num >= 50: gp = 6
        elif tot_num >= 45: gp = 5
        elif tot_num >= 40: gp = 4
        else: gp = 0
    else:
        gp = 0
    sub['grade_point'] = gp

    # Recalculate student totals
    total_obtained = sum(
        int(s.get('total', 0)) for s in subjects.values()
        if isinstance(s.get('total'), (int, float))
    )
    max_possible = len(subjects) * 100
    target_student['total_marks'] = total_obtained
    target_student['max_marks'] = max_possible
    target_student['percentage'] = round((total_obtained / max_possible) * 100, 2) if max_possible > 0 else 0.0

    # Recalculate SGPA
    tot_credits = 0
    tot_gp_credits = 0
    for s in subjects.values():
        cr = int(s.get('credits', 4))
        gp_val = s.get('grade_point', 0)
        tot_credits += cr
        tot_gp_credits += gp_val * cr
    target_student['sgpa'] = round(tot_gp_credits / tot_credits, 2) if tot_credits > 0 else 0.0

    # Recalculate backlogs & overall status
    has_backlog = False
    for s in subjects.values():
        r_flag = str(s.get('result', '')).upper()
        if r_flag in ['F', 'FAIL', 'A', 'ABSENT', 'NE', 'NOT ELIGIBLE', 'N']:
            has_backlog = True
            break
    target_student['status'] = 'Fail' if has_backlog else 'Pass'

    # Secondary verification check: Internal marks remain pristine
    assert sub['internal'] == old_internal, "Internal mark mutation detected post-calculation!"

    return target_student


def update_student_marks(student_result, subject_code, internal=None, external=None, is_internal_only=None):
    """
    Authoritative single-source update for general mark adjustments.
    """
    subjects = student_result.setdefault('subjects', {})
    sub = subjects.setdefault(subject_code, {})
    
    if internal is not None:
        try: sub['internal'] = int(internal)
        except (ValueError, TypeError): sub['internal'] = internal
    if external is not None:
        try: sub['external'] = int(external)
        except (ValueError, TypeError): sub['external'] = external
    if is_internal_only is not None:
        sub['is_internal_only'] = bool(is_internal_only)
        
    int_val = sub.get('internal', 0)
    ext_val = sub.get('external', 0)
    try:
        int_num = int(int_val) if isinstance(int_val, (int, float, str)) and str(int_val).isdigit() else 0
        ext_num = int(ext_val) if isinstance(ext_val, (int, float, str)) and str(ext_val).isdigit() else 0
        tot_num = int_num + ext_num
        sub['total'] = tot_num
    except Exception:
        tot_num = 0
        sub['total'] = tot_num
        
    is_int_only = sub.get('is_internal_only', False)
    ext_str = str(ext_val).strip().upper()
    int_str = str(int_val).strip().upper()
    
    if ext_str in ['A', 'ABSENT'] or int_str in ['A', 'ABSENT']:
        sub['result'] = 'A'
        sub['grade_point'] = 0
    elif is_int_only:
        sub['result'] = 'P' if tot_num >= 40 else 'F'
    else:
        sub['result'] = 'P' if (ext_num >= 18 and tot_num >= 40) else 'F'
            
    if sub['result'] == 'P':
        if tot_num >= 90: gp = 10
        elif tot_num >= 80: gp = 9
        elif tot_num >= 70: gp = 8
        elif tot_num >= 60: gp = 7
        elif tot_num >= 50: gp = 6
        elif tot_num >= 45: gp = 5
        elif tot_num >= 40: gp = 4
        else: gp = 0
    else:
        gp = 0
    sub['grade_point'] = gp
    
    total_obtained = sum(int(s.get('total', 0)) for s in subjects.values() if isinstance(s.get('total'), (int, float)))
    max_possible = len(subjects) * 100
    student_result['total_marks'] = total_obtained
    student_result['max_marks'] = max_possible
    student_result['percentage'] = round((total_obtained / max_possible) * 100, 2) if max_possible > 0 else 0.0
    
    tot_credits = 0
    tot_gp_credits = 0
    for s in subjects.values():
        cr = int(s.get('credits', 4))
        gp_val = s.get('grade_point', 0)
        tot_credits += cr
        tot_gp_credits += gp_val * cr
    student_result['sgpa'] = round(tot_gp_credits / tot_credits, 2) if tot_credits > 0 else 0.0
    
    has_backlog = False
    for s in subjects.values():
        r_flag = str(s.get('result', '')).upper()
        if r_flag in ['F', 'FAIL', 'A', 'ABSENT', 'NE', 'NOT ELIGIBLE', 'N']:
            has_backlog = True
            break
    student_result['status'] = 'Fail' if has_backlog else 'Pass'
    return student_result


def update_result_in_dataset(results, usn, subject_code, internal=None, external=None, is_internal_only=None):
    """
    Finds a student by USN in the master results list and updates their marks.
    """
    for r in results:
        if r.get('usn', '').strip().upper() == str(usn).strip().upper():
            return update_student_marks(r, subject_code, internal=internal, external=external, is_internal_only=is_internal_only)
    return None


def generate_excel_report(results, report_settings=None, user_id=None):
    """
    Generates a professional 8-sheet dynamic Excel workbook matching AcadFusion AI specifications:
      1. 'Overall Result'
      2. 'Subject Analysis'
      3. 'All Students'
      4. 'Top 5 Marks Secured'
      5. 'Top 5 SGPA'
      6. 'Students with Backlogs'
      7. 'Student Rankings (Marks)'
      8. 'Student Rankings (SGPA)'

    Architecture: Single Source of Truth
    The 'All Students' sheet serves as the authoritative master data sheet.
    All dependent summaries, pass/fail counts, percentages, subject analytics,
    Topper rankings, and backlog filtering are driven by live native Excel formulas.
    Modifying any mark in 'All Students' automatically recalculates every dependent sheet
    in Microsoft Excel without manual intervention.
    """
    if report_settings is None:
        report_settings = {}
        
    profile = {}
    subject_mappings_db = []
    
    if user_id:
        try:
            profile = db_instance.get_institution_profile(user_id) or {}
            if report_settings:
                subject_mappings_db = db_instance.get_subjects(user_id, {
                    'academic_year': report_settings.get('academic_year'),
                    'scheme': report_settings.get('scheme'),
                    'semester': report_settings.get('semester'),
                    'department': report_settings.get('department')
                })
        except Exception as e:
            _logger.warning(f"Error fetching metadata for Excel report: {e}")

    sub_map = {sm.get('subject_code', '').upper(): sm for sm in subject_mappings_db}

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove default blank sheet

    # Configure Automatic Calculation on Load
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = 'auto'

    # Styles
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    header_fill = PatternFill(start_color='1E1B4B', end_color='1E1B4B', fill_type='solid')
    sub_header_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    green_fill = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')
    red_fill = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
    yellow_fill = PatternFill(start_color='FEF08A', end_color='FEF08A', fill_type='solid')
    skyblue_fill = PatternFill(start_color='BAE6FD', end_color='BAE6FD', fill_type='solid')

    # Custom Rank Color Fills
    gold_fill = PatternFill(start_color='FDE047', end_color='FDE047', fill_type='solid')
    silver_fill = PatternFill(start_color='CBD5E1', end_color='CBD5E1', fill_type='solid')
    bronze_fill = PatternFill(start_color='FDBA74', end_color='FDBA74', fill_type='solid')
    rank4_fill = PatternFill(start_color='E9D5FF', end_color='E9D5FF', fill_type='solid')
    rank5_fill = PatternFill(start_color='CCFBF1', end_color='CCFBF1', fill_type='solid')

    # Collect unique subjects across results preserving order
    all_subjects_meta = {}
    import re as _re
    sub_map_wildcards = [(k, v) for k, v in sub_map.items() if '**' in k]

    for r in results:
        for sub_code, sub_data in r.get('subjects', {}).items():
            norm_code = sub_code.strip().upper()
            if norm_code not in all_subjects_meta:
                credits_val = sub_data.get('credits')
                faculty_val = sub_data.get('faculty')

                if credits_val is None or faculty_val is None:
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
                            try: credits_val = int(mapped.get('credits', 4))
                            except Exception: credits_val = 4
                        if faculty_val is None:
                            faculty_val = mapped.get('faculty_name') or 'Unassigned'

                if credits_val is None:
                    credits_val = 4
                if not faculty_val:
                    faculty_val = 'Unassigned'

                all_subjects_meta[norm_code] = {
                    'name': sub_data.get('name', 'N/A'),
                    'credits': credits_val,
                    'faculty': faculty_val,
                    'is_internal_only': sub_data.get('is_internal_only', False)
                }

    all_subject_codes = sorted(list(all_subjects_meta.keys()))
    num_students = len(results)
    max_marks_per_student = len(all_subject_codes) * 100 if all_subject_codes else 100
    r_start = 5
    r_end = 5 + num_students - 1 if num_students > 0 else 5

    # =========================================================================
    # --- SHEET 3: ALL STUDENTS (MASTER SHEET) ---
    # Created first so dependent sheets can establish valid formula references
    # =========================================================================
    ws_all = wb.create_sheet('All Students')
    ws_all.views.sheetView[0].showGridLines = True
    ws_all.cell(row=1, column=1, value="Detailed Student Marksheet").font = Font(bold=True, size=14, color='1E1B4B')

    # Multi-row Headers
    ws_all.cell(row=3, column=1, value="SL.No")
    ws_all.merge_cells("A3:A4")
    ws_all.cell(row=3, column=2, value="USN")
    ws_all.merge_cells("B3:B4")
    ws_all.cell(row=3, column=3, value="Student Name")
    ws_all.merge_cells("C3:C4")

    subject_col_map = {}
    curr_col = 4
    for code in all_subject_codes:
        ws_all.cell(row=3, column=curr_col, value=code)
        ws_all.merge_cells(start_row=3, start_column=curr_col, end_row=3, end_column=curr_col + 3)

        ws_all.cell(row=4, column=curr_col, value="INT")
        ws_all.cell(row=4, column=curr_col + 1, value="EXT")
        ws_all.cell(row=4, column=curr_col + 2, value="TOT")
        ws_all.cell(row=4, column=curr_col + 3, value="RES")

        subject_col_map[code] = {
            'int': get_column_letter(curr_col),
            'ext': get_column_letter(curr_col + 1),
            'tot': get_column_letter(curr_col + 2),
            'res': get_column_letter(curr_col + 3),
            'credits': all_subjects_meta[code]['credits'],
            'is_internal_only': all_subjects_meta[code]['is_internal_only']
        }
        curr_col += 4

    # Derived student metric columns
    col_grand_total = get_column_letter(curr_col)
    ws_all.cell(row=3, column=curr_col, value="Grand Total")
    ws_all.merge_cells(start_row=3, start_column=curr_col, end_row=4, end_column=curr_col)

    col_percentage = get_column_letter(curr_col + 1)
    ws_all.cell(row=3, column=curr_col + 1, value="Percentage")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 1, end_row=4, end_column=curr_col + 1)

    col_sgpa = get_column_letter(curr_col + 2)
    ws_all.cell(row=3, column=curr_col + 2, value="SGPA")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 2, end_row=4, end_column=curr_col + 2)

    col_backlogs = get_column_letter(curr_col + 3)
    ws_all.cell(row=3, column=curr_col + 3, value="No. of B/L")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 3, end_row=4, end_column=curr_col + 3)

    col_class = get_column_letter(curr_col + 4)
    ws_all.cell(row=3, column=curr_col + 4, value="Class Classify")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 4, end_row=4, end_column=curr_col + 4)

    col_result = get_column_letter(curr_col + 5)
    ws_all.cell(row=3, column=curr_col + 5, value="RESULT")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 5, end_row=4, end_column=curr_col + 5)

    # Hidden Helper Columns for dynamic sorting, ranking, and backlog filtering
    col_rank_marks = get_column_letter(curr_col + 6)
    ws_all.cell(row=3, column=curr_col + 6, value="Helper_Rank_Marks")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 6, end_row=4, end_column=curr_col + 6)

    col_rank_sgpa = get_column_letter(curr_col + 7)
    ws_all.cell(row=3, column=curr_col + 7, value="Helper_Rank_SGPA")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 7, end_row=4, end_column=curr_col + 7)

    col_rank_all_marks = get_column_letter(curr_col + 8)
    ws_all.cell(row=3, column=curr_col + 8, value="Helper_Rank_All_Marks")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 8, end_row=4, end_column=curr_col + 8)

    col_rank_all_sgpa = get_column_letter(curr_col + 9)
    ws_all.cell(row=3, column=curr_col + 9, value="Helper_Rank_All_SGPA")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 9, end_row=4, end_column=curr_col + 9)

    col_backlog_idx = get_column_letter(curr_col + 10)
    ws_all.cell(row=3, column=curr_col + 10, value="Helper_Backlog_Index")
    ws_all.merge_cells(start_row=3, start_column=curr_col + 10, end_row=4, end_column=curr_col + 10)

    for h_col in [col_rank_marks, col_rank_sgpa, col_rank_all_marks, col_rank_all_sgpa, col_backlog_idx]:
        ws_all.column_dimensions[h_col].hidden = True

    # Format header rows
    total_all_cols = curr_col + 10
    for row in range(3, 5):
        for col in range(1, total_all_cols + 1):
            cell = ws_all.cell(row=row, column=col)
            cell.font = Font(bold=True, size=9)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.fill = sub_header_fill
            cell.border = thin_border

    # Populate Student Rows with Dynamic Formulas
    total_course_credits = sum(all_subjects_meta[c]['credits'] for c in all_subject_codes) or 1

    for idx, r in enumerate(results):
        r_num = r_start + idx

        # Master values
        ws_all.cell(row=r_num, column=1, value=idx + 1).border = thin_border
        ws_all.cell(row=r_num, column=2, value=str(r.get('usn', ''))).border = thin_border
        ws_all.cell(row=r_num, column=3, value=str(r.get('name', 'N/A'))).border = thin_border

        tot_cells = []
        res_cells = []
        gp_terms = []

        sub_col_idx = 4
        for code in all_subject_codes:
            sub_info = subject_col_map[code]
            c_int = sub_info['int']
            c_ext = sub_info['ext']
            c_tot = sub_info['tot']
            c_res = sub_info['res']
            credits_num = sub_info['credits']
            is_int_only = sub_info['is_internal_only']

            sub_data = r.get('subjects', {}).get(code)
            if sub_data:
                raw_int = sub_data.get('internal', 0)
                raw_ext = sub_data.get('external', 0)
                try: int_val = int(raw_int) if str(raw_int).isdigit() else raw_int
                except Exception: int_val = raw_int
                try: ext_val = int(raw_ext) if str(raw_ext).isdigit() else raw_ext
                except Exception: ext_val = raw_ext
            else:
                int_val = "-"
                ext_val = "-"

            ws_all.cell(row=r_num, column=sub_col_idx, value=int_val).border = thin_border
            ws_all.cell(row=r_num, column=sub_col_idx + 1, value=ext_val).border = thin_border

            # Dynamic Subject Total Formula
            tot_formula = f'=IF(AND({c_int}{r_num}="-", {c_ext}{r_num}="-"), "-", IF(ISNUMBER({c_int}{r_num}), {c_int}{r_num}, 0) + IF(ISNUMBER({c_ext}{r_num}), {c_ext}{r_num}, 0))'
            ws_all.cell(row=r_num, column=sub_col_idx + 2, value=tot_formula).border = thin_border

            # Dynamic Subject Result Formula (VTU Passing Rules)
            if is_int_only:
                res_formula = f'=IF(AND({c_int}{r_num}="-", {c_ext}{r_num}="-"), "-", IF(OR({c_int}{r_num}="A", {c_ext}{r_num}="A", {c_ext}{r_num}="ABSENT"), "A", IF(AND(ISNUMBER({c_tot}{r_num}), {c_tot}{r_num}>=40), "P", "F")))'
            else:
                res_formula = f'=IF(AND({c_int}{r_num}="-", {c_ext}{r_num}="-"), "-", IF(OR({c_int}{r_num}="A", {c_ext}{r_num}="A", {c_ext}{r_num}="ABSENT"), "A", IF(AND(ISNUMBER({c_ext}{r_num}), {c_ext}{r_num}>=18, ISNUMBER({c_tot}{r_num}), {c_tot}{r_num}>=40), "P", "F")))'
            ws_all.cell(row=r_num, column=sub_col_idx + 3, value=res_formula).border = thin_border

            tot_cells.append(f'{c_tot}{r_num}')
            res_cells.append(f'{c_res}{r_num}')

            # Grade point term for SGPA calculation
            gp_term = f'({credits_num}*IF({c_res}{r_num}="P", IF({c_tot}{r_num}>=90, 10, IF({c_tot}{r_num}>=80, 9, IF({c_tot}{r_num}>=70, 8, IF({c_tot}{r_num}>=60, 7, IF({c_tot}{r_num}>=50, 6, IF({c_tot}{r_num}>=45, 5, 4)))))), 0))'
            gp_terms.append(gp_term)

            sub_col_idx += 4

        # Derived Grand Total
        gt_formula = f'=SUM({", ".join(tot_cells)})' if tot_cells else '0'
        ws_all.cell(row=r_num, column=curr_col, value=gt_formula).border = thin_border

        # Derived Percentage (based on enrolled subjects)
        enrolled_cnt_expr = " + ".join([f'IF({rc}<>"-", 1, 0)' for rc in res_cells]) if res_cells else '1'
        pct_formula = f'=IF(({enrolled_cnt_expr})>0, ROUND(({col_grand_total}{r_num}/(({enrolled_cnt_expr})*100))*100, 2), 0)'
        ws_all.cell(row=r_num, column=curr_col + 1, value=pct_formula).border = thin_border

        # Derived SGPA Formula (based on enrolled subject credits)
        gp_sum_expr = " + ".join(gp_terms) if gp_terms else "0"
        credit_sum_expr = " + ".join([f'IF({subject_col_map[c]["res"]}{r_num}<>"-", {subject_col_map[c]["credits"]}, 0)' for c in all_subject_codes]) or '1'
        sgpa_formula = f'=IF(({credit_sum_expr})>0, ROUND(({gp_sum_expr})/({credit_sum_expr}), 2), 0)'
        ws_all.cell(row=r_num, column=curr_col + 2, value=sgpa_formula).border = thin_border

        # Derived Backlog Count (only actual 'F', 'A', 'ABSENT' count as backlogs; un-enrolled '-' do not)
        bl_formula = " + ".join([f'IF(OR({rc}="F", {rc}="A", {rc}="ABSENT"), 1, 0)' for rc in res_cells]) if res_cells else '0'
        ws_all.cell(row=r_num, column=curr_col + 3, value=f'={bl_formula}').border = thin_border

        # Derived Class Classification
        absent_checks = ", ".join([f'AND({rc}<>"-", {rc}="A")' for rc in res_cells]) if res_cells else 'FALSE'
        class_formula = f'=IF(OR({absent_checks}), "Absent", IF({col_backlogs}{r_num}>0, "Fail Class", IF({col_sgpa}{r_num}>=7.75, "Distinction", IF({col_sgpa}{r_num}>=6.75, "First Class", IF({col_sgpa}{r_num}>=5.75, "Second Class", "Pass Class")))))'
        ws_all.cell(row=r_num, column=curr_col + 4, value=class_formula).border = thin_border

        # Derived Student Result Status
        result_status_formula = f'=IF(OR({absent_checks}), "Absent", IF({col_backlogs}{r_num}>0, "Fail", "Pass"))'
        ws_all.cell(row=r_num, column=curr_col + 5, value=result_status_formula).border = thin_border

        # Hidden Helper: Topper Rank (Marks) with deterministic tie-breaking for passing students
        rank_marks_formula = (
            f'=IF({col_result}{r_num}="Pass", '
            f'COUNTIFS(${col_result}$5:${col_result}${r_end}, "Pass", ${col_grand_total}$5:${col_grand_total}${r_end}, ">" & {col_grand_total}{r_num}) + '
            f'COUNTIFS(${col_result}$5:${col_result}{r_num}, "Pass", ${col_grand_total}$5:${col_grand_total}{r_num}, "=" & {col_grand_total}{r_num}), "-")'
        )
        ws_all.cell(row=r_num, column=curr_col + 6, value=rank_marks_formula).border = thin_border

        # Hidden Helper: Topper Rank (SGPA) with deterministic tie-breaking for passing students
        rank_sgpa_formula = (
            f'=IF({col_result}{r_num}="Pass", '
            f'COUNTIFS(${col_result}$5:${col_result}${r_end}, "Pass", ${col_sgpa}$5:${col_sgpa}${r_end}, ">" & {col_sgpa}{r_num}) + '
            f'COUNTIFS(${col_result}$5:${col_result}{r_num}, "Pass", ${col_sgpa}$5:${col_sgpa}{r_num}, "=" & {col_sgpa}{r_num}), "-")'
        )
        ws_all.cell(row=r_num, column=curr_col + 7, value=rank_sgpa_formula).border = thin_border

        # Hidden Helper: Complete Class Rank (Marks)
        rank_all_marks_formula = (
            f'=COUNTIF(${col_grand_total}$5:${col_grand_total}${r_end}, ">" & {col_grand_total}{r_num}) + '
            f'COUNTIF(${col_grand_total}$5:${col_grand_total}{r_num}, "=" & {col_grand_total}{r_num})'
        )
        ws_all.cell(row=r_num, column=curr_col + 8, value=rank_all_marks_formula).border = thin_border

        # Hidden Helper: Complete Class Rank (SGPA)
        rank_all_sgpa_formula = (
            f'=COUNTIF(${col_sgpa}$5:${col_sgpa}${r_end}, ">" & {col_sgpa}{r_num}) + '
            f'COUNTIF(${col_sgpa}$5:${col_sgpa}{r_num}, "=" & {col_sgpa}{r_num})'
        )
        ws_all.cell(row=r_num, column=curr_col + 9, value=rank_all_sgpa_formula).border = thin_border

        # Hidden Helper: Dynamic Backlog Student Index (1, 2, 3... for backlog students, "-" otherwise)
        backlog_idx_formula = f'=IF({col_backlogs}{r_num}>0, COUNTIFS(${col_backlogs}$5:${col_backlogs}{r_num}, ">0"), "-")'
        ws_all.cell(row=r_num, column=curr_col + 10, value=backlog_idx_formula).border = thin_border

        # Alignment
        for col_idx in range(1, total_all_cols + 1):
            if col_idx not in [2, 3]:
                ws_all.cell(row=r_num, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')

    # Apply Native Dynamic Conditional Formatting to 'All Students'
    if num_students > 0:
        for code, s_info in subject_col_map.items():
            c_res = s_info['res']
            c_int = s_info['int']
            c_tot = s_info['tot']

            # Subject RES formatting
            ws_all.conditional_formatting.add(
                f'{c_res}5:{c_res}{r_end}',
                CellIsRule(operator='equal', formula=['"F"'], fill=red_fill, font=Font(color='9C0006', bold=True))
            )
            ws_all.conditional_formatting.add(
                f'{c_res}5:{c_res}{r_end}',
                CellIsRule(operator='equal', formula=['"P"'], fill=green_fill, font=Font(color='006100', bold=True))
            )

            # Skyblue fill for failed subject mark columns INT, EXT, TOT
            ws_all.conditional_formatting.add(
                f'{c_int}5:{c_tot}{r_end}',
                FormulaRule(formula=[f'${c_res}5="F"'], fill=skyblue_fill)
            )

        # Student Overall RESULT formatting
        ws_all.conditional_formatting.add(
            f'{col_result}5:{col_result}{r_end}',
            CellIsRule(operator='equal', formula=['"Fail"'], fill=red_fill, font=Font(color='9C0006', bold=True))
        )
        ws_all.conditional_formatting.add(
            f'{col_result}5:{col_result}{r_end}',
            CellIsRule(operator='equal', formula=['"Pass"'], fill=green_fill, font=Font(color='006100', bold=True))
        )
        ws_all.conditional_formatting.add(
            f'{col_result}5:{col_result}{r_end}',
            CellIsRule(operator='equal', formula=['"Absent"'], fill=yellow_fill, font=Font(bold=True))
        )

    # Set column widths for All Students
    ws_all.column_dimensions['A'].width = 8
    ws_all.column_dimensions['B'].width = 16
    ws_all.column_dimensions['C'].width = 28
    for col_idx in range(4, curr_col + 6):
        c_letter = get_column_letter(col_idx)
        ws_all.column_dimensions[c_letter].width = 12

    # =========================================================================
    # --- SHEET 1: OVERALL RESULT ---
    # =========================================================================
    ws_sum = wb.create_sheet('Overall Result', 0)  # Make first sheet
    ws_sum.views.sheetView[0].showGridLines = True

    ws_sum.cell(row=1, column=1, value=profile.get('college_name', 'AcadFusion AI Enabled College')).font = Font(bold=True, size=14, color='581C87')
    ws_sum.cell(row=2, column=1, value=f"{report_settings.get('department', 'N/A')} - Semester {report_settings.get('semester','N/A')} ({report_settings.get('academic_year','N/A')})").font = Font(bold=True, size=11, color='475569')
    ws_sum.cell(row=3, column=1, value=f"VTU Result Analysis - {report_settings.get('examination', 'N/A')}").font = Font(bold=True, size=10, color='475569')

    ws_sum.cell(row=5, column=1, value="Report Metrics Summary").font = Font(bold=True, size=12, color='1E1B4B')

    overall_metrics = [
        ("Total Scraped Students", f"=COUNTA('All Students'!$B$5:$B${r_end})" if num_students > 0 else 0),
        ("Students Appeared", f'=COUNTIF(\'All Students\'!${col_result}$5:${col_result}${r_end}, "<>Absent")' if num_students > 0 else 0),
        ("Passed Students", f'=COUNTIF(\'All Students\'!${col_result}$5:${col_result}${r_end}, "Pass")' if num_students > 0 else 0),
        ("Failed Students", f'=COUNTIF(\'All Students\'!${col_result}$5:${col_result}${r_end}, "Fail")' if num_students > 0 else 0),
        ("Passing Percentage", f'=IF(B8>0, ROUND((B9/B8)*100, 2), 0)'),
        ("Average Semester SGPA", f'=ROUND(AVERAGEIF(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end}, ">0"), 2)' if num_students > 0 else 0),
        ("Highest SGPA", f'=MAX(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end})' if num_students > 0 else 0),
        ("Lowest SGPA", f'=MIN(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end})' if num_students > 0 else 0),
        ("Distinction Class Count", f'=COUNTIF(\'All Students\'!${col_class}$5:${col_class}${r_end}, "Distinction")' if num_students > 0 else 0),
        ("First Class Count", f'=COUNTIF(\'All Students\'!${col_class}$5:${col_class}${r_end}, "First Class")' if num_students > 0 else 0),
        ("Second Class Count", f'=COUNTIF(\'All Students\'!${col_class}$5:${col_class}${r_end}, "Second Class")' if num_students > 0 else 0),
        ("Pass Class Count", f'=COUNTIF(\'All Students\'!${col_class}$5:${col_class}${r_end}, "Pass Class")' if num_students > 0 else 0)
    ]

    for m_idx, (name, val) in enumerate(overall_metrics, start=7):
        ws_sum.cell(row=m_idx, column=1, value=name).font = Font(bold=True)
        ws_sum.cell(row=m_idx, column=2, value=val).alignment = Alignment(horizontal='center')
        ws_sum.cell(row=m_idx, column=1).border = thin_border
        ws_sum.cell(row=m_idx, column=2).border = thin_border

    ws_sum.column_dimensions['A'].width = 30
    ws_sum.column_dimensions['B'].width = 15

    # =========================================================================
    # --- SHEET 2: SUBJECT ANALYSIS ---
    # =========================================================================
    ws_sub = wb.create_sheet('Subject Analysis', 1)
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

    sub_row_idx = 4
    for code in all_subject_codes:
        meta = all_subjects_meta[code]
        sub_info = subject_col_map[code]
        c_tot = sub_info['tot']
        c_res = sub_info['res']

        ws_sub.cell(row=sub_row_idx, column=1, value=meta['name']).border = thin_border
        ws_sub.cell(row=sub_row_idx, column=2, value=code).border = thin_border
        ws_sub.cell(row=sub_row_idx, column=3, value=meta['credits']).border = thin_border
        ws_sub.cell(row=sub_row_idx, column=4, value=meta['faculty']).border = thin_border

        if num_students > 0:
            ws_sub.cell(row=sub_row_idx, column=5, value=f'=COUNTIFS(\'All Students\'!${c_tot}$5:${c_tot}${r_end}, ">=70", \'All Students\'!${c_res}$5:${c_res}${r_end}, "P")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=6, value=f'=COUNTIFS(\'All Students\'!${c_tot}$5:${c_tot}${r_end}, ">=60", \'All Students\'!${c_tot}$5:${c_tot}${r_end}, "<70", \'All Students\'!${c_res}$5:${c_res}${r_end}, "P")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=7, value=f'=COUNTIFS(\'All Students\'!${c_tot}$5:${c_tot}${r_end}, ">=35", \'All Students\'!${c_tot}$5:${c_tot}${r_end}, "<60", \'All Students\'!${c_res}$5:${c_res}${r_end}, "P")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=8, value=f'=COUNTIF(\'All Students\'!${c_res}$5:${c_res}${r_end}, "F")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=9, value=f'=COUNTIF(\'All Students\'!${c_res}$5:${c_res}${r_end}, "A")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=10, value=f'=COUNTIFS(\'All Students\'!${c_res}$5:${c_res}${r_end}, "<>-", \'All Students\'!${c_res}$5:${c_res}${r_end}, "<>A")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=11, value=f'=COUNTIF(\'All Students\'!${c_res}$5:${c_res}${r_end}, "P")').border = thin_border
            ws_sub.cell(row=sub_row_idx, column=12, value=f'=IF(J{sub_row_idx}>0, ROUND((K{sub_row_idx}/J{sub_row_idx})*100, 2), 0.0)').border = thin_border
        else:
            for c_pos in range(5, 13):
                ws_sub.cell(row=sub_row_idx, column=c_pos, value=0).border = thin_border

        for c_pos in range(2, 13):
            ws_sub.cell(row=sub_row_idx, column=c_pos).alignment = Alignment(horizontal='center', vertical='center')

        sub_row_idx += 1

    max_sub_row = sub_row_idx - 1
    if max_sub_row >= 4:
        ws_sub.conditional_formatting.add(
            f'L4:L{max_sub_row}',
            CellIsRule(operator='greaterThanOrEqual', formula=['80'], fill=green_fill, font=Font(color='006100', bold=True))
        )
        ws_sub.conditional_formatting.add(
            f'L4:L{max_sub_row}',
            CellIsRule(operator='lessThan', formula=['80'], fill=red_fill, font=Font(color='9C0006', bold=True))
        )

    for col in ws_sub.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_sub.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 35)

    # Add Chart to Sheet 1 referencing Sheet 2 Passing %
    if all_subject_codes and max_sub_row >= 4:
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

        data_ref = Reference(ws_sub, min_col=12, min_row=3, max_row=max_sub_row)
        cats_ref = Reference(ws_sub, min_col=2, min_row=4, max_row=max_sub_row)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        chart.legend = None

        if chart.series:
            chart.series[0].graphical_properties = GraphicalProperties(solidFill=ColorChoice(srgbClr="7C3AED"))

        ws_sum.add_chart(chart, "D7")

    # =========================================================================
    # --- SHEET 4: TOP 5 MARKS SECURED ---
    # =========================================================================
    ws_top_marks = wb.create_sheet('Top 5 Marks Secured')
    ws_top_marks.views.sheetView[0].showGridLines = True
    ws_top_marks.cell(row=1, column=1, value="Top 5 Students (Marks Secured)").font = Font(bold=True, size=14, color='1E1B4B')

    headers_top_marks = ["Rank", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "Result"]
    for c_idx, h in enumerate(headers_top_marks, 1):
        cell = ws_top_marks.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    rank_fills = [gold_fill, silver_fill, bronze_fill, rank4_fill, rank5_fill]

    for rank in range(1, 6):
        r_idx = rank + 3
        ws_top_marks.cell(row=r_idx, column=1, value=rank).border = thin_border

        if num_students > 0:
            ws_top_marks.cell(row=r_idx, column=2, value=f'=IFERROR(INDEX(\'All Students\'!$B$5:$B${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_marks}$5:${col_rank_marks}${r_end}, 0)), "-")').border = thin_border
            ws_top_marks.cell(row=r_idx, column=3, value=f'=IFERROR(INDEX(\'All Students\'!$C$5:$C${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_marks}$5:${col_rank_marks}${r_end}, 0)), "-")').border = thin_border
            ws_top_marks.cell(row=r_idx, column=4, value=max_marks_per_student).border = thin_border
            ws_top_marks.cell(row=r_idx, column=5, value=f'=IFERROR(INDEX(\'All Students\'!${col_grand_total}$5:${col_grand_total}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_marks}$5:${col_rank_marks}${r_end}, 0)), "-")').border = thin_border
            ws_top_marks.cell(row=r_idx, column=6, value=f'=IFERROR(INDEX(\'All Students\'!${col_percentage}$5:${col_percentage}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_marks}$5:${col_rank_marks}${r_end}, 0)), "-")').border = thin_border
            ws_top_marks.cell(row=r_idx, column=7, value=f'=IFERROR(INDEX(\'All Students\'!${col_result}$5:${col_result}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_marks}$5:${col_rank_marks}${r_end}, 0)), "-")').border = thin_border
        else:
            for c in range(2, 8):
                ws_top_marks.cell(row=r_idx, column=c, value='-').border = thin_border

        for col_c in range(1, 8):
            ws_top_marks.cell(row=r_idx, column=col_c).fill = rank_fills[rank - 1]
            if col_c != 3:
                ws_top_marks.cell(row=r_idx, column=col_c).alignment = Alignment(horizontal='center', vertical='center')

    for col in ws_top_marks.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_top_marks.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # =========================================================================
    # --- SHEET 5: TOP 5 SGPA ---
    # =========================================================================
    ws_top_sgpa = wb.create_sheet('Top 5 SGPA')
    ws_top_sgpa.views.sheetView[0].showGridLines = True
    ws_top_sgpa.cell(row=1, column=1, value="Top 5 Students (SGPA)").font = Font(bold=True, size=14, color='1E1B4B')

    headers_top_sgpa = ["Rank", "USN", "Student Name", "SGPA", "Total Marks", "Marks Secured", "Result"]
    for c_idx, h in enumerate(headers_top_sgpa, 1):
        cell = ws_top_sgpa.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    for rank in range(1, 6):
        r_idx = rank + 3
        ws_top_sgpa.cell(row=r_idx, column=1, value=rank).border = thin_border

        if num_students > 0:
            ws_top_sgpa.cell(row=r_idx, column=2, value=f'=IFERROR(INDEX(\'All Students\'!$B$5:$B${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_sgpa}$5:${col_rank_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_top_sgpa.cell(row=r_idx, column=3, value=f'=IFERROR(INDEX(\'All Students\'!$C$5:$C${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_sgpa}$5:${col_rank_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_top_sgpa.cell(row=r_idx, column=4, value=f'=IFERROR(INDEX(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_sgpa}$5:${col_rank_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_top_sgpa.cell(row=r_idx, column=5, value=max_marks_per_student).border = thin_border
            ws_top_sgpa.cell(row=r_idx, column=6, value=f'=IFERROR(INDEX(\'All Students\'!${col_grand_total}$5:${col_grand_total}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_sgpa}$5:${col_rank_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_top_sgpa.cell(row=r_idx, column=7, value=f'=IFERROR(INDEX(\'All Students\'!${col_result}$5:${col_result}${r_end}, MATCH(A{r_idx}, \'All Students\'!${col_rank_sgpa}$5:${col_rank_sgpa}${r_end}, 0)), "-")').border = thin_border
        else:
            for c in range(2, 8):
                ws_top_sgpa.cell(row=r_idx, column=c, value='-').border = thin_border

        for col_c in range(1, 8):
            ws_top_sgpa.cell(row=r_idx, column=col_c).fill = rank_fills[rank - 1]
            if col_c != 3:
                ws_top_sgpa.cell(row=r_idx, column=col_c).alignment = Alignment(horizontal='center', vertical='center')

    for col in ws_top_sgpa.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_top_sgpa.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # =========================================================================
    # --- SHEET 6: STUDENTS WITH BACKLOGS ---
    # Dynamic Auto-Filtering: Students with backlogs appear sequentially
    # When a student passes, they automatically vanish, shifting remaining rows up
    # =========================================================================
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

    max_bl_rows = max(num_students, 1)
    for k in range(1, max_bl_rows + 1):
        bl_row = k + 3
        if num_students > 0:
            ws_backlogs.cell(row=bl_row, column=1, value=f'=IF(ISNUMBER(MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), {k}, "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=2, value=f'=IFERROR(INDEX(\'All Students\'!$B$5:$B${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=3, value=f'=IFERROR(INDEX(\'All Students\'!$C$5:$C${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=4, value=f'=IF(B{bl_row}="", "", {max_marks_per_student})').border = thin_border
            ws_backlogs.cell(row=bl_row, column=5, value=f'=IFERROR(INDEX(\'All Students\'!${col_grand_total}$5:${col_grand_total}${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=6, value=f'=IFERROR(INDEX(\'All Students\'!${col_percentage}$5:${col_percentage}${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=7, value=f'=IFERROR(INDEX(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=8, value=f'=IFERROR(INDEX(\'All Students\'!${col_backlogs}$5:${col_backlogs}${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
            ws_backlogs.cell(row=bl_row, column=9, value=f'=IFERROR(INDEX(\'All Students\'!${col_result}$5:${col_result}${r_end}, MATCH({k}, \'All Students\'!${col_backlog_idx}$5:${col_backlog_idx}${r_end}, 0)), "")').border = thin_border
        else:
            for c in range(1, 10):
                ws_backlogs.cell(row=bl_row, column=c, value='').border = thin_border

        for c in range(1, 10):
            if c != 3:
                ws_backlogs.cell(row=bl_row, column=c).alignment = Alignment(horizontal='center', vertical='center')

    # Dynamic Conditional Formatting on Backlogs: Highlight only non-empty backlog rows in yellow
    end_bl_row = 3 + max_bl_rows
    ws_backlogs.conditional_formatting.add(
        f'A4:I{end_bl_row}',
        FormulaRule(formula=['$B4<>""'], fill=yellow_fill)
    )

    for col in ws_backlogs.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_backlogs.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # =========================================================================
    # --- SHEET 7: STUDENT RANKINGS (MARKS) ---
    # =========================================================================
    ws_rank_marks = wb.create_sheet('Student Rankings (Marks)')
    ws_rank_marks.views.sheetView[0].showGridLines = True
    ws_rank_marks.cell(row=1, column=1, value="Student Rankings (by Marks Secured)").font = Font(bold=True, size=14, color='1E1B4B')

    headers_rank_marks = ["Rank", "USN", "Student Name", "Total Marks", "Marks Secured", "Percentage", "Result"]
    for c_idx, h in enumerate(headers_rank_marks, 1):
        cell = ws_rank_marks.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    for k in range(1, max_bl_rows + 1):
        rm_row = k + 3
        ws_rank_marks.cell(row=rm_row, column=1, value=k).border = thin_border

        if num_students > 0:
            ws_rank_marks.cell(row=rm_row, column=2, value=f'=IFERROR(INDEX(\'All Students\'!$B$5:$B${r_end}, MATCH(A{rm_row}, \'All Students\'!${col_rank_all_marks}$5:${col_rank_all_marks}${r_end}, 0)), "-")').border = thin_border
            ws_rank_marks.cell(row=rm_row, column=3, value=f'=IFERROR(INDEX(\'All Students\'!$C$5:$C${r_end}, MATCH(A{rm_row}, \'All Students\'!${col_rank_all_marks}$5:${col_rank_all_marks}${r_end}, 0)), "-")').border = thin_border
            ws_rank_marks.cell(row=rm_row, column=4, value=max_marks_per_student).border = thin_border
            ws_rank_marks.cell(row=rm_row, column=5, value=f'=IFERROR(INDEX(\'All Students\'!${col_grand_total}$5:${col_grand_total}${r_end}, MATCH(A{rm_row}, \'All Students\'!${col_rank_all_marks}$5:${col_rank_all_marks}${r_end}, 0)), "-")').border = thin_border
            ws_rank_marks.cell(row=rm_row, column=6, value=f'=IFERROR(INDEX(\'All Students\'!${col_percentage}$5:${col_percentage}${r_end}, MATCH(A{rm_row}, \'All Students\'!${col_rank_all_marks}$5:${col_rank_all_marks}${r_end}, 0)), "-")').border = thin_border
            ws_rank_marks.cell(row=rm_row, column=7, value=f'=IFERROR(INDEX(\'All Students\'!${col_result}$5:${col_result}${r_end}, MATCH(A{rm_row}, \'All Students\'!${col_rank_all_marks}$5:${col_rank_all_marks}${r_end}, 0)), "-")').border = thin_border
        else:
            for c in range(2, 8):
                ws_rank_marks.cell(row=rm_row, column=c, value='-').border = thin_border

        if k <= 5:
            for col_c in range(1, 8):
                ws_rank_marks.cell(row=rm_row, column=col_c).fill = rank_fills[k - 1]

        for col_c in range(1, 8):
            if col_c != 3:
                ws_rank_marks.cell(row=rm_row, column=col_c).alignment = Alignment(horizontal='center', vertical='center')

    for col in ws_rank_marks.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_rank_marks.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # =========================================================================
    # --- SHEET 8: STUDENT RANKINGS (SGPA) ---
    # =========================================================================
    ws_rank_sgpa = wb.create_sheet('Student Rankings (SGPA)')
    ws_rank_sgpa.views.sheetView[0].showGridLines = True
    ws_rank_sgpa.cell(row=1, column=1, value="Student Rankings (by SGPA)").font = Font(bold=True, size=14, color='1E1B4B')

    headers_rank_sgpa = ["Rank", "USN", "Student Name", "SGPA", "Total Marks", "Marks Secured", "Result"]
    for c_idx, h in enumerate(headers_rank_sgpa, 1):
        cell = ws_rank_sgpa.cell(row=3, column=c_idx, value=h)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = thin_border

    for k in range(1, max_bl_rows + 1):
        rs_row = k + 3
        ws_rank_sgpa.cell(row=rs_row, column=1, value=k).border = thin_border

        if num_students > 0:
            ws_rank_sgpa.cell(row=rs_row, column=2, value=f'=IFERROR(INDEX(\'All Students\'!$B$5:$B${r_end}, MATCH(A{rs_row}, \'All Students\'!${col_rank_all_sgpa}$5:${col_rank_all_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_rank_sgpa.cell(row=rs_row, column=3, value=f'=IFERROR(INDEX(\'All Students\'!$C$5:$C${r_end}, MATCH(A{rs_row}, \'All Students\'!${col_rank_all_sgpa}$5:${col_rank_all_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_rank_sgpa.cell(row=rs_row, column=4, value=f'=IFERROR(INDEX(\'All Students\'!${col_sgpa}$5:${col_sgpa}${r_end}, MATCH(A{rs_row}, \'All Students\'!${col_rank_all_sgpa}$5:${col_rank_all_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_rank_sgpa.cell(row=rs_row, column=5, value=max_marks_per_student).border = thin_border
            ws_rank_sgpa.cell(row=rs_row, column=6, value=f'=IFERROR(INDEX(\'All Students\'!${col_grand_total}$5:${col_grand_total}${r_end}, MATCH(A{rs_row}, \'All Students\'!${col_rank_all_sgpa}$5:${col_rank_all_sgpa}${r_end}, 0)), "-")').border = thin_border
            ws_rank_sgpa.cell(row=rs_row, column=7, value=f'=IFERROR(INDEX(\'All Students\'!${col_result}$5:${col_result}${r_end}, MATCH(A{rs_row}, \'All Students\'!${col_rank_all_sgpa}$5:${col_rank_all_sgpa}${r_end}, 0)), "-")').border = thin_border
        else:
            for c in range(2, 8):
                ws_rank_sgpa.cell(row=rs_row, column=c, value='-').border = thin_border

        if k <= 5:
            for col_c in range(1, 8):
                ws_rank_sgpa.cell(row=rs_row, column=col_c).fill = rank_fills[k - 1]

        for col_c in range(1, 8):
            if col_c != 3:
                ws_rank_sgpa.cell(row=rs_row, column=col_c).alignment = Alignment(horizontal='center', vertical='center')

    for col in ws_rank_sgpa.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_rank_sgpa.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # Save to buffer
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def generate_csv_report(results, report_settings=None, user_id=None):
    """
    Generates a flattened CSV data report containing all student subject scores
    mapped to faculty and credit counts with report settings metadata.
    """
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
