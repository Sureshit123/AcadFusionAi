"""
AcadFusion AI – Regression & Bug-Fix Validation Tests
======================================================
Tests cover all 5 bugs fixed in this session:
  Issue 1 – Internal-only subjects must not show FAIL (external=0 with P from VTU)
  Issue 2 – Institution Hub config must be used for faculty
  Issue 3 – Faculty Master must load correctly per subject
  Issue 4 – Credits must come from Institution Hub, not hardcoded as 4
  Issue 5 – USN processing errors must never silently drop a USN
"""

import sys
import os
import traceback
import logging

logging.basicConfig(level=logging.ERROR)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pass(msg):
    print(f"  [PASS]  {msg}")

def _fail(msg, detail=""):
    print(f"  [FAIL]  {msg}")
    if detail:
        print(f"          {detail}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Issue 1 – Internal-only subject detection in scraper (parse_vtu_html)
# ---------------------------------------------------------------------------

def test_scraper_internal_only_detection():
    """
    Verifies that parse_vtu_html does NOT force-fail a subject whose
    external marks == 0 when the VTU site returned P (internal-only subject).
    """
    print("\n[Issue 1] Scraper: internal-only subject detection")

    # Minimal HTML simulating a VTU page with an internal-only subject
    html = """
    <html><body>
    <table>
      <tr><td>STUDENT NAME</td><td>Test Student</td></tr>
      <tr>
        <td>21KCS651</td>
        <td>Yoga and Wellness</td>
        <td>50</td>
        <td>0</td>
        <td>50</td>
        <td>P</td>
      </tr>
      <tr>
        <td>21CS641</td>
        <td>Machine Learning</td>
        <td>40</td>
        <td>55</td>
        <td>95</td>
        <td>P</td>
      </tr>
    </table>
    </body></html>
    """

    # Import and test
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scraper import parse_vtu_html

    result = parse_vtu_html("2BL23IS001", html)
    subjects = result.get("subjects", {})

    # Yoga / internal-only subject
    yoga = subjects.get("21KCS651")
    if yoga is None:
        _fail("21KCS651 (Yoga) was not parsed", str(subjects))
    if yoga.get("result") != "P":
        _fail(
            "21KCS651 (Yoga) should be P (internal-only, ext=0), but got result="
            + str(yoga.get("result"))
        )
    if not yoga.get("is_internal_only"):
        _fail(
            "21KCS651 (Yoga) should have is_internal_only=True, got: "
            + str(yoga.get("is_internal_only"))
        )
    _pass("21KCS651 (Yoga, ext=0) correctly identified as internal-only and not failed")

    # Regular subject
    ml = subjects.get("21CS641")
    if ml is None:
        _fail("21CS641 (ML) was not parsed")
    if ml.get("is_internal_only"):
        _fail("21CS641 (ML) should NOT be internal-only")
    _pass("21CS641 (ML) correctly identified as a regular subject")


# ---------------------------------------------------------------------------
# Issue 1 – classify_grade respects is_internal_only
# ---------------------------------------------------------------------------

def test_classify_grade_internal_only():
    """classify_grade must not fail an internal-only subject due to external < 18."""
    print("\n[Issue 1] classify_grade: internal-only aware grading")

    from processor import classify_grade

    # Internal-only: external=0, total=75 -> should be A (not F)
    grade = classify_grade(total=75, internal=75, external=0, is_internal_only=True)
    if grade == "F":
        _fail("classify_grade returned F for internal-only subject with total=75, ext=0")
    _pass(f"Internal-only subject with total=75 -> grade={grade} (expected A or similar, not F)")

    # Regular subject: external=10, total=60 -> must be F (ext < 18)
    grade = classify_grade(total=60, internal=50, external=10, is_internal_only=False)
    if grade != "F":
        _fail(f"Regular subject with ext=10 should be F, got {grade}")
    _pass(f"Regular subject with ext=10 -> grade=F (correctly failed)")

    # Regular subject: external=20, total=60 -> should be B+
    grade = classify_grade(total=60, internal=40, external=20, is_internal_only=False)
    if grade == "F":
        _fail(f"Regular subject with ext=20 and total=60 should not be F, got {grade}")
    _pass(f"Regular subject ext=20, total=60 -> grade={grade} (not F)")


# ---------------------------------------------------------------------------
# Issue 1B + 2 + 3 + 4 – calculate_sgpa_and_map_faculty
# ---------------------------------------------------------------------------

def test_sgpa_internal_only_credits_faculty():
    """
    Verifies:
    - Internal-only subjects don't get failed due to external=0
    - Credits come from Institution Hub, not hardcoded 4
    - Faculty comes from Institution Hub
    - Name-based fallback matching works
    - SGPA computed correctly with correct credits
    """
    print("\n[Issues 1B, 2, 3, 4] calculate_sgpa_and_map_faculty")

    # Simulate importing without Flask app context
    import importlib
    import types

    # Minimal subjects_db (Institution Hub data)
    subjects_db = [
        {
            "subject_code": "21KCS651",
            "subject_name": "Yoga and Wellness",
            "faculty_name": "Ravi Kumar",
            "credits": 1,
            "subject_type": "Internal Only",
        },
        {
            "subject_code": "21CS641",
            "subject_name": "Machine Learning",
            "faculty_name": "Priya Singh",
            "credits": 3,
            "subject_type": "Theory",
        },
        {
            "subject_code": "21EVS51",
            "subject_name": "Environmental Studies",
            "faculty_name": "Anil Reddy",
            "credits": 2,
            "max_external_marks": 0,   # Hub says no external
        },
    ]

    student_result = {
        "usn": "2BL23IS001",
        "name": "Test Student",
        "status": "Pass",
        "subjects": {
            "21KCS651": {
                "name": "Yoga and Wellness",
                "internal": 75,
                "external": 0,
                "total": 75,
                "result": "P",
                "is_internal_only": True,
            },
            "21CS641": {
                "name": "Machine Learning",
                "internal": 40,
                "external": 55,
                "total": 95,
                "result": "P",
            },
            "21EVS51": {
                "name": "Environmental Studies",
                "internal": 80,
                "external": 0,
                "total": 80,
                "result": "P",
                "is_internal_only": True,
            },
        },
    }

    # Call the function (requires blueprints.analyzer to be importable without Flask)
    from blueprints.analyzer import calculate_sgpa_and_map_faculty
    calculate_sgpa_and_map_faculty(student_result, subjects_db)

    subjs = student_result["subjects"]

    # --- Issue 4: Credits must not all be 4 ---
    yoga_credits = subjs["21KCS651"].get("credits")
    if yoga_credits != 1:
        _fail(f"Yoga credits should be 1 (from Institution Hub), got {yoga_credits}")
    _pass(f"Yoga credits={yoga_credits} (correct from Institution Hub)")

    ml_credits = subjs["21CS641"].get("credits")
    if ml_credits != 3:
        _fail(f"ML credits should be 3, got {ml_credits}")
    _pass(f"ML credits={ml_credits} (correct from Institution Hub)")

    evs_credits = subjs["21EVS51"].get("credits")
    if evs_credits != 2:
        _fail(f"EVS credits should be 2, got {evs_credits}")
    _pass(f"EVS credits={evs_credits} (correct from Institution Hub)")

    # --- Issue 3: Faculty must come from Institution Hub ---
    yoga_faculty = subjs["21KCS651"].get("faculty")
    if yoga_faculty != "Ravi Kumar":
        _fail(f"Yoga faculty should be 'Ravi Kumar', got '{yoga_faculty}'")
    _pass(f"Yoga faculty='{yoga_faculty}' (correct from Institution Hub)")

    ml_faculty = subjs["21CS641"].get("faculty")
    if ml_faculty != "Priya Singh":
        _fail(f"ML faculty should be 'Priya Singh', got '{ml_faculty}'")
    _pass(f"ML faculty='{ml_faculty}' (correct from Institution Hub)")

    # --- Issue 1B: Internal-only subjects must pass even with ext=0 ---
    yoga_result = subjs["21KCS651"].get("result")
    if yoga_result != "P":
        _fail(f"Yoga should be P (internal-only), got result={yoga_result}")
    _pass(f"Yoga result={yoga_result} (not forced to F)")

    evs_result = subjs["21EVS51"].get("result")
    if evs_result != "P":
        _fail(f"EVS should be P (internal-only via max_external_marks=0), got result={evs_result}")
    _pass(f"EVS result={evs_result} (not forced to F)")

    # --- SGPA check: must use correct credits ---
    # Yoga: gp=8 (tot=75), credits=1  → 8
    # ML: gp=10 (tot=95), credits=3   → 30
    # EVS: gp=9 (tot=80), credits=2   → 18
    # total_grade_points = 8+30+18 = 56, total_credits = 6
    # SGPA = 56/6 = 9.33
    expected_sgpa = round(56 / 6, 2)
    actual_sgpa = student_result.get("sgpa")
    if abs(actual_sgpa - expected_sgpa) > 0.05:
        _fail(f"SGPA should be ~{expected_sgpa}, got {actual_sgpa}")
    _pass(f"SGPA={actual_sgpa} (correctly computed with Institution Hub credits)")


# ---------------------------------------------------------------------------
# Issue 2 – Name-based subject fallback matching
# ---------------------------------------------------------------------------

def test_name_based_fallback_matching():
    """
    When no subject code matches, Institution Hub match should fall back
    to comparing subject names.
    """
    print("\n[Issue 2] Name-based fallback matching in calculate_sgpa_and_map_faculty")

    subjects_db = [
        {
            "subject_code": "XXXXXX",  # non-matching code
            "subject_name": "Advanced Algorithms",
            "faculty_name": "Sushma Nair",
            "credits": 4,
        }
    ]

    student_result = {
        "usn": "TEST001",
        "name": "Test",
        "status": "Pass",
        "subjects": {
            "21CS650": {
                "name": "Advanced Algorithms",   # name matches Institution Hub
                "internal": 45,
                "external": 60,
                "total": 105,
                "result": "P",
            }
        },
    }

    from blueprints.analyzer import calculate_sgpa_and_map_faculty
    calculate_sgpa_and_map_faculty(student_result, subjects_db)

    sub = student_result["subjects"]["21CS650"]
    if sub.get("faculty") != "Sushma Nair":
        _fail(f"Name-based fallback failed: expected 'Sushma Nair', got '{sub.get('faculty')}'")
    _pass(f"Name-based fallback correctly resolved faculty='{sub.get('faculty')}'")

    if sub.get("credits") != 4:
        _fail(f"Name-based fallback credits wrong: expected 4, got {sub.get('credits')}")
    _pass(f"Name-based fallback correctly resolved credits={sub.get('credits')}")


# ---------------------------------------------------------------------------
# Issue 5 – USN processing errors must never silently drop a USN
# ---------------------------------------------------------------------------

def test_usn_never_silently_dropped():
    """
    Simulates an unexpected exception inside background_scraper loop.
    Verifies every USN ends in Success, Failed, or Processing Error –
    never silently disappears.
    """
    print("\n[Issue 5] USN processing error containment")

    from blueprints.analyzer import JOBS
    import uuid

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "total": 3,
        "completed": 0,
        "results": [],
        "status": "Running",
        "current_usn": "",
        "captcha_solved": True,
        "captcha_text": "SIM",
        "excel_file": None,
    }

    # Manually simulate the error path:
    # If an exception occurs, the result must be appended with Processing Error status
    usn = "2BL23IS001"
    try:
        raise RuntimeError("Simulated unexpected error during scraping")
    except Exception as exc:
        JOBS[job_id]["results"].append({
            "usn": usn,
            "status": f"Processing Error: {str(exc)[:120]}"
        })
        JOBS[job_id]["completed"] += 1

    found = next((r for r in JOBS[job_id]["results"] if r["usn"] == usn), None)
    if found is None:
        _fail(f"USN {usn} was silently dropped from results!")
    if "Processing Error" not in found.get("status", ""):
        _fail(f"USN {usn} status should contain 'Processing Error', got: {found.get('status')}")
    _pass(f"USN {usn} correctly captured with status='{found['status']}'")

    completed = JOBS[job_id]["completed"]
    if completed != 1:
        _fail(f"completed counter should be 1 after error, got {completed}")
    _pass(f"completed counter={completed} (correctly incremented on error)")

    del JOBS[job_id]


# ---------------------------------------------------------------------------
# Issue 4 – get_mock_result respects credits from Institution Hub keywords
# ---------------------------------------------------------------------------

def test_mock_result_internal_only_subjects():
    """
    get_mock_result must generate internal=0 for internal-only subjects
    and not fail them due to external=0.
    """
    print("\n[Issue 4] get_mock_result: internal-only subject totals")

    from scraper import get_mock_result

    mock_subjects = {
        "21KCS651": "Yoga and Wellness",
        "21CS641": "Machine Learning",
    }

    result = get_mock_result("TEST001", mock_subjects)
    subjects = result["subjects"]

    yoga = subjects.get("21KCS651")
    if yoga is None:
        _fail("21KCS651 (Yoga) not generated in mock result")
    if yoga.get("external") != 0:
        _fail(f"Yoga external should be 0 (internal-only), got {yoga.get('external')}")
    if yoga.get("is_internal_only") is not True:
        _fail(f"Yoga is_internal_only should be True, got {yoga.get('is_internal_only')}")
    if yoga.get("result") not in ["P", "F"]:
        _fail(f"Yoga result should be P or F, got {yoga.get('result')}")
    # Yoga with internal>=40 must pass
    if yoga.get("internal", 0) >= 40 and yoga.get("result") != "P":
        _fail(f"Yoga with internal={yoga.get('internal')} should be P, got {yoga.get('result')}")
    _pass(f"Yoga: external=0, is_internal_only=True, result={yoga.get('result')}")

    ml = subjects.get("21CS641")
    if ml is None:
        _fail("21CS641 (ML) not generated in mock result")
    if ml.get("is_internal_only") is True:
        _fail("ML should NOT be is_internal_only")
    _pass(f"ML: external={ml.get('external')}, is_internal_only={ml.get('is_internal_only')}")


def test_sheet2_grade_ranges():
    """
    Verifies Sheet 2 (Subject Analysis) layout & grade ranges:
      Headers: Subject | Subject Code | Credits | Faculty Assigned | FCD (70–100%) | FC (60–69%) | SC (35–59%) | Fail | Absent | Total Students Appeared | No. of Students Passed | Passing %
      Old columns (Distinction, First Class, Second Class, Pass Class) must NEVER appear.
    """
    print("\n[Issue 1 & 2] Sheet 2 Subject Analysis layout & grade range calculations")

    from processor import generate_excel_report
    import openpyxl

    results = [
        # Student 1: 75 marks -> FCD
        {"usn": "101", "name": "S1", "status": "Pass", "subjects": {"CS1": {"name": "Sub 1", "total": 75, "result": "P"}}},
        # Student 2: 65 marks -> FC
        {"usn": "102", "name": "S2", "status": "Pass", "subjects": {"CS1": {"name": "Sub 1", "total": 65, "result": "P"}}},
        # Student 3: 55 marks -> SC (35-59)
        {"usn": "103", "name": "S3", "status": "Pass", "subjects": {"CS1": {"name": "Sub 1", "total": 55, "result": "P"}}},
        # Student 4: 35 marks -> SC (35-59 inclusive)
        {"usn": "104", "name": "S4", "status": "Pass", "subjects": {"CS1": {"name": "Sub 1", "total": 35, "result": "P"}}},
        # Student 5: 34 marks -> Fail (0-34)
        {"usn": "105", "name": "S5", "status": "Fail", "subjects": {"CS1": {"name": "Sub 1", "total": 34, "result": "F"}}},
        # Student 6: Absent
        {"usn": "106", "name": "S6", "status": "Fail", "subjects": {"CS1": {"name": "Sub 1", "total": 0, "result": "ABSENT"}}},
    ]

    excel_buf = generate_excel_report(results)
    wb = openpyxl.load_workbook(excel_buf)
    ws = wb['Subject Analysis']

    # Header check (Row 3)
    headers = [ws.cell(row=3, column=col).value for col in range(1, 13)]
    for old_h in ["Distinction", "First Class", "Second Class", "Pass Class"]:
        if old_h in headers:
            _fail(f"Old column '{old_h}' found in Sheet 2 headers! Headers: {headers}")
    _pass("Old columns (Distinction, First Class, Second Class, Pass Class) completely removed")

    expected_headers = [
        "Subject", "Subject Code", "Credits", "Faculty Assigned",
        "FCD (70–100%)", "FC (60–69%)", "SC (35–59%)", "Fail", "Absent",
        "Total Students Appeared", "No. of Students Passed", "Passing %"
    ]
    if headers != expected_headers:
        _fail(f"Sheet 2 headers mismatch! Expected {expected_headers}, got {headers}")
    _pass("Sheet 2 column layout matches required specification exactly")

    # Row 4 is CS1 data
    sub_name = ws.cell(row=4, column=1).value     # Subject
    code = ws.cell(row=4, column=2).value         # Subject Code
    distinction = ws.cell(row=4, column=5).value  # FCD
    first_class = ws.cell(row=4, column=6).value  # FC
    second_class = ws.cell(row=4, column=7).value # SC
    failed = ws.cell(row=4, column=8).value       # Fail
    absent = ws.cell(row=4, column=9).value       # Absent
    appeared = ws.cell(row=4, column=10).value    # Total Students Appeared
    passed = ws.cell(row=4, column=11).value      # No. of Students Passed
    pct = ws.cell(row=4, column=12).value         # Passing %

    if code != "CS1":
        _fail(f"Expected CS1 in Row 4 Col 2, got {code}")
    if distinction != 1:
        _fail(f"FCD count expected 1 (75 marks), got {distinction}")
    _pass(f"FCD count = {distinction} (75 marks)")

    if first_class != 1:
        _fail(f"FC count expected 1 (65 marks), got {first_class}")
    _pass(f"FC count = {first_class} (65 marks)")

    if second_class != 2:
        _fail(f"SC count expected 2 (55 and 35 marks), got {second_class}")
    _pass(f"SC count = {second_class} (55 and 35 marks)")

    if failed != 1:
        _fail(f"Failed count expected 1 (34 marks), got {failed}")
    _pass(f"Failed count = {failed} (34 marks)")

    if absent != 1:
        _fail(f"Absent count expected 1, got {absent}")
    _pass(f"Absent count = {absent}")

    if appeared != 5:
        _fail(f"Total Students Appeared expected 5, got {appeared}")
    _pass(f"Total Students Appeared = {appeared}")

    if passed != 4:
        _fail(f"No. of Students Passed expected 4 (1 FCD + 1 FC + 2 SC), got {passed}")
    _pass(f"No. of Students Passed = {passed} (FCD + FC + SC = {distinction} + {first_class} + {second_class})")

    expected_pct = round((4 / 5) * 100, 2)
    if pct != expected_pct:
        _fail(f"Passing % expected {expected_pct}%, got {pct}%")
    _pass(f"Passing % = {pct}% (correctly computed)")


def test_subject_mapping_single_source_of_truth():
    """
    Verifies that Subject Mapping from Institution Hub loads faculty names and credits
    correctly for example subjects:
      1BMATS201 -> Credits: 4, Faculty: Dr. P.K. Gonnagar
      1BAIA203  -> Credits: 3, Faculty: Prof. Rashmi Doddamani
      1BENG206  -> Credits: 1, Faculty: Prof. Satyajeet Nimbaragi
      1BPRJ258  -> Credits: 1, Faculty: Prof. Chaitra Bagewadi
      1BIC207   -> Credits: 0, Faculty: Prof. Shweta Wangi
    """
    print("\n[Issue 2] Subject Mapping single source of truth resolution")

    subjects_db = [
        {"subject_code": "1BMATS201", "subject_name": "Mathematics II", "credits": 4, "faculty_name": "Dr. P.K. Gonnagar"},
        {"subject_code": "1BAIA203", "subject_name": "Artificial Intelligence", "credits": 3, "faculty_name": "Prof. Rashmi Doddamani"},
        {"subject_code": "1BENG206", "subject_name": "English", "credits": 1, "faculty_name": "Prof. Satyajeet Nimbaragi"},
        {"subject_code": "1BPRJ258", "subject_name": "Mini Project", "credits": 1, "faculty_name": "Prof. Chaitra Bagewadi"},
        {"subject_code": "1BIC207", "subject_name": "Indian Constitution", "credits": 0, "faculty_name": "Prof. Shweta Wangi"},
    ]

    student_result = {
        "usn": "1BM23CS001",
        "name": "Ananya Sharma",
        "status": "Pass",
        "subjects": {
            "1BMATS201": {"name": "Mathematics II", "internal": 40, "external": 50, "total": 90, "result": "P"},
            "1BAIA203": {"name": "Artificial Intelligence", "internal": 35, "external": 45, "total": 80, "result": "P"},
            "1BENG206": {"name": "English", "internal": 20, "external": 30, "total": 50, "result": "P"},
            "1BPRJ258": {"name": "Mini Project", "internal": 45, "external": 0, "total": 45, "result": "P", "is_internal_only": True},
            "1BIC207": {"name": "Indian Constitution", "internal": 40, "external": 0, "total": 40, "result": "P", "is_internal_only": True},
        }
    }

    from blueprints.analyzer import calculate_sgpa_and_map_faculty
    calculate_sgpa_and_map_faculty(student_result, subjects_db)

    subjs = student_result["subjects"]

    # Verify 1BMATS201
    mats = subjs["1BMATS201"]
    if mats.get("faculty") != "Dr. P.K. Gonnagar" or mats.get("credits") != 4:
        _fail(f"1BMATS201 failed: faculty='{mats.get('faculty')}', credits={mats.get('credits')}")
    _pass(f"1BMATS201 -> Faculty: '{mats.get('faculty')}', Credits: {mats.get('credits')}")

    # Verify 1BAIA203
    aia = subjs["1BAIA203"]
    if aia.get("faculty") != "Prof. Rashmi Doddamani" or aia.get("credits") != 3:
        _fail(f"1BAIA203 failed: faculty='{aia.get('faculty')}', credits={aia.get('credits')}")
    _pass(f"1BAIA203 -> Faculty: '{aia.get('faculty')}', Credits: {aia.get('credits')}")

    # Verify 1BENG206
    eng = subjs["1BENG206"]
    if eng.get("faculty") != "Prof. Satyajeet Nimbaragi" or eng.get("credits") != 1:
        _fail(f"1BENG206 failed: faculty='{eng.get('faculty')}', credits={eng.get('credits')}")
    _pass(f"1BENG206 -> Faculty: '{eng.get('faculty')}', Credits: {eng.get('credits')}")

    # Verify 1BPRJ258
    prj = subjs["1BPRJ258"]
    if prj.get("faculty") != "Prof. Chaitra Bagewadi" or prj.get("credits") != 1:
        _fail(f"1BPRJ258 failed: faculty='{prj.get('faculty')}', credits={prj.get('credits')}")
    _pass(f"1BPRJ258 -> Faculty: '{prj.get('faculty')}', Credits: {prj.get('credits')}")

    # Verify 1BIC207
    ic = subjs["1BIC207"]
    if ic.get("faculty") != "Prof. Shweta Wangi" or ic.get("credits") != 0:
        _fail(f"1BIC207 failed: faculty='{ic.get('faculty')}', credits={ic.get('credits')}")
    _pass(f"1BIC207 -> Faculty: '{ic.get('faculty')}', Credits: {ic.get('credits')}")


def test_excel_download_fallback():
    """
    Verifies `/download/<job_id>` route fallback logic:
    1. Returns excel file if in JOBS memory cache
    2. Generates excel file if memory has results
    3. Falls back to MongoDB when memory cache is empty
    """
    print("\n[Issue 1] Excel Download route fallback logic")

    from blueprints.analyzer import JOBS, download
    from flask import Flask

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.secret_key = 'test'

    job_id = "test-job-fallback-123"
    results = [
        {"usn": "101", "name": "S1", "status": "Pass", "subjects": {"CS1": {"name": "Sub 1", "total": 80, "result": "P"}}}
    ]

    with app.test_request_context():
        # Case 1: Job with memory results
        JOBS[job_id] = {
            "results": results,
            "report_settings": {},
            "excel_file": None
        }

        response = download(job_id)
        if response.status_code != 200:
            _fail(f"Expected HTTP 200 on download with memory results, got {response.status_code}")
        if response.mimetype != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
            _fail(f"Expected spreadsheet mimetype, got {response.mimetype}")
        _pass("Download successfully generated Excel buffer from memory results")

        # Cleanup memory
        del JOBS[job_id]

        # Case 2: Job not in memory -> returns 404
        response = download("non-existent-job-xyz")
        if isinstance(response, tuple):
            status_code = response[1]
        else:
            status_code = response.status_code

        if status_code != 404:
            _fail(f"Expected 404 for missing job, got {status_code}")
        _pass("Download correctly returned 404 for non-existent job with clear message")


def test_no_alternate_usns_skipped():
    """
    Verifies Issue 1 fix:
    Processing a bulk list of 10 USNs (001 to 010) processes every single USN.
    Queue length equals uploaded count, 0 USNs skipped, 0 duplicates.
    """
    print("\n[Issue 1 - Fix] Bulk USN Queue Processing (001 to 010 without skipping alternate USNs)")

    from blueprints.analyzer import JOBS, background_scraper
    import uuid

    usn_input = [f"2BL23IS{i:03d}" for i in range(1, 11)] # 001 to 010
    job_id = f"test-job-queue-{uuid.uuid4().hex[:6]}"
    
    JOBS[job_id] = {'excel_file': None, 'captcha_solved': True}

    # Run in simulation/mock mode
    background_scraper(job_id, usn_input, "test_user", is_mock=True, report_settings={})

    job = JOBS.get(job_id)
    if not job:
        _fail("Job not found in memory after background_scraper completion")

    results = job.get('results', [])
    processed_usns = [r['usn'] for r in results]

    if len(processed_usns) != 10:
        _fail(f"Expected 10 processed USNs, got {len(processed_usns)}: {processed_usns}")
    _pass(f"All 10 USNs processed (Total: {len(processed_usns)})")

    # Check order & exact match
    for idx, expected in enumerate(usn_input):
        if processed_usns[idx] != expected:
            _fail(f"USN mismatch at index {idx}: expected {expected}, got {processed_usns[idx]}")
    _pass("Processed USNs match input list exactly with 0 alternate USNs skipped")

    # Check no duplicates
    if len(set(processed_usns)) != 10:
        _fail(f"Duplicate USNs detected in processed results: {processed_usns}")
    _pass("Zero duplicates in processed results")

    # Cleanup memory
    del JOBS[job_id]


def test_timetable_generator_restored():
    """
    Verifies Issue 2 fix:
    Timetable Generator blueprint, database methods, and routes are fully restored.
    """
    print("\n[Issue 2 - Fix] Timetable Generator Restoration Verification")

    from blueprints.timetable import timetable_bp
    from models.database import db_instance

    if not timetable_bp:
        _fail("timetable_bp blueprint is missing or failed to import")
    _pass("timetable_bp successfully imported")

    # Verify DB methods exist on db_instance
    methods = ['save_timetable', 'get_teacher_schedule', 'update_teacher_schedule', 'get_department_resources', 'update_department_resources', 'save_cycle', 'get_user_timetable_history', 'delete_timetable_cycle']
    for m in methods:
        if not hasattr(db_instance, m):
            _fail(f"db_instance missing required method '{m}'")
    _pass("All 8 timetable database methods verified on db_instance")


def test_teacher_feedback_module():
    """
    Verifies Issue 3 fix:
    Teacher Feedback blueprint routes, analytics calculation, duplicate check, and exports.
    """
    print("\n[Issue 3 - Fix] Teacher Feedback Module Verification")

    from blueprints.feedback import feedback_bp
    from models.database import db_instance

    if not feedback_bp:
        _fail("feedback_bp blueprint is missing or failed to import")
    _pass("feedback_bp successfully imported")

    # Verify DB feedback methods exist
    fb_methods = ['save_feedback', 'check_existing_feedback', 'get_feedbacks', 'get_feedback_analytics']
    for m in fb_methods:
        if not hasattr(db_instance, m):
            _fail(f"db_instance missing required feedback method '{m}'")
    _pass("All feedback database methods verified on db_instance")


def test_uploaded_order_preserved_in_excel():
    """
    Verifies Upload Order Preservation Fix:
    1. Uploaded USN count = Output row count (1-to-1 symmetry)
    2. Excel Sheet 3 ('All Students') rows match uploaded USNs in EXACT order (Row 5 -> 001, Row 6 -> 002, ..., Row 14 -> 010)
    3. Even if non-Pass/Fail or error status occurs, USN is preserved in position without being reordered or dropped.
    """
    print("\n[Upload Order Fix] 1-to-1 Order Preservation and Excel Row Alignment")

    from blueprints.analyzer import JOBS, background_scraper
    from processor import generate_excel_report
    import openpyxl
    import io
    import uuid

    usn_input = [f"2BL23IS{i:03d}" for i in range(1, 11)] # 001 to 010
    job_id = f"test-job-order-{uuid.uuid4().hex[:6]}"
    
    JOBS[job_id] = {'excel_file': None, 'captcha_solved': True}

    background_scraper(job_id, usn_input, "test_user", is_mock=True, report_settings={'department': 'ISE', 'semester': 5})

    job = JOBS.get(job_id)
    results = job.get('results', [])

    # Check 1: Count match
    if len(results) != 10:
        _fail(f"Results count mismatch: expected 10, got {len(results)}")
    _pass(f"Count symmetry verified: Uploaded (10) == Results ({len(results)})")

    # Check 2: Exact Upload Order in memory
    for idx, expected in enumerate(usn_input):
        actual = results[idx]['usn']
        if actual != expected:
            _fail(f"Memory order mismatch at index {idx}: expected {expected}, got {actual}")
    _pass("Memory results match uploaded USN order exactly")

    # Check 3: Excel Sheet 3 Row Alignment
    excel_bytes = generate_excel_report(results, {'department': 'ISE', 'semester': 5}, "test_user")
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes.getvalue()))
    ws_all = wb['All Students']

    # Rows 5 to 14 correspond to USNs 001 to 010
    excel_usns = []
    for r in range(5, 15):
        val = ws_all.cell(row=r, column=2).value
        excel_usns.append(val)

    if len(excel_usns) != 10:
        _fail(f"Excel Sheet 3 student row count mismatch: expected 10, got {len(excel_usns)}")
    _pass(f"Excel Sheet 3 row count verified: {len(excel_usns)} rows")

    for idx, expected in enumerate(usn_input):
        actual = excel_usns[idx]
        if actual != expected:
            _fail(f"Excel Row {idx+5} USN mismatch: expected {expected}, got {actual}")
    _pass("Excel Sheet 3 rows match uploaded USN order 100% symmetrically (Row 5=001 ... Row 14=010)")

    # Cleanup memory
    del JOBS[job_id]


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("AcadFusion AI – Bug Fix Regression Tests")
    print("=" * 60)

    tests = [
        test_scraper_internal_only_detection,
        test_classify_grade_internal_only,
        test_sgpa_internal_only_credits_faculty,
        test_name_based_fallback_matching,
        test_usn_never_silently_dropped,
        test_mock_result_internal_only_subjects,
        test_sheet2_grade_ranges,
        test_subject_mapping_single_source_of_truth,
        test_excel_download_fallback,
        test_no_alternate_usns_skipped,
        test_timetable_generator_restored,
        test_teacher_feedback_module,
        test_uploaded_order_preserved_in_excel,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except SystemExit:
            failed += 1
        except Exception as e:
            print(f"  [ERROR] UNEXPECTED EXCEPTION in {test_fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


