import os
import threading
import uuid
import re
import logging
import time
from flask import Blueprint, render_template, request, jsonify, send_file, session, redirect, url_for
import requests
from scraper import initialize_scrape, complete_scrape, get_headers
from processor import generate_excel_report
from models.database import db_instance
from blueprints.auth import is_admin

_logger = logging.getLogger(__name__)

analyzer_bp = Blueprint('analyzer', __name__)

# JOBS store - in production this would be Redis/DB
JOBS = {}

# Internal-only subject name keywords (case-insensitive)
_INTERNAL_ONLY_NAME_KEYWORDS = [
    'yoga', 'environmental', 'internship', 'mini project', 'constitution',
    'science and society', 'professional ethics', 'human rights',
    'audit course', 'nss', 'ncc', 'sports',
]

# Known internal-only subject_type values stored in Institution Hub
_INTERNAL_ONLY_TYPES = {
    'internal only', 'internal assessment', 'ia only', 'internal',
    'non-credit', 'audit'
}


def _is_internal_only_subject(sub_data, matched_sub):
    """
    Determines whether a subject is internal-assessment only (no external exam).

    Detection order:
      1. Runtime flag set by scraper.parse_vtu_html (most reliable for live data).
      2. Institution Hub 'subject_type' field.
      3. Institution Hub 'max_external_marks' == 0.
      4. Heuristic: external marks == 0 AND internal marks > 0 AND site result was P.
      5. Subject name keyword match.
    """
    # 1. Scraper-set flag (live data)
    if sub_data.get('is_internal_only') is True:
        return True

    if matched_sub:
        # 2. Institution Hub subject_type
        sub_type = str(matched_sub.get('subject_type', '')).strip().lower()
        if sub_type in _INTERNAL_ONLY_TYPES:
            return True

        # 3. Institution Hub max_external_marks == 0
        max_ext = matched_sub.get('max_external_marks')
        if max_ext is not None:
            try:
                if int(max_ext) == 0:
                    return True
            except (ValueError, TypeError):
                pass

    # 4. Runtime heuristic: external==0, internal>0 (scraper flag already covers this
    #    for live data; this path catches mock data or stored results without the flag)
    ext = sub_data.get('external', -1)
    int_ = sub_data.get('internal', 0)
    res = str(sub_data.get('result', '')).strip().upper()
    if ext == 0 and int_ > 0 and res in ['P', 'PASS']:
        return True

    # 5. Subject name keyword match
    sub_name = str(sub_data.get('name', '') or '').lower()
    if any(kw in sub_name for kw in _INTERNAL_ONLY_NAME_KEYWORDS):
        return True

    return False


def calculate_sgpa_and_map_faculty(student_result, subjects_db):
    """
    Maps Institution Hub configuration (credits, faculty, subject_type) onto each
    subject in student_result, then calculates SGPA and percentage.

    Institution Hub is the single source of truth:
      - Credits are read from Institution Hub. A default of 4 is ONLY used when
        Institution Hub genuinely has no record for the subject.
      - Faculty is read from Institution Hub. 'Unassigned' is only used when
        Institution Hub has no record.
      - Internal-only subjects skip the external-marks fail rule.

    Matching order (per subject):
      1. Exact subject_code match.
      2. Wildcard code match (e.g. B**601).
      3. Prefix+suffix wildcard fallback.
      4. Subject name fuzzy match (normalized lowercase comparison).
    """
    if not student_result or 'subjects' not in student_result:
        return
        
    total_grade_points = 0
    total_credits = 0
    
    # Build fast lookup structures from subjects_db
    exact_map = {}          # subject_code -> subject doc
    wildcard_subjects = []  # [(code_with_**, doc), ...]
    name_map = {}           # normalized_subject_name -> subject doc

    for db_sub in subjects_db:
        db_code = db_sub.get('subject_code', '').strip().upper()
        if '**' in db_code:
            wildcard_subjects.append((db_code, db_sub))
        elif db_code:
            exact_map[db_code] = db_sub

        # Build name index for fallback matching
        db_name = db_sub.get('subject_name', '').strip().lower()
        if db_name:
            name_map[db_name] = db_sub
    
    for sub_code, sub_data in list(student_result['subjects'].items()):
        norm_code = sub_code.strip().upper()
        matched_sub = None
        match_method = None
        
        # --- Match 1: Exact code ---
        if norm_code in exact_map:
            matched_sub = exact_map[norm_code]
            match_method = 'exact_code'
        
        # --- Match 2: Wildcard code (e.g. B**601 matches BCS601 or BIS601) ---
        if not matched_sub:
            for db_code, db_sub in wildcard_subjects:
                escaped = re.escape(db_code)
                pattern = '^' + escaped.replace('\\*\\*', '[A-Z0-9]{2}') + '$'
                if re.match(pattern, norm_code):
                    matched_sub = db_sub
                    match_method = 'wildcard_code'
                    break
        
        # --- Match 3: Prefix+suffix wildcard fallback ---
        if not matched_sub:
            for db_code, db_sub in wildcard_subjects:
                if len(db_code) == len(norm_code):
                    star_pos = db_code.index('*')
                    star_end = db_code.rindex('*') + 1
                    if (norm_code[:star_pos] == db_code[:star_pos] and
                            norm_code[star_end:] == db_code[star_end:]):
                        matched_sub = db_sub
                        match_method = 'wildcard_prefix_suffix'
                        break

        # --- Match 4: Subject name fallback ---
        if not matched_sub and name_map:
            scraper_name = str(sub_data.get('name', '') or '').strip().lower()
            if scraper_name and scraper_name in name_map:
                matched_sub = name_map[scraper_name]
                match_method = 'name_exact'
            else:
                # Partial name match: check if scraper name is a substring of any db name
                for db_name, db_sub in name_map.items():
                    if scraper_name and (scraper_name in db_name or db_name in scraper_name):
                        matched_sub = db_sub
                        match_method = 'name_partial'
                        break

        # --- Resolve credits and faculty from matched_sub ---
        if matched_sub:
            # Override subject name with Institution Hub canonical name
            if matched_sub.get('subject_name'):
                sub_data['name'] = matched_sub['subject_name']

            # Credits: read from Institution Hub; warn if missing
            raw_credits = matched_sub.get('credits')
            if raw_credits is not None:
                try:
                    credits = int(raw_credits)
                    if credits <= 0:
                        _logger.warning(
                            f"Subject '{norm_code}' (match: {match_method}) has credits={credits} "
                            f"in Institution Hub. Using 0 credits."
                        )
                except (ValueError, TypeError):
                    _logger.warning(
                        f"Subject '{norm_code}' (match: {match_method}) has non-numeric credits='{raw_credits}' "
                        f"in Institution Hub. Defaulting to 4."
                    )
                    credits = 4
            else:
                _logger.warning(
                    f"Subject '{norm_code}' (match: {match_method}): Institution Hub record has no "
                    f"'credits' field. Defaulting to 4. Fix the Institution Hub configuration."
                )
                credits = 4

            # Faculty: read from Institution Hub; warn if missing
            faculty = matched_sub.get('faculty_name', '') or ''
            if not faculty.strip():
                _logger.warning(
                    f"Subject '{norm_code}' (match: {match_method}): Institution Hub record has no "
                    f"'faculty_name'. Set the faculty in Institution Hub Settings."
                )
                faculty = 'Unassigned'

            # Store subject_type for downstream consumers
            sub_data['subject_type'] = matched_sub.get('subject_type', '')

        else:
            # Truly no Institution Hub configuration for this subject
            _logger.warning(
                f"No Institution Hub match for subject code '{norm_code}' "
                f"(name='{sub_data.get('name', '')}') "
                f"after exhausting exact, wildcard, and name lookups. "
                f"Defaulting to credits=4 and faculty='Unassigned'. "
                f"Please add this subject to Institution Hub Settings."
            )
            credits = 4
            faculty = 'Unassigned'
                
        sub_data['credits'] = credits
        sub_data['faculty'] = faculty
        
        # --- Internal-only detection ---
        is_internal_only = _is_internal_only_subject(sub_data, matched_sub)
        sub_data['is_internal_only'] = is_internal_only

        if is_internal_only:
            _logger.debug(
                f"Subject '{norm_code}' classified as internal-only. "
                f"Skipping external fail check."
            )
        
        # --- VTU pass/fail logic ---
        res = str(sub_data.get('result', '')).strip().upper()
        total_marks = sub_data.get('total', 0)
        internal = sub_data.get('internal', 0)
        external = sub_data.get('external', 0)
        
        try:
            total_marks = int(total_marks)
            internal = int(internal)
            external = int(external)
        except (ValueError, TypeError):
            total_marks = 0
            internal = 0
            external = 0
        
        if is_internal_only:
            # Internal-only subjects: pass if total >= 40 (no external threshold)
            is_pass = (
                res not in ['F', 'A', 'ABSENT', 'FAIL']
                and total_marks >= 40
            )
        else:
            # Standard VTU subjects: total >= 40 AND external >= 18
            is_pass = True
            if res in ['F', 'A', 'ABSENT', 'FAIL']:
                is_pass = False
            elif total_marks < 40:
                is_pass = False
            elif external < 18:
                # external < 18 is fail regardless of internal
                is_pass = False
            
        if not is_pass:
            gp = 0
            sub_data['result'] = 'F'
        else:
            if total_marks >= 90: gp = 10
            elif total_marks >= 80: gp = 9
            elif total_marks >= 70: gp = 8
            elif total_marks >= 60: gp = 7
            elif total_marks >= 50: gp = 6
            elif total_marks >= 45: gp = 5
            elif total_marks >= 40: gp = 4
            else: gp = 0
            sub_data['result'] = 'P'
            
        total_grade_points += gp * credits
        total_credits += credits
        sub_data['grade_point'] = gp
        
    calculated_sgpa = 0.0
    if total_credits > 0:
        calculated_sgpa = round(total_grade_points / total_credits, 2)
        
    student_result['sgpa'] = calculated_sgpa
    
    # Calculate percentage (marks obtained / max possible marks * 100)
    subjects = student_result.get('subjects', {})
    if subjects:
        total_obtained = sum(s.get('total', 0) for s in subjects.values())
        # Max marks: internal-only subjects count full 100, regular subjects count 100
        max_possible = len(subjects) * 100
        student_result['percentage'] = round((total_obtained / max_possible) * 100, 2) if max_possible > 0 else 0
    else:
        student_result['percentage'] = 0


def background_scraper(job_id, usn_list, user_id, is_mock=None, report_settings=None):
    total_count = len(usn_list)
    result_type = report_settings.get('result_type', 'regular') if report_settings else 'regular'
    vtu_url = report_settings.get('vtu_result_link', '') if report_settings else ''
    
    # Pre-initialize results map preserving original uploaded order (1-to-1 symmetry)
    results_by_usn = {}
    for pos, u in enumerate(usn_list):
        results_by_usn[u] = {
            "usn": u,
            "name": "N/A",
            "status": "Pending",
            "uploaded_position": pos + 1,
            "total_marks": 0,
            "max_marks": 0,
            "sgpa": 0.0,
            "percentage": 0.0,
            "subjects": {}
        }

    JOBS[job_id].update({
        'total': total_count,
        'completed': 0,
        'results': [results_by_usn[u] for u in usn_list], # Initialized in exact uploaded order
        'status': 'Running',
        'current_usn': '',
        'report_settings': report_settings
    })
    
    # Fetch mapped subjects from database to assist with SGPA & Mock generation
    subjects_db = []
    if report_settings:
        subjects_db = db_instance.get_subjects(user_id, {
            'academic_year': report_settings.get('academic_year'),
            'scheme': report_settings.get('scheme'),
            'semester': report_settings.get('semester'),
            'department': report_settings.get('department')
        })
        
    # Compile mock subjects list if in mock mode
    mock_subjects_dict = {}
    for s in subjects_db:
        code = s.get('subject_code', '').upper()
        # Handle 22 scheme wildcard subject mapping (e.g. B**601 -> BCS601 / BIS601)
        if '**' in code:
            dept_code = 'CS'
            dept_name = report_settings.get('department', '').upper()
            if 'INFORMATION' in dept_name or 'ISE' in dept_name:
                dept_code = 'IS'
            elif 'ELECTRONICS' in dept_name or 'ECE' in dept_name:
                dept_code = 'EC'
            elif 'ELECTRICAL' in dept_name or 'EEE' in dept_name:
                dept_code = 'EE'
            elif 'MECHANICAL' in dept_name or 'ME' in dept_name:
                dept_code = 'ME'
            elif 'CIVIL' in dept_name or 'CV' in dept_name:
                dept_code = 'CV'
            code = code.replace('**', dept_code)
        mock_subjects_dict[code] = s.get('subject_name', 'Subject')

    # Persistent Session for the entire job
    job_session = requests.Session()
    job_session.verify = False
    job_session.headers.update(get_headers())
    
    MAX_RETRIES = 3            # Max inline retries per USN before recording an error row
    CAPTCHA_WAIT_TIMEOUT = 300  # Seconds to wait for human CAPTCHA input (5 minutes)

    # Process every USN strictly in uploaded order — no tail-appending, no skipping
    for seq_idx, usn in enumerate(usn_list):
        uploaded_pos = seq_idx + 1
        output_row  = uploaded_pos + 4  # Sheet 3 data rows start at row 5
        JOBS[job_id]['current_usn'] = usn

        _logger.info(
            f"\n================================================\n"
            f"[QUEUE] Processing USN {uploaded_pos}/{total_count}: {usn}\n"
            f"================================================"
        )

        final_status   = None   # will be set once we produce a definitive result
        attempt        = 0

        # ── Inner retry loop for THIS USN only ──────────────────────────────
        # Retries are exhausted before moving to the next USN.
        # The outer for-loop NEVER advances while this USN is still pending.
        while attempt < MAX_RETRIES and final_status is None:
            attempt += 1

            # Reset captcha / session state for every attempt
            JOBS[job_id]['captcha_solved'] = False
            JOBS[job_id]['captcha_text']   = None
            JOBS[job_id]['captcha_base64'] = None
            JOBS[job_id]['current_session'] = None
            JOBS[job_id]['token_dict']      = None

            _logger.info(f"[{usn}] Attempt {attempt}/{MAX_RETRIES}")

            # ── Step 1: Initialize scrape session ────────────────────────────
            try:
                JOBS[job_id]['status'] = f'Initializing {usn} (attempt {attempt})...'
                req_session, b64_captcha, token_dict, err = initialize_scrape(
                    usn, mock=is_mock, session=job_session, vtu_url=vtu_url
                )
            except Exception as init_exc:
                _logger.warning(f"[{usn}] initialize_scrape raised: {init_exc}")
                req_session = None
                b64_captcha = None
                token_dict  = None
                err         = str(init_exc)

            if err or not req_session or not b64_captcha:
                _logger.warning(
                    f"[{usn}] Init failed (attempt {attempt}/{MAX_RETRIES}): {err}"
                )
                # Refresh session for next attempt
                try:
                    job_session = requests.Session()
                    job_session.verify = False
                    job_session.headers.update(get_headers())
                except Exception:
                    pass
                if attempt < MAX_RETRIES:
                    time.sleep(2)
                    continue   # retry THIS USN — does NOT advance to next USN
                else:
                    final_status = f"Init Error: {err or 'No session/captcha returned'}"
                    break

            JOBS[job_id]['captcha_base64']   = b64_captcha
            JOBS[job_id]['current_session']  = req_session
            JOBS[job_id]['token_dict']       = token_dict

            # ── Step 2: Wait for CAPTCHA resolution ──────────────────────────
            from scraper import VTU_MOCK_MODE
            effective_mock = is_mock if is_mock is not None else VTU_MOCK_MODE
            if effective_mock:
                JOBS[job_id]['captcha_solved'] = True
                JOBS[job_id]['captcha_text']   = 'SIM'
            else:
                # ── CAPTCHA State Machine: WAITING ──────────────────────────
                # Generate a unique token for this exact captcha session.
                # The frontend MUST echo this token back on submission.
                # This is the only way to validate that a submission belongs
                # to the correct USN + attempt, preventing any stale submissions.
                import uuid as _uuid
                captcha_token = str(_uuid.uuid4())
                JOBS[job_id]['captcha_token']  = captcha_token
                JOBS[job_id]['queue_locked']   = True   # Lock: queue MUST NOT advance
                JOBS[job_id]['captcha_state']  = 'WAITING'
                JOBS[job_id]['status']         = 'Waiting for Captcha'
                _logger.info(
                    f"[{usn}] CAPTCHA WAITING — token={captcha_token[:8]}… "
                    f"(attempt {attempt}/{MAX_RETRIES}, timeout={CAPTCHA_WAIT_TIMEOUT}s)"
                )

            # ── CAPTCHA wait loop — blocks until solved or timeout ──────────
            wait_ticks = 0
            while not JOBS[job_id].get('captcha_solved'):
                time.sleep(1)
                wait_ticks += 1
                if wait_ticks > CAPTCHA_WAIT_TIMEOUT:
                    break

            # ── Release captcha lock regardless of outcome ──────────────────
            JOBS[job_id]['queue_locked']  = False
            JOBS[job_id]['captcha_state'] = 'DONE'
            JOBS[job_id]['captcha_token'] = None  # Invalidate token immediately

            if not JOBS[job_id].get('captcha_solved'):
                _logger.warning(
                    f"[{usn}] Captcha not solved within {CAPTCHA_WAIT_TIMEOUT}s "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                    continue   # retry THIS USN — refresh session and new captcha image
                else:
                    final_status = "Captcha Timeout"
                    break

            # ── Step 3: Perform the actual scrape ────────────────────────────
            try:
                JOBS[job_id]['status'] = f'Scraping {usn} (attempt {attempt})...'
                res_dict = complete_scrape(
                    usn, req_session, token_dict,
                    JOBS[job_id]['captcha_text'],
                    mock=is_mock,
                    mock_subjects=mock_subjects_dict if mock_subjects_dict else None,
                    result_type=result_type,
                    vtu_url=vtu_url
                )
            except Exception as scrape_exc:
                _logger.warning(f"[{usn}] complete_scrape raised: {scrape_exc}")
                if attempt < MAX_RETRIES:
                    try:
                        job_session = requests.Session()
                        job_session.verify = False
                        job_session.headers.update(get_headers())
                    except Exception:
                        pass
                    time.sleep(2)
                    continue   # retry THIS USN
                else:
                    final_status = f"Processing Error: {str(scrape_exc)[:120]}"
                    break

            status_lbl = res_dict.get('status', 'Error')

            # Retriable server-side errors → retry the SAME USN
            RETRIABLE_STATUSES = {
                'Invalid Captcha', 'Busy/Redirect',
                'VTU Timeout', 'Network/Parse Error', 'Direct Access Error'
            }
            if status_lbl in RETRIABLE_STATUSES:
                _logger.warning(
                    f"[{usn}] Retriable status '{status_lbl}' "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                try:
                    job_session = requests.Session()
                    job_session.verify = False
                    job_session.headers.update(get_headers())
                except Exception:
                    pass
                if attempt < MAX_RETRIES:
                    time.sleep(2)
                    continue   # retry THIS USN — does NOT advance to next USN
                else:
                    final_status = status_lbl
                    break

            # ── Step 4: Definitive result obtained ───────────────────────────
            if status_lbl in ('Pass', 'Fail'):
                try:
                    calculate_sgpa_and_map_faculty(res_dict, subjects_db)
                except Exception as sgpa_exc:
                    _logger.warning(f"[{usn}] SGPA mapping error: {sgpa_exc}")

            res_dict['uploaded_position'] = uploaded_pos
            results_by_usn[usn] = res_dict

            JOBS[job_id]['completed'] = seq_idx + 1
            JOBS[job_id]['results']   = [results_by_usn[u] for u in usn_list]

            _logger.info(
                f"\n---------------------------------\n"
                f"Uploaded Position : {uploaded_pos}/{total_count}\n"
                f"Current USN       : {usn}\n"
                f"Status            : {status_lbl}\n"
                f"Attempts          : {attempt}\n"
                f"Output Row Number : {output_row}\n"
                f"---------------------------------"
            )
            time.sleep(1)
            final_status = status_lbl  # exits inner while-loop
        # ── End of inner retry loop ──────────────────────────────────────────

        # If all retries exhausted without a definitive result, record error row
        if final_status is None or (
            final_status not in ('Pass', 'Fail')
            and final_status not in results_by_usn[usn].get('status', '')
        ):
            # Only update if still at the Pending placeholder
            if results_by_usn[usn].get('status') == 'Pending':
                err_status = final_status or f"Max Retries Exhausted after {MAX_RETRIES} attempts"
                results_by_usn[usn].update({
                    'usn': usn,
                    'status': err_status,
                    'uploaded_position': uploaded_pos
                })
                _logger.error(
                    f"\n---------------------------------\n"
                    f"Uploaded Position : {uploaded_pos}/{total_count}\n"
                    f"Current USN       : {usn}\n"
                    f"Status            : {err_status} (Error Row Written)\n"
                    f"Attempts          : {attempt}\n"
                    f"Output Row Number : {output_row}\n"
                    f"---------------------------------"
                )
                JOBS[job_id]['completed'] = seq_idx + 1
                JOBS[job_id]['results']   = [results_by_usn[u] for u in usn_list]
        
    # Final verification and Summary Logging
    final_results = [results_by_usn[u] for u in usn_list]
    JOBS[job_id]['results'] = final_results
    
    successful_cnt = sum(1 for r in final_results if r.get('status') == 'Pass')
    failed_cnt = sum(1 for r in final_results if r.get('status') == 'Fail')
    invalid_cnt = sum(1 for r in final_results if 'Invalid' in str(r.get('status')) or 'No Res' in str(r.get('status')))
    timeout_cnt = sum(1 for r in final_results if 'Timeout' in str(r.get('status')))
    network_cnt = sum(1 for r in final_results if 'Network' in str(r.get('status')) or 'Init Error' in str(r.get('status')) or 'Busy' in str(r.get('status')))
    
    missing_usns = [u for u in usn_list if u not in results_by_usn]
    seen_u = set()
    dup_usns = [r['usn'] for r in final_results if r['usn'] in seen_u or seen_u.add(r['usn'])]
    
    if missing_usns:
        _logger.error(f"[Job {job_id}] CRITICAL: {len(missing_usns)} USNs disappeared! Missing USNs: {missing_usns}")
    
    _logger.info(
        f"\n============================================================\n"
        f"JOB SUMMARY REPORT - Job ID: {job_id}\n"
        f"============================================================\n"
        f"Uploaded USNs: {len(usn_list)}\n"
        f"Processed USNs: {len(final_results)}\n"
        f"Successful: {successful_cnt}\n"
        f"Failed: {failed_cnt}\n"
        f"Invalid: {invalid_cnt}\n"
        f"Timeout: {timeout_cnt}\n"
        f"Network Errors: {network_cnt}\n"
        f"Excel Rows Generated: {len(final_results)}\n"
        f"Missing USNs: {len(missing_usns)} {missing_usns if missing_usns else 'None'}\n"
        f"Duplicate USNs: {len(dup_usns)} {dup_usns if dup_usns else 'None'}\n"
        f"============================================================"
    )
        
    JOBS[job_id]['status'] = 'Processing Excel'
    try:
        excel_data = generate_excel_report(JOBS[job_id]['results'], report_settings, user_id)
        JOBS[job_id]['excel_file'] = excel_data.getvalue()
        JOBS[job_id]['status'] = 'Completed'
        
        # Save to MongoDB for persistence
        db_instance.save_analysis_job(job_id, usn_list, JOBS[job_id]['results'], user_id, report_settings)
        
    except Exception as e:
        JOBS[job_id]['status'] = f'Error during Excel generation: {str(e)}'

def expand_usn_range(start_usn, count):
    """Expands a starting USN into a sequential list of `count` USNs."""
    usn_list = []
    # Match any VTU USN format: digit + college_code + year + dept + serial
    # e.g. 2BL23IS001, 1AJ22CS100
    match = re.match(r"^([1-9][A-Z0-9]{2}\d{2}[A-Z]{2})(\d{3})$", start_usn.strip().upper())
    if not match:
        return [start_usn.strip().upper()]
    prefix = match.group(1)
    start_num = int(match.group(2))
    for i in range(count):
        usn_list.append(f"{prefix}{(start_num + i):03d}")
    return usn_list

@analyzer_bp.route('/analyzer')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    from scraper import VTU_MOCK_MODE
    current_mode = session.get('use_mock')
    if current_mode is None: current_mode = VTU_MOCK_MODE
    
    return render_template('analyzer/dashboard.html', config={'VTU_MOCK_MODE': current_mode})

@analyzer_bp.route('/api/start_analysis', methods=['POST'])
def start_analysis():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    job_id = str(uuid.uuid4())
    usn_list = []

    # Retrieve report settings parameters
    department = request.form.get('department')
    academic_year = request.form.get('academic_year')
    examination = request.form.get('examination')
    semester = request.form.get('semester')
    scheme = request.form.get('scheme')
    result_type = request.form.get('result_type', 'regular').strip().lower()
    vtu_result_link = request.form.get('vtu_result_link', '').strip()
    
    if semester and str(semester).isdigit():
        semester = int(semester)
        
    report_settings = {
        'department': department,
        'academic_year': academic_year,
        'examination': examination,
        'semester': semester,
        'scheme': scheme,
        'result_type': result_type,
        'vtu_result_link': vtu_result_link
    }

    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        filename = file.filename.lower()
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            import pandas as pd
            try:
                df = pd.read_excel(file, header=None)
                if not df.empty:
                    usn_pattern = re.compile(r"^[1-9][A-Z0-9]{2}\d{2}[A-Z]{2}\d{3}$", re.IGNORECASE)
                    for col_idx in range(df.shape[1]):
                        col_data = df.iloc[:, col_idx].dropna().astype(str).str.strip().str.upper()
                        valid_usns = [val for val in col_data if usn_pattern.match(val)]
                        if valid_usns:
                            usn_list = valid_usns
                            break
            except Exception as e:
                _logger.error(f"Failed to read Excel file: {e}")
                return jsonify({'error': f'Failed to read Excel file: {str(e)}'}), 400
        else:
            # TXT or CSV: first token on each non-empty line is the USN
            content = file.read().decode('utf-8', errors='ignore').splitlines()
            usn_pattern_check = re.compile(r'^[1-9][A-Z0-9]{2}\d{2}[A-Z]{2}\d{3}$', re.IGNORECASE)
            raw_usns = [line.split(',')[0].strip().upper() for line in content if line.strip()]
            usn_list = [u for u in raw_usns if usn_pattern_check.match(u)]
            if not usn_list and raw_usns:
                # If none matched the strict pattern, use raw (user may have lateral entry USNs)
                usn_list = raw_usns
    else:
        start_usn = request.form.get('start_usn', '').strip()
        count = int(request.form.get('count', 1))
        if start_usn: usn_list = expand_usn_range(start_usn, count)
            
    if not usn_list: return jsonify({'error': 'No input provided'}), 400

    # De-duplicate while preserving order
    seen = set()
    deduped = []
    for u in usn_list:
        uu = u.strip().upper()
        if uu and uu not in seen:
            seen.add(uu)
            deduped.append(uu)
    usn_list = deduped

    is_mock = session.get('use_mock')
    JOBS[job_id] = {'excel_file': None, 'captcha_solved': True} 
    thread = threading.Thread(target=background_scraper, args=(job_id, usn_list, session.get('user_id'), is_mock, report_settings))
    thread.daemon = True
    thread.start()
    return jsonify({'job_id': job_id})

@analyzer_bp.route('/api/progress/<job_id>')
def get_progress(job_id):
    job = JOBS.get(job_id)
    if not job: return jsonify({'error': 'Job not found'}), 404
    current_status = job.get('status', '')
    is_waiting_captcha = str(current_status).startswith('Waiting for Captcha')
    return jsonify({
        'status': current_status,
        'total': job.get('total', 0),
        'completed': job.get('completed', 0),
        'current_usn': job.get('current_usn'),
        'queue_usn': job.get('current_usn'),  # Explicit queue USN for sync validation
        'captcha_base64': job.get('captcha_base64') if is_waiting_captcha else None
    })

@analyzer_bp.route('/api/submit_captcha/<job_id>', methods=['POST'])
def submit_captcha(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    payload      = request.json or {}
    captcha      = payload.get('captcha')
    sub_token    = payload.get('captcha_token')  # Frontend must echo the session token
    sub_usn      = payload.get('queue_usn')      # Optional cross-check

    if not captcha:
        return jsonify({'error': 'No captcha text provided'}), 400

    # ── Guard 1: State machine check ────────────────────────────────────────
    current_state  = job.get('captcha_state', 'IDLE')
    current_status = job.get('status', '')
    queue_locked   = job.get('queue_locked', False)

    if not queue_locked or not str(current_status).startswith('Waiting for Captcha'):
        _logger.warning(
            f"[submit_captcha] Job {job_id}: rejected — queue not locked "
            f"(state={current_state}, status='{current_status}', locked={queue_locked}). "
            f"Submitted USN: {sub_usn}, Current USN: {job.get('current_usn')}"
        )
        return jsonify({
            'error': 'Job is not currently waiting for captcha input',
            'captcha_state': current_state,
            'queue_locked': queue_locked
        }), 409

    # ── Guard 2: Session token validation ───────────────────────────────────
    # Each captcha fetch generates a fresh UUID token.
    # A submission carrying a different token is stale and must be rejected.
    expected_token = job.get('captcha_token')
    if expected_token and sub_token and sub_token != expected_token:
        _logger.error(
            f"[submit_captcha] STALE TOKEN — Job {job_id}, USN {job.get('current_usn')}: "
            f"expected token {expected_token[:8]}…, received {sub_token[:8]}… — submission rejected."
        )
        return jsonify({
            'error': 'Stale captcha token — this captcha session has expired',
            'hint': 'Reload the captcha and try again'
        }), 409

    # ── Guard 3: USN cross-check (optional defensive layer) ─────────────────
    queue_usn = job.get('current_usn', '')
    if sub_usn and sub_usn != queue_usn:
        _logger.error(
            f"[submit_captcha] QUEUE DESYNC — Job {job_id}: "
            f"frontend submitted for USN '{sub_usn}' but queue is on '{queue_usn}'. Rejected."
        )
        return jsonify({
            'error': 'Queue desync: submitted USN does not match current queue USN',
            'submitted_usn': sub_usn,
            'queue_usn': queue_usn
        }), 409

    # ── Accept submission ────────────────────────────────────────────────────
    job['captcha_text']   = captcha
    job['captcha_solved'] = True
    job['captcha_state']  = 'SUBMITTED'
    _logger.info(
        f"[submit_captcha] ✓ Accepted — Job {job_id}, USN {queue_usn}, "
        f"token {str(sub_token or expected_token or '')[:8]}…"
    )
    return jsonify({'success': True, 'queue_usn': queue_usn})

@analyzer_bp.route('/download/<job_id>')
def download(job_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    user_id = session.get('user_id')
    
    # Check JOBS memory cache first
    job = JOBS.get(job_id)
    db_job = None
    if not job or not job.get('results'):
        db_job = db_instance.get_analysis_job_results(job_id)

    # Authorization Check: regular users can only access their own jobs
    if not is_admin():
        job_owner = (job.get('user_id') if job else (db_job.get('user_id') if db_job else None))
        if job_owner and str(job_owner) != str(user_id):
            return "Unauthorized access: You can only download your own reports.", 403

    if job and job.get('excel_file'):
        import io
        file_obj = io.BytesIO(job['excel_file'])
        file_obj.seek(0)
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'VTU_Results_{job_id[:8]}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    results = None
    report_settings = None
    if job and job.get('results'):
        results = job['results']
        report_settings = job.get('report_settings')
    elif db_job:
        results = db_job.get('results', [])
        report_settings = db_job.get('report_settings')

    if not results:
        _logger.warning(f"Download requested for job_id '{job_id}' but no results found.")
        return f"Report results for job {job_id} not found.", 404

    try:
        excel_data = generate_excel_report(results, report_settings, user_id)
        excel_bytes = excel_data.getvalue()
        if job:
            job['excel_file'] = excel_bytes

        import io
        file_obj = io.BytesIO(excel_bytes)
        file_obj.seek(0)
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'VTU_Results_{job_id[:8]}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        _logger.exception(f"Error generating Excel report for job_id '{job_id}': {e}")
        return f"Error generating Excel report: {str(e)}", 500

@analyzer_bp.route('/api/history')
def get_history():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    history = db_instance.get_user_analysis_history(session['user_id'])
    return jsonify(history)

@analyzer_bp.route('/download_history/<job_id>')
def download_history(job_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    return download(job_id)

@analyzer_bp.route('/api/toggle_mock', methods=['POST'])
def toggle_mock():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    use_mock = request.json.get('use_mock')
    session['use_mock'] = use_mock
    return jsonify({'success': True, 'current_mode': 'Simulation' if use_mock else 'Live'})

@analyzer_bp.route('/api/job_results/<job_id>')
def get_job_results(job_id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    job = JOBS.get(job_id)
    db_job = None
    if not job or not job.get('results'):
        db_job = db_instance.get_analysis_job_results(job_id)

    # Authorization Check
    if not is_admin():
        job_owner = (job.get('user_id') if job else (db_job.get('user_id') if db_job else None))
        if job_owner and str(job_owner) != str(session['user_id']):
            return jsonify({'error': 'Unauthorized: Access restricted to report owner'}), 403

    results = None
    report_settings = None
    
    if job and job.get('results'):
        results = job['results']
        report_settings = job.get('report_settings')
    elif db_job:
        results = db_job.get('results', [])
        report_settings = db_job.get('report_settings')
            
    if results is not None:
        subject_mappings = {}
        if report_settings:
            subjects_db = db_instance.get_subjects(session['user_id'], {
                'academic_year': report_settings.get('academic_year'),
                'scheme': report_settings.get('scheme'),
                'semester': report_settings.get('semester'),
                'department': report_settings.get('department')
            })
            for s in subjects_db:
                subject_mappings[s.get('subject_code')] = {
                    'subject_name': s.get('subject_name'),
                    'credits': s.get('credits', 4),
                    'faculty_name': s.get('faculty_name', 'Unassigned')
                }
                
        return jsonify({
            'results': results,
            'report_settings': report_settings or {},
            'subject_mappings': subject_mappings
        })
        
    return jsonify({'error': 'Job results not found'}), 404

@analyzer_bp.route('/download_pdf/<job_id>')
def download_pdf(job_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    user_id = session['user_id']
    
    job = JOBS.get(job_id)
    db_job = None
    if not job or not job.get('results'):
        db_job = db_instance.get_analysis_job_results(job_id)

    if not is_admin():
        job_owner = (job.get('user_id') if job else (db_job.get('user_id') if db_job else None))
        if job_owner and str(job_owner) != str(user_id):
            return "Unauthorized access: You can only download your own reports.", 403

    results = None
    report_settings = None
    if job and job.get('results'):
        results = job['results']
        report_settings = job.get('report_settings')
    elif db_job:
        results = db_job.get('results', [])
        report_settings = db_job.get('report_settings')
            
    if not results: return "Results not found", 404
    
    try:
        from services.pdf_service import generate_pdf_report
        pdf_data = generate_pdf_report(results, report_settings, user_id)
        import io
        file_obj = io.BytesIO(pdf_data)
        file_obj.seek(0)
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'VTU_Report_{job_id[:8]}.pdf',
            mimetype='application/pdf'
        )
    except Exception as e:
        return f"Error generating PDF report: {str(e)}", 500

@analyzer_bp.route('/download_csv/<job_id>')
def download_csv(job_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    user_id = session['user_id']
    
    job = JOBS.get(job_id)
    db_job = None
    if not job or not job.get('results'):
        db_job = db_instance.get_analysis_job_results(job_id)

    if not is_admin():
        job_owner = (job.get('user_id') if job else (db_job.get('user_id') if db_job else None))
        if job_owner and str(job_owner) != str(user_id):
            return "Unauthorized access: You can only download your own reports.", 403

    results = None
    report_settings = None
    if job and job.get('results'):
        results = job['results']
        report_settings = job.get('report_settings')
    elif db_job:
        results = db_job.get('results', [])
        report_settings = db_job.get('report_settings')
            
    if not results: return "Results not found", 404
    
    try:
        from processor import generate_csv_report
        csv_data = generate_csv_report(results, report_settings, user_id)
        import io
        file_obj = io.BytesIO(csv_data.encode('utf-8'))
        file_obj.seek(0)
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'VTU_Report_{job_id[:8]}.csv',
            mimetype='text/csv'
        )
    except Exception as e:
        return f"Error generating CSV report: {str(e)}", 500
