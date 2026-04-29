
import io
import base64
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

def generate_pdf_reportlab(project, rooms, materials, profile):
    """
    Generates a professional PDF estimate using ReportLab.
    Ported from the user's previous working implementation.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    # Styles
    styles = getSampleStyleSheet()
    # Safe Color Parsing
    color_hex = profile.get("pdf_color") or "#007bff"
    if not str(color_hex).startswith("#"):
        color_hex = f"#{color_hex}"
    
    try:
        primary_color = colors.HexColor(color_hex)
    except:
        primary_color = colors.HexColor("#007bff")
    
    title_style = ParagraphStyle(
        'ProjectTitle',
        parent=styles['Title'],
        textColor=primary_color,
        fontSize=24,
        alignment=1, # Center
        spaceAfter=20
    )
    
    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=primary_color,
        spaceBefore=15,
        spaceAfter=10,
        borderPadding=(2, 0, 2, 5),
        borderWidth=0,
        leftIndent=0
    )

    normal_style = styles['Normal']
    label_style = ParagraphStyle('Label', parent=normal_style, fontName='Helvetica-Bold')

    elements = []

    # 1. Header
    try:
        company_name = profile.get("company_name") or "Tilnet Contractor"
        elements.append(Paragraph(str(company_name).upper(), title_style))
        elements.append(Paragraph("Project Estimate Report", ParagraphStyle('Sub', parent=styles['Normal'], alignment=1, fontSize=10, textColor=colors.grey)))
        elements.append(Spacer(1, 0.2*inch))
    except Exception as e:
        print(f"Header Error: {e}")

    # 2. Project Info Table
    try:
        date_str = project.get("created_at", "").split("T")[0] if project.get("created_at") else datetime.now().strftime("%Y-%m-%d")
        header_data = [
            [Paragraph(f"<b>Estimate #:</b> EST-{str(project.get('id', '000'))[:6]}", normal_style), 
             Paragraph(f"<b>Date:</b> {date_str}", normal_style)],
            [Paragraph(f"<b>Project:</b> {project.get('name', 'New Project')}", normal_style),
             Paragraph(f"<b>Client:</b> {project.get('customer_name', 'N/A')}", normal_style)]
        ]
        header_table = Table(header_data, colWidths=[doc.width/2.0]*2)
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 0.3*inch))
    except Exception as e:
        print(f"Info Table Error: {e}")

    # 3. Measurements
    if rooms:
        try:
            elements.append(Paragraph("Measurement Details", section_style))
            room_header = ["Room Name", "Dimensions", "Floor Area", "Wall Area"]
            room_data = [room_header]
            for r in rooms:
                # Use 'area' which is what's in the DB
                f_area = float(r.get('area') or r.get('floor_area_with_waste') or 0)
                w_area = float(r.get('wall_area') or r.get('wall_area_with_waste') or 0)
                room_data.append([
                    str(r.get("name", "Room")),
                    f"{r.get('length', 0)}m x {r.get('breadth', 0)}m",
                    f"{f_area:.2f} m²",
                    f"{w_area:.2f} m²"
                ])
            
            # Total Area Row
            total_area = float(project.get("total_area_with_waste") or 0)
            room_data.append(["TOTAL PROJECT AREA", "", "", f"{total_area:.2f} m²"])

            room_table = Table(room_data, colWidths=[doc.width*0.3, doc.width*0.3, doc.width*0.2, doc.width*0.2])
            room_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ]))
            elements.append(room_table)
        except Exception as e:
            print(f"Rooms Error: {e}")

    # 4. Materials
    total_mat_cost = 0
    if materials:
        try:
            elements.append(Paragraph("Material Requirements", section_style))
            mat_header = ["#", "Material Item", "Quantity", "Unit", "Price Est."]
            mat_data = [mat_header]
            for i, m in enumerate(materials, 1):
                qty = float(m.get("quantity") or 0)
                price = float(m.get("price") or 0)
                line_total = qty * price
                total_mat_cost += line_total
                mat_data.append([
                    str(i),
                    str(m.get("name", "Material")),
                    f"{qty:.2f}",
                    str(m.get("unit", "pcs")),
                    f"₵{price:,.2f}"
                ])
            
            mat_table = Table(mat_data, colWidths=[doc.width*0.1, doc.width*0.4, doc.width*0.15, doc.width*0.15, doc.width*0.2])
            mat_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f8f9fa")),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(mat_table)
        except Exception as e:
            print(f"Materials Error: {e}")

    # 5. Financial Summary
    try:
        elements.append(Paragraph("Financial Summary", section_style))
        labor = float(project.get("total_labor_cost") or 0)
        profit = float(project.get("profit") or 0)
        transport = float(project.get("transport") or 0)
        grand_total = total_mat_cost + labor + profit + transport

        summary_data = [
            ["Material Subtotal:", f"₵{total_mat_cost:,.2f}"],
            ["Labor Cost:", f"₵{labor:,.2f}"],
            ["Transport & Logistics:", f"₵{transport:,.2f}"],
            ["Profit Margin:", f"₵{profit:,.2f}"],
            [Paragraph("<b>GRAND TOTAL ESTIMATE</b>", normal_style), Paragraph(f"<b>₵{grand_total:,.2f}</b>", ParagraphStyle('Total', parent=normal_style, fontSize=14, textColor=primary_color))]
        ]
        summary_table = Table(summary_data, colWidths=[doc.width*0.6, doc.width*0.4])
        summary_table.setStyle(TableStyle([
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('LINEABOVE', (0, -1), (-1, -1), 1, primary_color),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(summary_table)
    except Exception as e:
        print(f"Summary Error: {e}")

    # 6. Footer / Signatures
    try:
        elements.append(Spacer(1, 0.5*inch))
        sig_data = [
            ["________________________", "________________________"],
            ["Customer Signature", f"{company_name} Representative"]
        ]
        sig_table = Table(sig_data, colWidths=[doc.width/2.0]*2)
        sig_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 30),
        ]))
        elements.append(sig_table)

        elements.append(Spacer(1, 0.5*inch))
        elements.append(Paragraph("<i>Thank you for choosing Tilnet. This estimate is valid for 30 days.</i>", ParagraphStyle('Footer', parent=styles['Normal'], alignment=1, fontSize=8, textColor=colors.grey)))
    except Exception as e:
        print(f"Footer Error: {e}")

    # Build
    doc.build(elements)
    pdf_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    buffer.close()
    return pdf_base64
