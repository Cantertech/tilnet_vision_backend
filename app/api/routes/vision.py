import base64
import io
import json
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa
from app.agents.vision_agent import vision_graph
from app.agents.document_agent import document_graph
from app.core.supabase import supabase, supabase_admin

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
    print(f"\n[API] Received Scan Request: project_type={project_type}, unit={unit}, user_id={user_id}")
    try:
        # 1. Store file in Supabase Storage
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = f"{user_id}/{timestamp}_{file.filename}"
        file_content = await file.read()
        print(f"[API] Uploading image to storage: {file_path}...")
        
        # Upload using Admin client with upsert enabled
        supabase_admin.storage.from_("scans").upload(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type, "upsert": "true"}
        )
        
        # Get Public URL
        image_url = supabase_admin.storage.from_("scans").get_public_url(file_path)
        print(f"[API] Image live at: {image_url}")
        
        # 2. Run the Agent
        initial_state = {
            "image_url": image_url,
            "project_type": project_type,
            "unit": unit,
            "user_id": user_id,
            "project_name": "New Estimate",
            "raw_text": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "idle",
            "rooms": [],
            "customer": {},
            "labor": {},
            "workers_extracted": [],
            "materials_pref": [],
            "site_notes": [],
            "validation_errors": [],
            "calculated_results": None,
            "processed_rooms": []
        }
        
        print(f"[API] Invoking AI Agent Graph...")
        start_time = datetime.now()
        # Invoke the graph
        final_state = vision_graph.invoke(initial_state)
        duration = (datetime.now() - start_time).total_seconds()
        print(f"[API] Agent Processing Finished in {duration:.2f}s. Final Status: {final_state.get('status', 'unknown')}")
        
        return {
            "success": True,
            "data": {
                "project_name": final_state.get("project_name"),
                "project_type": final_state.get("project_type"),
                "measurement_unit": final_state.get("unit"),
                "estimated_days": final_state.get("calculated_results", {}).get("estimated_days", 3),
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


@router.post("/extract-document")
async def extract_document(
    user_id: str,
    file: UploadFile = File(...)
):
    """
    Dedicated Document Extractor Agent that extracts structured construction estimates 
    exactly as they are from any uploaded sketch, receipt, or document using the 
    LangGraph Document Extraction Flow.
    """
    print(f"\n[API] Document Extraction Request: user_id={user_id}")
    try:
        # 1. Store file in Supabase Storage
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = f"{user_id}/docs/{timestamp}_{file.filename}"
        file_content = await file.read()
        print(f"[API] Uploading doc image to storage: {file_path}...")
        
        # Upload using Admin client with upsert enabled
        supabase_admin.storage.from_("scans").upload(
            path=file_path,
            file=file_content,
            file_options={"content-type": file.content_type, "upsert": "true"}
        )
        
        # Get Public URL
        image_url = supabase_admin.storage.from_("scans").get_public_url(file_path)
        print(f"[API] Doc Image live at: {image_url}")
        
        # 2. Setup and Invoke Document LangGraph Agent Flow
        initial_state = {
            "image_url": image_url,
            "user_id": user_id,
            "raw_json_text": "",
            "extracted_data": None,
            "status": "idle",
            "errors": []
        }
        
        print(f"[API] Invoking Document Extractor Agent Graph...")
        start_time = datetime.now()
        final_state = document_graph.invoke(initial_state)
        duration = (datetime.now() - start_time).total_seconds()
        print(f"[API] Doc Agent Processing Finished in {duration:.2f}s. Status: {final_state.get('status', 'unknown')}")
        
        if final_state.get("status") == "failed" or not final_state.get("extracted_data"):
            raise HTTPException(status_code=500, detail=f"Document extraction failed: {', '.join(final_state.get('errors', []))}")
            
        return {
            "success": True,
            "data": final_state.get("extracted_data")
        }
    except Exception as e:
        print(f"Error in extract_document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-pdf")
async def generate_pdf(payload: dict):
    project_id = payload.get("project_id")
    print(f"[PDF] Generating PDF for Project ID: {project_id}")
    if not project_id or project_id == "undefined":
        raise HTTPException(status_code=400, detail="Valid Project ID is required")

    try:
        # Clean the ID if it's a string
        if isinstance(project_id, str):
            project_id = project_id.strip()

        # --- Apply updates from payload and save to database first ---
        update_payload = {}
        customer_name_payload = payload.get("customer_name")
        contact_payload = payload.get("customer_phone") or payload.get("contact")
        location_payload = payload.get("customer_location") or payload.get("Location") or payload.get("location")
        transport_payload = payload.get("transport")
        cost_per_area_payload = payload.get("cost_per_area")

        if customer_name_payload is not None:
            update_payload["customer_name"] = customer_name_payload
        if contact_payload is not None:
            update_payload["customer_phone"] = contact_payload
        if location_payload is not None:
            update_payload["customer_location"] = location_payload
        if transport_payload is not None:
            try:
                update_payload["transport"] = float(transport_payload)
            except (ValueError, TypeError):
                pass
        if cost_per_area_payload is not None:
            try:
                update_payload["cost_per_area"] = float(cost_per_area_payload)
            except (ValueError, TypeError):
                pass

        if update_payload:
            print(f"[PDF] Applying database pre-updates: {update_payload}")
            supabase_admin.table("projects").update(update_payload).eq("id", project_id).execute()

        # 1. Fetch Project Data from Supabase
        print(f"[PDF] Fetching project {project_id}...")
        response = supabase_admin.table("projects").select("*").eq("id", project_id).execute()
        
        if not response.data or len(response.data) == 0:
            print(f"[PDF] Error: Project {project_id} not found.")
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found.")
            
        project = response.data[0]
        print(f"[PDF] Found project: {project.get('name')}")

        # Rooms
        r_res = supabase_admin.from_("rooms").select("*").eq("project_id", project_id).execute()
        rooms = r_res.data or []
        print(f"[PDF] Found {len(rooms)} rooms")
        
        # Materials
        m_res = supabase_admin.from_("project_materials").select("*").eq("project_id", project_id).execute()
        materials = m_res.data or []
        print(f"[PDF] Found {len(materials)} materials")

        # 2. Fetch User Profile for Branding
        u_res = supabase_admin.from_("profiles").select("*").eq("id", project["user_id"]).execute()
        profile = u_res.data[0] if u_res.data else {}

        # 3. Prepare Context for Template
        # Calculate wastage factor
        wastage_factor = 1 + (float(project.get("wastage_percentage") or 10) / 100)
        
        # Calculate floor_area_with_waste and wall_area_with_waste for each room
        for room in rooms:
            # Floor area
            base_floor = float(room.get("area") or room.get("floor_area") or 0)
            if base_floor == 0 and room.get("length") and room.get("breadth"):
                base_floor = float(room.get("length")) * float(room.get("breadth"))
            
            room["floor_area_with_waste"] = base_floor * wastage_factor
            
            # Wall area
            base_wall = float(room.get("wall_area") or 0)
            if base_wall == 0 and room.get("length") and room.get("breadth") and room.get("height"):
                base_wall = 2 * (float(room.get("length")) + float(room.get("breadth"))) * float(room.get("height"))
            
            room["wall_area_with_waste"] = base_wall * wastage_factor

        # Check if we have custom extracted tables from Image to PDF wizard
        custom_doc = payload.get("custom_doc")
        custom_tables = None
        
        if custom_doc:
            print("[PDF] Custom Doc found in payload. Overriding fields for Image-To-PDF rendering.")
            custom_tables = custom_doc.get("tables", [])
            customer_name = custom_doc.get("customer_name") or project.get("customer_name") or "N/A"
            location = custom_doc.get("subtitle") or project.get("customer_location") or "N/A"
            contact = custom_doc.get("customer_phone") or project.get("customer_phone") or "N/A"
            total_material_cost = float(custom_doc.get("summary", {}).get("total_material_cost") or 0)
            total_labor_cost = float(custom_doc.get("summary", {}).get("total_labor_cost") or 0)
            transport = float(custom_doc.get("summary", {}).get("transport") or 0)
            profit = 0.0
            grand_total = float(custom_doc.get("summary", {}).get("grand_total") or 0)
            description = custom_doc.get("summary", {}).get("notes") or ""
            rooms = []
            materials = []
            total_area = 0.0
        else:
            customer_name = project.get("customer_name") or "N/A"
            location = project.get("customer_location") or project.get("location") or "N/A"
            contact = project.get("customer_phone") or "N/A"
            total_material_cost = sum(float(m.get("price") or 0) * float(m.get("quantity") or 0) for m in materials)
            total_labor_cost_raw = float(project.get("total_labor_cost") or 0)
            transport = float(project.get("transport") or 0)
            profit_raw = float(project.get("profit") or 0)
            total_labor_cost = total_labor_cost_raw + profit_raw
            profit = 0.0
            grand_total = total_material_cost + total_labor_cost + transport + profit
            total_area = float(project.get("total_area_with_waste") or 0)
            description = project.get("description", "")

        context = {
            "project_name": project.get("name"),
            "estimate_number": project.get("estimate_number", f"EST-{str(project_id)[:6]}"),
            "project_date": project.get("created_at")[:10] if project.get("created_at") else datetime.now().strftime("%Y-%m-%d"),
            "customer_name": customer_name,
            "location": location,
            "contact": contact,
            "rooms": rooms,
            "materials": materials,
            "custom_tables": custom_tables,
            "total_material_cost": total_material_cost,
            "total_labor_cost": total_labor_cost,
            "transport": transport,
            "profit": profit,
            "grand_total": grand_total,
            "total_area": total_area,
            "estimated_days": project.get("estimated_days", 3),
            "cost_per_area": grand_total / total_area if total_area > 0 else 0,
            "primary_color": profile.get("pdf_color") or "#007bff",
            "user_profile": profile,
            "user_info": {
                "phone_number": profile.get("phone_number", "0502560760"),
                "email": profile.get("email", "N/A")
            },
            "description": description
        }

        # 4. Render and Generate PDF using WeasyPrint
        import io
        import base64
        from jinja2 import Environment, FileSystemLoader
        from weasyprint import HTML
        import os

        template_path = os.path.join(os.getcwd(), "app", "templates")
        jinja_env = Environment(loader=FileSystemLoader(template_path))
        
        # Register custom filters
        def format_currency(value):
            try:
                return f"{float(value or 0):,.2f}"
            except (ValueError, TypeError):
                return value
        jinja_env.filters['format_currency'] = format_currency
        
        template = jinja_env.get_template("pdf_template.html")
        html_content = template.render(context)

        # Generate PDF bytes
        pdf_bytes = HTML(string=html_content).write_pdf()

        # Convert to Base64
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        
        return {
            "success": True,
            "pdf_base64": pdf_base64,
            "filename": f"Estimate_{project.get('name')}.pdf"
        }

    except Exception as e:
        import traceback
        error_msg = f"PDF Error: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/save-project")
async def save_project(payload: dict):
    """
    Saves the finalized estimate to Supabase.
    """
    try:
        user_id = payload.get("user_id")
        project_data = payload.get("project_data", {})
        p_id = payload.get("project_id")
        
        if not p_id and (not user_id or not project_data):
            raise HTTPException(status_code=400, detail="Missing user_id or project_data")

        # 1. Prepare Update/Insert Data
        # If it's a partial update of client info from ProjectsPage
        if p_id and not project_data:
            project_update = {}
            if "customer_name" in payload: project_update["customer_name"] = payload["customer_name"]
            if "customer_phone" in payload: project_update["customer_phone"] = payload["customer_phone"]
            if "customer_location" in payload: project_update["customer_location"] = payload["customer_location"]
            
            res = supabase_admin.from_("projects").update(project_update).eq("id", p_id).execute()
            return {"success": True, "project_id": p_id}

        # 1. Save Project Header
        # Rooms and materials might be nested inside "results"
        results = project_data.get("results", {})
        materials = project_data.get("materials") or results.get("materials", [])
        rooms = project_data.get("rooms") or results.get("rooms", [])
        
        # Calculate derived totals if not present
        total_mat = sum(float(m.get("price") or 0) * float(m.get("quantity") or m.get("quantity_with_wastage") or 0) for m in materials)
        total_labor = float(project_data.get("total_labor_cost") or results.get("total_labor_cost") or 0)
        
        project_insert = {
            "user_id": user_id,
            "name": project_data.get("project_name", "New Project"),
            "project_type": project_data.get("project_type", "tiling"),
            "measurement_unit": project_data.get("measurement_unit", "meters"),
            "customer_name": project_data.get("customer_name") or project_data.get("customer", {}).get("name"),
            "customer_phone": project_data.get("customer_phone") or project_data.get("customer", {}).get("phone"),
            "customer_location": project_data.get("customer_location") or project_data.get("customer", {}).get("location"),
            "total_area_with_waste": project_data.get("total_area") or project_data.get("results", {}).get("total_area_with_waste"),
            "total_labor_cost": total_labor,
            "cost_per_area": project_data.get("cost_per_area") or project_data.get("results", {}).get("cost_per_area"),
            "transport": project_data.get("transport", 0),
            "profit": project_data.get("profit", 0) or project_data.get("results", {}).get("profit", 0),
            "wastage_percentage": project_data.get("wastage_percentage", 10),
            "estimated_days": project_data.get("estimated_days", 3),
            "created_at": datetime.now().isoformat()
        }

        # Check if project already exists (if we have an ID)
        p_id = payload.get("project_id")
        if p_id:
            res = supabase_admin.from_("projects").update(project_insert).eq("id", p_id).execute()
        else:
            res = supabase_admin.from_("projects").insert(project_insert).execute()
        
        if not res.data:
            raise Exception("Failed to save project header")
        
        new_project_id = res.data[0]["id"]

        # 2. Save Rooms
        if rooms:
            if p_id:
                supabase_admin.from_("rooms").delete().eq("project_id", p_id).execute()
            
            room_inserts = []
            for r in rooms:
                # Map floor_area if area is missing
                area_val = float(r.get("area") or r.get("floor_area") or (float(r.get("length") or 0) * float(r.get("breadth") or 0)) or 0)
                room_inserts.append({
                    "project_id": new_project_id,
                    "name": r.get("name"),
                    "length": float(r.get("length") or 0),
                    "breadth": float(r.get("breadth") or 0),
                    "height": float(r.get("height") or 0),
                    "area": area_val,
                    "wall_area": float(r.get("wall_area") or 0)
                })
            supabase_admin.from_("rooms").insert(room_inserts).execute()

        # 3. Save Materials
        if materials:
            if p_id:
                supabase_admin.from_("project_materials").delete().eq("project_id", p_id).execute()
            
            mat_inserts = []
            for m in materials:
                # Map quantity_with_wastage if quantity is missing
                qty_val = float(m.get("quantity") or m.get("quantity_with_wastage") or 0)
                mat_inserts.append({
                    "project_id": new_project_id,
                    "name": m.get("name") or m.get("material", {}).get("name"),
                    "quantity": qty_val,
                    "unit": m.get("unit"),
                    "price": float(m.get("price") or 0)
                })
            supabase_admin.from_("project_materials").insert(mat_inserts).execute()

        return {
            "success": True,
            "project_id": new_project_id,
            "message": "Project saved successfully"
        }

    except Exception as e:
        print(f"Save Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

