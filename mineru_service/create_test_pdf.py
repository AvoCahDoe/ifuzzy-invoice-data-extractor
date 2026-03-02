"""Create a simple test PDF for MinerU testing."""
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors

def create_test_pdf(filename="test_invoice.pdf"):
    """Create a simple invoice-style PDF for testing."""
    c = canvas.Canvas(filename, pagesize=letter)
    width, height = letter
    
    # Header
    c.setFont("Helvetica-Bold", 24)
    c.drawString(200, height - 50, "INVOICE")
    
    # Company info
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 100, "Test Company Inc.")
    c.drawString(50, height - 115, "123 Business Street")
    c.drawString(50, height - 130, "New York, NY 10001")
    c.drawString(50, height - 145, "Email: contact@testcompany.com")
    
    # Invoice details
    c.setFont("Helvetica-Bold", 12)
    c.drawString(400, height - 100, "Invoice #: INV-2024-001")
    c.setFont("Helvetica", 12)
    c.drawString(400, height - 115, "Date: January 30, 2026")
    c.drawString(400, height - 130, "Due: February 28, 2026")
    
    # Bill To section
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 180, "Bill To:")
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 195, "Customer ABC Corp")
    c.drawString(50, height - 210, "456 Customer Avenue")
    c.drawString(50, height - 225, "Los Angeles, CA 90001")
    
    # Table header
    y_pos = height - 280
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y_pos, "Description")
    c.drawString(300, y_pos, "Qty")
    c.drawString(380, y_pos, "Unit Price")
    c.drawString(480, y_pos, "Total")
    
    # Draw line under header
    c.line(50, y_pos - 5, 550, y_pos - 5)
    
    # Table content
    items = [
        ("Web Development Services", "40", "$75.00", "$3,000.00"),
        ("Logo Design", "1", "$500.00", "$500.00"),
        ("Hosting Setup", "1", "$150.00", "$150.00"),
        ("Domain Registration", "2", "$15.00", "$30.00"),
    ]
    
    c.setFont("Helvetica", 11)
    y_pos -= 20
    for item in items:
        c.drawString(50, y_pos, item[0])
        c.drawString(300, y_pos, item[1])
        c.drawString(380, y_pos, item[2])
        c.drawString(480, y_pos, item[3])
        y_pos -= 18
    
    # Draw line above totals
    c.line(350, y_pos - 5, 550, y_pos - 5)
    
    # Totals
    y_pos -= 25
    c.setFont("Helvetica", 11)
    c.drawString(380, y_pos, "Subtotal:")
    c.drawString(480, y_pos, "$3,680.00")
    
    y_pos -= 18
    c.drawString(380, y_pos, "Tax (8%):")
    c.drawString(480, y_pos, "$294.40")
    
    y_pos -= 18
    c.setFont("Helvetica-Bold", 12)
    c.drawString(380, y_pos, "TOTAL:")
    c.drawString(480, y_pos, "$3,974.40")
    
    # Payment info
    y_pos -= 60
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y_pos, "Payment Information:")
    c.setFont("Helvetica", 11)
    y_pos -= 15
    c.drawString(50, y_pos, "Bank: First National Bank")
    y_pos -= 15
    c.drawString(50, y_pos, "Account: 1234567890")
    y_pos -= 15
    c.drawString(50, y_pos, "Routing: 011000015")
    
    # Footer
    c.setFont("Helvetica", 10)
    c.drawString(200, 50, "Thank you for your business!")
    
    c.save()
    print(f"Created test PDF: {filename}")

if __name__ == "__main__":
    create_test_pdf()
