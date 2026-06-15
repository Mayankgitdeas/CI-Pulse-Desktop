"""pdf_generator.py — PDF export using fpdf2 (pure Python, Windows-friendly)"""
from datetime import datetime
from fpdf import FPDF


def generate_signals_pdf(signals, title="Cognizant CI Pulse"):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    
    # Header
    pdf.set_fill_color(13, 18, 67)  # Navy
    pdf.rect(0, 0, 210, 28, 'F')
    pdf.set_xy(15, 8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, title, ln=True)
    pdf.set_xy(15, 18)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(200, 200, 200)
    pdf.cell(0, 5, f"Competitive Intelligence Report  -  Generated {datetime.now().strftime('%B %d, %Y')}", ln=True)
    
    pdf.set_y(36)
    pdf.set_text_color(15, 23, 42)
    
    for sig in signals:
        # Check space, add page if needed
        if pdf.get_y() > 250:
            pdf.add_page()
        
        comp = sig.get("competitor", "")
        date = (sig.get("published_at") or "").split("T")[0]
        impact = {"hi": "HIGH", "med": "MEDIUM", "lo": "LOW"}.get(sig.get("impact"), "MEDIUM")
        
        # Competitor + date + impact line
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(0, 87, 184)  # Blue
        pdf.cell(0, 6, f"{comp}  |  {date}  |  {impact} IMPACT", ln=True)
        
        # Headline
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(13, 18, 67)
        pdf.multi_cell(0, 6, _clean(sig.get("headline", "")))
        pdf.ln(1)
        
        # Description
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 5, _clean(sig.get("description", "")))
        pdf.ln(1)
        
        # Industry + topics
        industry = sig.get("industry", "")
        topics = sig.get("topics", [])
        if industry or topics:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 116, 139)
            tags = []
            if industry:
                tags.append(f"Industry: {industry}")
            if topics:
                tags.append(f"Topics: {', '.join(topics)}")
            pdf.multi_cell(0, 5, _clean("  |  ".join(tags)))
            pdf.ln(1)
        
        # Analyst review
        review = sig.get("analyst_review")
        if review:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(0, 100, 100)
            pdf.cell(0, 5, "Analyst Review:", ln=True)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(51, 65, 85)
            pdf.multi_cell(0, 5, _clean(review))
        
        # Source
        source = sig.get("source", "")
        if source:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(148, 163, 184)
            pdf.cell(0, 5, f"Source: {_clean(source)}", ln=True)
        
        # Separator
        pdf.ln(2)
        pdf.set_draw_color(226, 232, 240)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)
    
    return bytes(pdf.output())


def _clean(text):
    """Remove characters fpdf can't encode (latin-1 only)."""
    if not text:
        return ""
    return str(text).encode("latin-1", "replace").decode("latin-1")
