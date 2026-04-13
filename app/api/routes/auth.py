import random
import string
import base64
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase import supabase
from app.core.config import settings
from supabase import create_client

router = APIRouter()

# Initialize Admin Client for password resets
supabase_admin = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

class SendOTPRequest(BaseModel):
    phone_number: str

class VerifyOTPRequest(BaseModel):
    phone_number: str
    otp_code: str
    new_password: str

def generate_otp():
    return "".join(random.choices(string.digits, k=6))

@router.post("/send-otp")
async def send_otp(req: SendOTPRequest):
    otp = generate_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    
    # 1. Save OTP to Supabase using the Admin client to bypass RLS
    try:
        res = supabase_admin.from_("password_resets").insert({
            "phone_number": req.phone_number,
            "otp_code": otp,
            "expires_at": expires_at
        }).execute()
    except Exception as e:
        print(f"DB Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate reset request")

    # 2. Send SMS via Hubtel
    auth_str = f"{settings.HUBTEL_CLIENT_ID}:{settings.HUBTEL_CLIENT_SECRET}"
    encoded_auth = base64.b64encode(auth_str.encode()).decode()
    
    sms_payload = {
        "From": settings.HUBTEL_SENDER_ID,
        "To": req.phone_number,
        "Content": f"Your Tilnet password reset code is: {otp}. It expires in 10 minutes."
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://smsc.hubtel.com/v1/messages/send",
                headers={"Authorization": f"Basic {encoded_auth}"},
                json=sms_payload
            )
            if response.status_code not in [200, 201]:
                print(f"Hubtel Error: {response.text}")
                raise HTTPException(status_code=500, detail="Failed to send SMS")
        except Exception as e:
            print(f"SMS Error: {e}")
            raise HTTPException(status_code=500, detail="Communication error with SMS provider")

    return {"message": "OTP sent successfully"}

@router.post("/reset-password")
async def reset_password(req: VerifyOTPRequest):
    # 1. Verify OTP
    try:
        res = supabase_admin.from_("password_resets") \
            .select("*") \
            .eq("phone_number", req.phone_number) \
            .eq("otp_code", req.otp_code) \
            .eq("is_verified", False) \
            .gt("expires_at", datetime.utcnow().isoformat()) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        if not res.data:
            raise HTTPException(status_code=400, detail="Invalid or expired reset code")
        
        reset_id = res.data[0]["id"]
    except Exception as e:
        print(f"Verify Error: {e}")
        raise HTTPException(status_code=400, detail="Verification failed")

    # 2. Update User Password in Supabase Auth (Admin API)
    # The virtual email is phone@tilnet.com
    virtual_email = f"{req.phone_number.replace(' ', '')}@tilnet.com"
    
    try:
        # Find user by email
        user_res = supabase_admin.auth.admin.list_users()
        target_user = next((u for u in user_res if u.email == virtual_email), None)
        
        if not target_user:
            raise HTTPException(status_code=404, detail="User account not found")
        
        # Update password
        supabase_admin.auth.admin.update_user_by_id(
            target_user.id,
            attributes={"password": req.new_password}
        )
        
        # Mark OTP as used using Admin client
        supabase_admin.from_("password_resets").update({"is_verified": True}).eq("id", reset_id).execute()
        
    except Exception as e:
        print(f"Auth Reset Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": "Password updated successfully"}
