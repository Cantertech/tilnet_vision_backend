from typing import TypedDict, List, Optional, Any
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from datetime import datetime
import json
from app.core.config import settings

# --- AGENT STATE FOR DOCUMENT EXTRACTION ---
class DocumentAgentState(TypedDict):
    image_url: str
    user_id: str
    raw_json_text: str
    extracted_data: Optional[dict]
    status: str
    errors: List[str]

# Initialize Specialists
# Node 1: Vision Specialist (Gemini 2.5 Flash)
vision_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    google_api_key=settings.GOOGLE_API_KEY
)

# Node 2: Math Specialist (GPT-4o Mini)
math_llm = ChatOpenAI(
    model="gpt-4o-mini", 
    openai_api_key=settings.OPENAI_API_KEY
)

# --- NODE 1: VISION DOCUMENT EXTRACTION ---
def vision_document_node(state: DocumentAgentState):
    """
    Invokes Gemini 2.5 Flash to extract tabular estimate details from the document image.
    """
    print(f"\n[DOCUMENT AGENT] NODE 1: Running Vision Document Extraction...")
    
    prompt = """You are a professional estimator and data architect.
Your task is to analyze the uploaded construction sheet, estimate, or receipt image, and extract ALL tables, items, and financial values.

🎯 JSON SCHEMA TO RETURN:
{
  "title": "string - title of the estimate or invoice (e.g., 'Tiling Estimate')",
  "subtitle": "string - customer name, date, or invoice details",
  "tables": [
    {
      "table_title": "string - name/category (e.g., 'Material Quantities', 'Workmanship', 'Ground Floor')",
      "table_description": "string - short description if any",
      "table_headers": ["string", "string", ...],
      "items": [
        {
          "name": "string - item/activity name exactly as written",
          "quantity": "number or null - qty value",
          "unit": "string - unit (e.g., 'bags', 'm2', 'pcs', 'days')",
          "unit_price": "number or null - price per unit",
          "total_price": "number or null - total cost (if blank, multiply qty * unit_price)"
        }
      ],
      "subtotal": "number or null - sum of all total_prices in this table"
    }
  ],
  "summary": {
    "total_material_cost": "number or null - sum of material items",
    "total_labor_cost": "number or null - sum of workmanship/labor items",
    "grand_total": "number or null - final estimate total",
    "notes": "string - special instructions or notes"
  }
}

🧠 CRITICAL RULES:
1. Capture all lines and values exactly as written.
2. Group items into distinct tables if the image uses separate sections or lists.
3. If total prices or subtotals are missing, calculate them!
4. Return ONLY the strict raw JSON. No explanation text, no markdown wrappers, no code blocks.
"""

    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": state["image_url"]},
        ]
    )
    
    try:
        response = vision_llm.invoke([message])
        raw_text = response.content.strip()
        
        # Clean markdown code blocks if any
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
        return {
            "raw_json_text": raw_text,
            "status": "extracted"
        }
    except Exception as e:
        print(f"[DOCUMENT AGENT] Node 1 Failed: {str(e)}")
        return {
            "raw_json_text": "",
            "status": "failed",
            "errors": [f"Vision Extraction Node Error: {str(e)}"]
        }

# --- NODE 2: MATHEMATICAL VALIDATION & CALCULATIONS RECONCILIATION ---
def mathematical_validation_node(state: DocumentAgentState):
    """
    Uses GPT-4o Mini to double-check and correct all mathematical subtotals, item multiplications, 
    and grand totals to guarantee absolute precision.
    """
    print(f"[DOCUMENT AGENT] NODE 2: Reconciling calculations and validating schema...")
    
    if state["status"] == "failed" or not state["raw_json_text"]:
        return {"status": "failed"}
        
    prompt = f"""You are a master mathematical validation system. 
You will take the raw JSON estimate extraction below, review every single calculation, correct any mathematical errors, and return a clean, validated JSON object.

RAW ESTIMATE JSON:
{state['raw_json_text']}

VALIDATION STEPS:
1. For each item in every table:
   - Ensure quantity, unit_price, and total_price are numeric.
   - Verify that quantity * unit_price equals total_price. If they do not match, or if total_price is missing, set total_price = quantity * unit_price.
2. For each table:
   - Calculate the subtotal as the precise sum of all its item total_price values. Set this as 'subtotal'.
3. For the overall summary:
   - Ensure total_material_cost, total_labor_cost, and grand_total are precisely correct based on the items listed.
   - If an item's table is named 'Labor', 'Workmanship' or matches labor intent, assign its sum to 'total_labor_cost'. Otherwise, assign to 'total_material_cost'.
   - Ensure grand_total = total_material_cost + total_labor_cost.

Return ONLY the final, validated JSON matching the schema of the input. No explanation, no comments, no markdown code blocks.
"""

    try:
        response = math_llm.invoke(prompt)
        text_out = response.content.strip()
        
        if "```json" in text_out:
            text_out = text_out.split("```json")[1].split("```")[0].strip()
        elif "```" in text_out:
            text_out = text_out.split("```")[1].split("```")[0].strip()
            
        parsed_data = json.loads(text_out)
        return {
            "extracted_data": parsed_data,
            "status": "completed"
        }
    except Exception as e:
        print(f"[DOCUMENT AGENT] Node 2 Failed: {str(e)}")
        # Fallback to direct raw json parsing
        try:
            parsed_data = json.loads(state["raw_json_text"])
            return {
                "extracted_data": parsed_data,
                "status": "completed_fallback",
                "errors": [f"Math Validation failed, fallback to raw parsing: {str(e)}"]
            }
        except Exception as fallback_err:
            return {
                "status": "failed",
                "errors": state.get("errors", []) + [f"Math Validation Node Error: {str(e)}", f"Fallback parsing failed: {str(fallback_err)}"]
            }

# --- DEFINE LANGGRAPH FLOW ---
workflow = StateGraph(DocumentAgentState)

# Add Nodes
workflow.add_node("vision_extraction", vision_document_node)
workflow.add_node("mathematical_validation", mathematical_validation_node)

# Add Edges
workflow.set_entry_point("vision_extraction")
workflow.add_edge("vision_extraction", "mathematical_validation")
workflow.add_edge("mathematical_validation", END)

# Compile Graph
document_graph = workflow.compile()
