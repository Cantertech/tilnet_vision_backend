from typing import TypedDict, List, Optional, Any
from decimal import Decimal
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from datetime import datetime
from app.core.config import settings

# --- SYSTEM MATERIALS FOR MAPPING ---
SYSTEM_MATERIALS = [
    {"name": "Cement", "base_price": 95, "unit": "bags"},
    {"name": "Sand", "base_price": 350, "unit": "trip"},
    {"name": "Tile Adhesive", "base_price": 55, "unit": "bags"},
    {"name": "Grout", "base_price": 15, "unit": "packets"}
]

# --- ENHANCED AGENT STATE ---
class AgentState(TypedDict):
    # Input
    image_url: str
    project_type: str
    unit: str
    user_id: str  # Added for Supabase persistence
    
    # Metadata
    project_name: str
    date: str
    
    # Node 1: Raw Comprehensive Extraction
    raw_text: str
    
    # Node 2: Structured Entities
    rooms: List[dict]
    customer: dict
    labor: dict
    materials_pref: List[str]
    site_notes: List[str]
    workers_extracted: List[dict] # Added to match Node 2 output
    
    # Process metadata
    status: str
    validation_errors: List[str]
    
    # Math Engine Outputs
    calculated_results: Optional[dict]
    processed_rooms: List[dict] # Added to store detailed per-room math

# Initialize Models
# Node 1: Vision Specialist (Gemini 1.5 Flash)
vision_llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash", 
    google_api_key=settings.GOOGLE_API_KEY
)

# Nodes 2-5: Logic & JSON Specialist (GPT-4o Mini)
reasoning_llm = ChatOpenAI(
    model="gpt-4o-mini", 
    openai_api_key=settings.OPENAI_API_KEY
)

# --- NODE 1: COMPREHENSIVE VISION EXTRACTION ---
def vision_extraction_node(state: AgentState):
    """
    Extracts every piece of information visible in the image(s).
    Treats the image as a full project brief (Dimensions, Labor, Client, Materials).
    """
    print("--- NODE 1: COMPREHENSIVE VISION EXTRACTION ---")
    
    prompt = f"""
    You are a Senior Tiling & Construction Expert. 
    Analyze this site note/image for a {state['project_type']} project. 
    Measurement Unit context: {state['unit']}
    
    EXTRACT EVERYTHING YOU SEE. Do not skip details. 
    Categorize your extraction into:
    1. CUSTOMER: Name, location, phone numbers, or address.
    2. ROOMS & DIMENSIONS: Every room with Length, Breadth, and Height (e.g. 'Hall 10x8'). 
       IMPORTANT: If a total area is provided (e.g. 'Hall 20sqm'), extract it specifically.
    3. LABOR & WORKERS: Any mention of worker counts, labor charges (e.g. 'Labour 2000' or 'Rate ₵35/m2').
    4. MATERIALS: Specific tiles, cement, weights, or prices mentioned.
    5. SPECIAL NOTES: Dates, deadlines, or site conditions (e.g. 'upstairs').
    
    Return the information clearly grouped so it can be structured in the next phase.
    """
    
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": state["image_url"]},
        ]
    )
    
    response = vision_llm.invoke([message])
    
    return {
        "raw_text": response.content,
        "status": "fully_extracted"
    }

# --- NODE 2: ENTITY STRUCTURING & SEMANTIC CLEANING ---
def semantic_reasoning_node(state: AgentState):
    """
    Parses the raw text into a strict JSON structure.
    Resolves shorthand (Kthn -> Kitchen) and interprets labor intent.
    """
    print("--- NODE 2: ENTITY STRUCTURING ---")
    
    prompt = f"""
    You are a data architect. Convert the raw notes below into a valid JSON object.
    RAW NOTES: {state['raw_text']}
    PRIMARY UNIT: {state['unit']}
    
    JSON STRUCTURE REQUIREMENTS:
    {{
        "project_name": "Professional Name Here",
        "customer": {{"name": "...", "phone": "...", "location": "..."}},
        "rooms": [{{
            "name": "Kitchen", 
            "length": 0.0, 
            "breadth": 0.0, 
            "height": 0.0,
            "area": 0.0
        }}],
        "labor": {{
            "fixed_total": 0.0, 
            "rate_per_unit": 0.0
        }},
        "workers": [{{
            "role": "Master", 
            "count": 1, 
            "rate": 0, 
            "rate_type": "daily"
        }}],
        "materials": [{{ "name": "...", "qty": 0, "price": 0, "is_system": true }}],
        "notes": []
    }}
    
    RULES:
    1. ROLES: Match workers to System Roles: [Master, Laborer, Painter, Supervisor].
       - If they write 'Assistant' or 'Apprentice', map to 'Laborer'.
    2. GENERATE PROJECT NAME: If missing, create one (e.g. 'Airport Residential Tiling').
    3. MATCH MATERIALS: Compare extracted materials to: {SYSTEM_MATERIALS}. 
       Map to system names if possible and set 'is_system': true.
    4. Resolve common shorthand and extract prices if written.
    5. If a room only has an Area (e.g. '20sqm'), set length/breadth to 0 and set the 'area' field.
    """
    
    response = reasoning_llm.invoke(prompt)
    
    import json
    import re
    
    try:
        json_match = re.search(r'{{.*}}', response.content, re.DOTALL)
        # Using a more robust match for the outermost braces
        json_str = response.content[response.content.find('{'):response.content.rfind('}')+1]
        data = json.loads(json_str)
        
        return {
            "project_name": data.get("project_name", "New Estimate"),
            "rooms": data.get("rooms", []),
            "customer": data.get("customer", {}),
            "labor": data.get("labor", {}),
            "workers_extracted": data.get("workers", []), # Store extracted worker list
            "materials_pref": data.get("materials", []),
            "site_notes": data.get("notes", []),
            "status": "fully_structured"
        }
    except Exception as e:
        print(f"Error parsing entity JSON: {e}")
        return {"status": "structure_failed", "validation_errors": [str(e)]}

# --- NODE 3: VALIDATION & SANITY CHECKS ---
def validation_node(state: AgentState):
    """
    Performs critical sanity checks to prevent 'Hallucination' math.
    """
    print("--- NODE 3: VALIDATION ---")
    errors = []
    
    rooms = state.get("rooms", [])
    if not rooms:
        errors.append("No rooms detected in image. Please ensure dimensions are clear.")
    
    for room in rooms:
        name = room.get("name", "Unknown Room")
        l, b = float(room.get("length", 0)), float(room.get("breadth", 0))
        area = float(room.get("area", 0))
        
        # 1. Check for usable area
        if area <= 0 and (l <= 0 or b <= 0):
            errors.append(f"Room '{name}' has invalid dimensions/area.")
            
        # 2. Check for physical outliers (Residential safety check)
        # If any single room is > 500m2, it's likely a unit error (cm vs meters)
        calculated_area = area if area > 0 else (l * b)
        if calculated_area > 500:
            errors.append(f"Room '{name}' seems unusually large ({calculated_area}{state['unit']}²). Check units.")

    # 3. Labor Sanity
    labor = state.get("labor", {})
    if labor.get("fixed_total", 0) < 0:
        errors.append("Negative labor charge detected.")

    status = "validated" if not errors else "validation_warning"
    
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "validation_errors": errors,
        "status": status
    }

from app.services.calculations import CalculationService
from app.schemas.project import ProjectCreate, RoomBase, WorkerBase, ProjectType, MeasurementUnit, ProfitType

# --- NODE 4: THE MATH ENGINE ---
def calculation_node(state: AgentState):
    """
    Invokes the core Python CalculationService using normalized AI data.
    """
    print("--- NODE 4: CALCULATION ---")
    service = CalculationService()
    
    # 1. Map AI Rooms to Schemas
    rooms_for_calc = []
    for r in state.get("rooms", []):
        rooms_for_calc.append(RoomBase(
            name=r.get("name", "Room"),
            length=Decimal(str(r.get("length", 0))),
            breadth=Decimal(str(r.get("breadth", 0))),
            height=Decimal(str(r.get("height", 0)))
        ))
        
    # 2. Map AI Labor to Workers
    extracted_workers = state.get("workers_extracted", [])
    workers_for_calc = []
    
    if extracted_workers:
        for w in extracted_workers:
            workers_for_calc.append(WorkerBase(
                role=w.get("role", "Master").title(),
                count=int(w.get("count", 1) or 1),
                rate=Decimal(str(w.get("rate", 0) or 0)),
                rate_type=w.get("rate_type", "daily")
            ))
    else:
        # Fallback if AI didn't find a list but found a single labor total
        labor_info = state.get("labor", {})
        workers_for_calc.append(WorkerBase(
            role="Master",
            count=1,
            rate=Decimal(str(labor_info.get("fixed_total", 0) or 0))
        ))

    # 3. Build Calculation Payload
    calc_input = ProjectCreate(
        name=state.get("project_name", "AI Estimate"),
        project_type=ProjectType(state["project_type"]),
        measurement_unit=MeasurementUnit(state["unit"]),
        rooms=rooms_for_calc,
        workers=workers_for_calc,
        profit_type=ProfitType.PER_AREA, # Defaulting for Agent
        profit_value=Decimal("0")        # Adjusted by user later
    )

    # 4. Execute Math
    # Pass all materials (including those with user-provided prices/quantities)
    user_materials = state.get("materials_pref", [])
    
    # Note: calculate_project returns (processed_rooms, results)
    processed_rooms, results = service.calculate_project(calc_input, user_materials=user_materials)
    
    return {
        "calculated_results": {
            **results.model_dump(),
            "rooms": [r.model_dump() for r in processed_rooms]
        },
        "processed_rooms": [r.model_dump() for r in processed_rooms],
        "status": "calculated"
    }

from app.core.supabase import supabase

# --- NODE 5: PERSISTENCE (THE COMMITTER) ---
def persistence_node(state: AgentState):
    """
    Saves the final calculated project to Supabase.
    """
    print("--- NODE 5: PERSISTENCE ---")
    
    # 1. Save Main Project
    results = state.get("calculated_results", {})
    project_payload = {
        "name": state.get("project_name", "AI Estimate"),
        "user_id": state.get("user_id"),
        "project_type": state.get("project_type"),
        "measurement_unit": state.get("unit"),
        "total_area_with_waste": results.get("total_area_with_waste", 0),
        "total_labor_cost": results.get("total_labor_cost", 0),
        "profit": results.get("profit", 0),
        "estimated_days": results.get("estimated_days", 0),
        "customer_name": state.get("customer", {}).get("name"),
        "customer_phone": state.get("customer", {}).get("phone"),
        "status": "completed",
        "date": state.get("date")
    }

    try:
        # Insert Project
        proj_res = supabase.table("projects").insert(project_payload).execute()
        project_id = proj_res.data[0]["id"]
        print(f"Project saved with ID: {project_id}")

        # 2. Save Rooms
        rooms_payload = []
        for r in state.get("rooms", []):
            rooms_payload.append({
                "project_id": project_id,
                "name": r.get("name"),
                "length": r.get("length"),
                "breadth": r.get("breadth"),
                "area": r.get("area")
            })
        if rooms_payload:
            supabase.table("rooms").insert(rooms_payload).execute()

        # 3. Save Materials
        mats_payload = []
        for m in results.get("materials", []):
            mats_payload.append({
                "project_id": project_id,
                "name": m.get("name"),
                "quantity": m.get("quantity_with_wastage"),
                "unit": m.get("unit"),
                "price": m.get("price")
            })
        if mats_payload:
            supabase.table("project_materials").insert(mats_payload).execute()

        return {"status": "fully_persisted"}
        
    except Exception as e:
        print(f"Error persisting to Supabase: {e}")
        # Even if DB fails, we have the calculations in state for the response
        return {"status": "persistence_failed", "validation_errors": [str(e)]}

# --- BUILD THE ENHANCED GRAPH ---
workflow = StateGraph(AgentState)

workflow.add_node("extract_raw", vision_extraction_node)
workflow.add_node("structure_entities", semantic_reasoning_node)
workflow.add_node("validate", validation_node)
workflow.add_node("calculate", calculation_node)
workflow.add_node("persist", persistence_node)

workflow.set_entry_point("extract_raw")
workflow.add_edge("extract_raw", "structure_entities")
workflow.add_edge("structure_entities", "validate")
workflow.add_edge("validate", "calculate")
workflow.add_edge("calculate", "persist")
workflow.add_edge("persist", END)

vision_graph = workflow.compile()
