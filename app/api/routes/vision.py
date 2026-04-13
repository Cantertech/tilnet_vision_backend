import base64
import io
import json
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa
from app.agents.vision_agent import vision_graph
from app.core.supabase import supabase

router = APIRouter()

# Setup Jinja2 environment
# Adjusting to look for templates in the backend/app/templates directory
templates_dir = "app/templates"
jinja_env = Environment(loader=FileSystemLoader(templates_dir))

# Custom filters for Jinja2
def format_currency(value, decimals=2):
    try:
        return f"{float(value or 0):,.{decimals}f}"
    except (ValueError, TypeError):
        return value

jinja_env.filters['format_currency'] = format_currency

@router.post("/process-estimate")
async def process_estimate(
    project_type: str,
    unit: str,
    user_id: str,
    file: UploadFile = File(...)
):
    """
    Endpoint to receive an image and run the LangGraph Vision Agent.
    """
    try:
        # 1. Store file in Supabase Storage
        file_path = f"{user_id}/{file.filename}"
        file_content = await file.read()
        
        # Upload
        supabase.storage.from_("scans").upload(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
        
        # Get Public URL (Ensure the bucket 'scans' is public)
        image_url = supabase.storage.from_("scans").get_public_url(file_path)
        
        # 2. Run the Agent
        initial_state = {
            "image_url": image_url,
            "project_type": project_type,
            "unit": unit,
            "user_id": user_id,
            "status": "idle",
            "rooms": [],
            "customer": {},
            "labor": {},
            "materials_pref": [],
            "site_notes": [],
            "validation_errors": []
        }
        
        # Invoke the graph
        final_state = vision_graph.invoke(initial_state)
        
        return {
            "success": True,
            "data": {
                "project_name": final_state.get("project_name"),
                "date": final_state.get("date"),
                "customer": final_state.get("customer"),
                "results": final_state.get("calculated_results")
            },
            "status": final_state["status"],
            "errors": final_state.get("validation_errors", [])
        }
    except Exception as e:
        print(f"Error in process_estimate: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-pdf")
async def generate_pdf(payload: dict):
    project_id = payload.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Project ID is required")

    try:
        # 1. Fetch Project Data from Supabase
        # Project
        p_res = supabase.from_("projects").select("*").eq("id", project_id).single().execute()
        project = p_res.data
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Rooms
        r_res = supabase.from_("rooms").select("*").eq("project_id", project_id).execute()
        rooms = r_res.data

        # Materials
        m_res = supabase.from_("project_materials").select("*, material(*)").eq("project_id", project_id).execute()
        materials = m_res.data

        # 2. Fetch User Profile for Branding
        u_res = supabase.from_("profiles").select("*").eq("id", project["user_id"]).single().execute()
        profile = u_res.data or {}

        # 3. Prepare Context for Template
        # Calculate subtotal and grand total
        total_material_cost = sum(m.get("price", 0) * m.get("quantity", 0) for m in materials)
        total_labor_cost = project.get("total_labor_cost", 0)
        transport = project.get("transport", 0)
        profit = project.get("profit", 0)
        subtotal = total_material_cost + total_labor_cost
        grand_total = subtotal + transport + profit

        context = {
            "project_name": project.get("name"),
            "estimate_number": project.get("estimate_number", f"EST-{project_id[:6]}"),
            "project_date": project.get("created_at")[:10] if project.get("created_at") else datetime.now().strftime("%Y-%m-%d"),
            "customer_name": project.get("customer_name"),
            "location": project.get("customer_location") or project.get("location"),
            "contact": project.get("customer_phone"),
            "rooms": rooms,
            "materials": materials,
            "total_material_cost": total_material_cost,
            "total_labor_cost": total_labor_cost,
            "transport": transport,
            "profit": profit,
            "subtotal_cost": subtotal,
            "grand_total": grand_total,
            "total_area": project.get("total_area_with_waste", 0),
            "wastage_percentage": project.get("wastage_percentage", 10),
            "estimated_days": project.get("estimated_days", 3),
            "cost_per_area": project.get("cost_per_area", 0),
            "primary_color": profile.get("pdf_color", "#007bff"),
            "user_profile": {
                "company_name": profile.get("company_name") or "Tilnet Contractor",
                "address": profile.get("address") or "Business Address Not Set"
            },
            "user_info": {"phone_number": profile.get("phone_number") or "0502560760"},
            "description": project.get("description", "")
        }

        # 3. Render Template
        template = jinja_env.get_template("pdf_template.html")
        html_content = template.render(context)

        # 4. Generate PDF
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)

        if pisa_status.err:
            raise HTTPException(status_code=500, detail="PDF generation failed")

        # 5. Return Base64
        pdf_base64 = base64.b64encode(pdf_buffer.getvalue()).decode("utf-8")
        
        return {
            "success": True,
            "pdf_base64": pdf_base64,
            "filename": f"Estimate_{project.get('name')}.pdf"
        }

    except Exception as e:
        print(f"PDF Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/save-project")
async def save_project(payload: dict):
    """
    Saves the finalized estimate to Supabase.
    """
    try:
        user_id = payload.get("user_id")
        project_data = payload.get("project_data")
        
        if not user_id or not project_data:
            raise HTTPException(status_code=400, detail="Missing user_id or project_data")

        # 1. Save Project Header
        materials = project_data.get("materials", [])
        rooms = project_data.get("rooms", [])
        
        # Calculate derived totals if not present
        total_mat = sum(m.get("price", 0) * m.get("quantity", 0) for m in materials)
        total_labor = project_data.get("total_labor_cost", 0)
        
        project_insert = {
            "user_id": user_id,
            "name": project_data.get("project_name", "New Project"),
            "customer_name": project_data.get("customer_name") or project_data.get("customer", {}).get("name"),
            "customer_phone": project_data.get("customer_phone") or project_data.get("customer", {}).get("phone"),
            "customer_location": project_data.get("customer_location") or project_data.get("customer", {}).get("location"),
            "total_area_with_waste": project_data.get("total_area"),
            "total_labor_cost": total_labor,
            "cost_per_area": project_data.get("cost_per_area"),
            "transport": project_data.get("transport", 0),
            "profit": project_data.get("profit", 0),
            "wastage_percentage": project_data.get("wastage_percentage", 10),
            "estimated_days": project_data.get("estimated_days", 3),
            "created_at": datetime.now().isoformat()
        }

        # Check if project already exists (if we have an ID)
        p_id = payload.get("project_id")
        if p_id:
            res = supabase.from_("projects").update(project_insert).eq("id", p_id).execute()
        else:
            res = supabase.from_("projects").insert(project_insert).execute()
        
        if not res.data:
            raise Exception("Failed to save project header")
        
        new_project_id = res.data[0]["id"]

        # 2. Save Rooms
        if rooms:
            if p_id:
                supabase.from_("rooms").delete().eq("project_id", p_id).execute()
            
            room_inserts = []
            for r in rooms:
                room_inserts.append({
                    "project_id": new_project_id,
                    "name": r.get("name"),
                    "length": r.get("length"),
                    "breadth": r.get("breadth"),
                    "area": r.get("area")
                })
            supabase.from_("rooms").insert(room_inserts).execute()

        # 3. Save Materials
        if materials:
            if p_id:
                supabase.from_("project_materials").delete().eq("project_id", p_id).execute()
            
            mat_inserts = []
            for m in materials:
                mat_inserts.append({
                    "project_id": new_project_id,
                    "name": m.get("name") or m.get("material", {}).get("name"),
                    "quantity": m.get("quantity"),
                    "unit": m.get("unit"),
                    "price": m.get("price")
                })
            supabase.from_("project_materials").insert(mat_inserts).execute()

        return {
            "success": True,
            "project_id": new_project_id,
            "message": "Project saved successfully"
        }

    except Exception as e:
        print(f"Save Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

