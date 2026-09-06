import traceback
from processor import generate_excel_report, generate_csv_report
from services.pdf_service import generate_pdf_report

# Mock result data from a result analysis job
results = [
    {
        'usn': '1BL23IS001',
        'name': 'Sureshit Sharma',
        'status': 'Pass',
        'subjects': {
            'BCS401': {'name': 'Analysis and Design of Algorithms', 'internal': 42, 'external': 38, 'total': 80, 'result': 'P', 'credits': 4, 'faculty': 'Dr. A Kumar'},
            'BCS402': {'name': 'Operating Systems', 'internal': 40, 'external': 35, 'total': 75, 'result': 'P', 'credits': 4, 'faculty': 'Prof. S Rao'},
            'BCS403': {'name': 'Database Management Systems', 'internal': 45, 'external': 37, 'total': 82, 'result': 'P', 'credits': 4, 'faculty': 'Dr. P Sharma'}
        },
        'total_marks': 237,
        'max_marks': 300,
        'percentage': 79.0,
        'sgpa': 8.0
    },
    {
        'usn': '1BL23IS002',
        'name': 'Jane Smith',
        'status': 'Fail',
        'subjects': {
            'BCS401': {'name': 'Analysis and Design of Algorithms', 'internal': 22, 'external': 12, 'total': 34, 'result': 'F', 'credits': 4, 'faculty': 'Dr. A Kumar'},
            'BCS402': {'name': 'Operating Systems', 'internal': 35, 'external': 22, 'total': 57, 'result': 'P', 'credits': 4, 'faculty': 'Prof. S Rao'},
            'BCS403': {'name': 'Database Management Systems', 'internal': 41, 'external': 30, 'total': 71, 'result': 'P', 'credits': 4, 'faculty': 'Dr. P Sharma'}
        },
        'total_marks': 162,
        'max_marks': 300,
        'percentage': 54.0,
        'sgpa': 0.0
    }
]

# Mock configuration settings parameters
report_settings = {
    'department': 'Department of Information Science & Engineering',
    'academic_year': '2026-27',
    'examination': 'Even Semester',
    'semester': 4,
    'scheme': '2022 Scheme'
}

print("Starting verification of reporting engines...")

try:
    print("1. Testing Excel Generator...")
    excel_buf = generate_excel_report(results, report_settings, user_id=None)
    with open("scratch/test_report.xlsx", "wb") as f:
        f.write(excel_buf.getvalue())
    print("   Excel Generation: SUCCESS (Saved to scratch/test_report.xlsx)")
    
    print("2. Testing CSV Generator...")
    csv_str = generate_csv_report(results, report_settings, user_id=None)
    with open("scratch/test_report.csv", "w", encoding="utf-8") as f:
        f.write(csv_str)
    print("   CSV Generation: SUCCESS (Saved to scratch/test_report.csv)")
    
    print("3. Testing PDF Generator (ReportLab & Matplotlib)...")
    pdf_bytes = generate_pdf_report(results, report_settings, user_id=None)
    with open("scratch/test_report.pdf", "wb") as f:
        f.write(pdf_bytes)
    print("   PDF Generation: SUCCESS (Saved to scratch/test_report.pdf)")
    
    print("\nVerification complete! All 3 formats generated successfully.")
except Exception as e:
    print("\nVERIFICATION FAILED:")
    traceback.print_exc()
