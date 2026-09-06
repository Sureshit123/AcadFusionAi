from processor import generate_excel_report
buf = generate_excel_report([])
print('OK, size=', len(buf.getvalue()))
