import io
import unittest
from processor import generate_excel_report


class ExcelGenerationTests(unittest.TestCase):
    def test_generate_excel_report_empty(self):
        buf = generate_excel_report([])
        self.assertIsInstance(buf, io.BytesIO)
        data = buf.getvalue()
        self.assertGreater(len(data), 0)
        self.assertEqual(data[:2], b'PK')

    def test_generate_excel_report_with_results(self):
        results = [
            {
                'usn': '123',
                'name': 'abc',
                'status': 'Pass',
                'subjects': {
                    'CS1': {'internal': 10, 'external': 20, 'total': 30, 'result': 'P'}
                },
                'total_marks': 30,
                'max_marks': 100,
                'percentage': 30.0
            }
        ]
        buf = generate_excel_report(results)
        self.assertIsInstance(buf, io.BytesIO)
        data = buf.getvalue()
        self.assertGreater(len(data), 0)
        self.assertEqual(data[:2], b'PK')


if __name__ == '__main__':
    unittest.main()
