
from weasyprint import HTML
import io

try:
    html = "<h1>Test</h1>"
    pdf = HTML(string=html).write_pdf()
    print("SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
