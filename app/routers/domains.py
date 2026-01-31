"""Custom Domain management endpoints"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
from typing import Dict, Optional
import logging
import httpx
import re

from app.middleware.auth import get_current_user
from app.database import supabase_admin
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


# ============================================================================
# Pydantic Models
# ============================================================================

class DomainCreate(BaseModel):
    """Request model for adding a custom domain"""
    domain: str

    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v: str) -> str:
        # Normalize domain
        v = v.lower().strip().replace('https://', '').replace('http://', '').rstrip('/')

        # Basic domain validation
        domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$'
        if not re.match(domain_pattern, v):
            raise ValueError('Invalid domain format')

        # Block common domains
        blocked_domains = ['voxtro.io', 'vercel.app', 'localhost', 'supabase.co']
        for blocked in blocked_domains:
            if v.endswith(blocked) or v == blocked:
                raise ValueError(f'Cannot use {blocked} as custom domain')

        return v


class DomainResponse(BaseModel):
    """Response model for domain data"""
    id: str
    domain: str
    verification_status: str
    vercel_domain_id: Optional[str] = None
    verified_at: Optional[str] = None
    created_at: str
    cname_target: str = "cname.vercel-dns.com"


class DomainLookupResponse(BaseModel):
    """Response for public domain lookup"""
    found: bool
    user_id: Optional[str] = None
    branding: Optional[dict] = None


# ============================================================================
# Vercel API Integration
# ============================================================================

VERCEL_API_BASE = "https://api.vercel.com"


async def add_domain_to_vercel(domain: str) -> dict:
    """Add domain to Vercel project"""
    vercel_token = getattr(settings, 'vercel_token', None)
    vercel_project_id = getattr(settings, 'vercel_project_id', None)

    if not vercel_token or not vercel_project_id:
        logger.warning("Vercel credentials not configured, skipping Vercel API call")
        return {"id": None, "configured": False}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{VERCEL_API_BASE}/v10/projects/{vercel_project_id}/domains",
                headers={
                    "Authorization": f"Bearer {vercel_token}",
                    "Content-Type": "application/json"
                },
                json={"name": domain}
            )

        if response.status_code in [200, 201]:
            data = response.json()
            return {"id": data.get("name"), "configured": True}
        elif response.status_code == 409:
            # Domain already exists in Vercel
            return {"id": domain, "configured": True}
        else:
            logger.error(f"Vercel API error: {response.status_code} - {response.text}")
            return {"id": None, "configured": False, "error": response.text}

    except Exception as e:
        logger.error(f"Error adding domain to Vercel: {e}")
        return {"id": None, "configured": False, "error": str(e)}


async def check_domain_in_vercel(domain: str) -> dict:
    """Check domain configuration status in Vercel"""
    vercel_token = getattr(settings, 'vercel_token', None)
    vercel_project_id = getattr(settings, 'vercel_project_id', None)

    if not vercel_token or not vercel_project_id:
        return {"verified": False, "error": "Vercel credentials not configured"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{VERCEL_API_BASE}/v10/projects/{vercel_project_id}/domains/{domain}",
                headers={"Authorization": f"Bearer {vercel_token}"}
            )

        if response.status_code == 200:
            data = response.json()
            # Check if domain is properly configured
            verified = data.get("verified", False)
            return {
                "verified": verified,
                "configured": data.get("configured", False),
                "verification": data.get("verification", [])
            }
        else:
            return {"verified": False, "error": f"Status {response.status_code}"}

    except Exception as e:
        logger.error(f"Error checking domain in Vercel: {e}")
        return {"verified": False, "error": str(e)}


async def remove_domain_from_vercel(domain: str) -> bool:
    """Remove domain from Vercel project"""
    vercel_token = getattr(settings, 'vercel_token', None)
    vercel_project_id = getattr(settings, 'vercel_project_id', None)

    if not vercel_token or not vercel_project_id:
        return True  # Nothing to remove

    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{VERCEL_API_BASE}/v10/projects/{vercel_project_id}/domains/{domain}",
                headers={"Authorization": f"Bearer {vercel_token}"}
            )

        return response.status_code in [200, 204, 404]  # Success or already deleted

    except Exception as e:
        logger.error(f"Error removing domain from Vercel: {e}")
        return False


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("", response_model=Optional[DomainResponse])
async def get_my_domain(auth_data: Dict = Depends(get_current_user)):
    """Get current user's custom domain configuration"""
    try:
        user_id = auth_data["user_id"]

        result = supabase_admin.table("user_custom_domains") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()

        if not result.data or len(result.data) == 0:
            return None

        domain_data = result.data[0]
        return DomainResponse(
            id=domain_data["id"],
            domain=domain_data["domain"],
            verification_status=domain_data["verification_status"],
            vercel_domain_id=domain_data.get("vercel_domain_id"),
            verified_at=domain_data.get("verified_at"),
            created_at=domain_data["created_at"]
        )

    except Exception as e:
        logger.error(f"Error getting custom domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=DomainResponse)
async def add_custom_domain(data: DomainCreate, auth_data: Dict = Depends(get_current_user)):
    """Add a custom domain for the user's customer portal"""
    try:
        user_id = auth_data["user_id"]

        # Check if user already has a domain
        existing = supabase_admin.table("user_custom_domains") \
            .select("id") \
            .eq("user_id", user_id) \
            .execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(
                status_code=400,
                detail="You already have a custom domain. Delete it first to add a new one."
            )

        # Check if domain is already taken
        domain_check = supabase_admin.table("user_custom_domains") \
            .select("id") \
            .eq("domain", data.domain) \
            .execute()

        if domain_check.data and len(domain_check.data) > 0:
            raise HTTPException(status_code=400, detail="This domain is already in use")

        # Add domain to Vercel
        vercel_result = await add_domain_to_vercel(data.domain)

        # Store in database
        result = supabase_admin.table("user_custom_domains").insert({
            "user_id": user_id,
            "domain": data.domain,
            "verification_status": "pending",
            "vercel_domain_id": vercel_result.get("id")
        }).execute()

        domain_data = result.data[0]

        logger.info(f"Custom domain added: {data.domain} for user {user_id}")

        return DomainResponse(
            id=domain_data["id"],
            domain=domain_data["domain"],
            verification_status=domain_data["verification_status"],
            vercel_domain_id=domain_data.get("vercel_domain_id"),
            created_at=domain_data["created_at"]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding custom domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_domain(auth_data: Dict = Depends(get_current_user)):
    """Check and update domain verification status"""
    try:
        user_id = auth_data["user_id"]

        # Get user's domain
        result = supabase_admin.table("user_custom_domains") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()

        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="No custom domain configured")

        domain = result.data[0]["domain"]

        # Check with Vercel
        vercel_status = await check_domain_in_vercel(domain)

        if vercel_status.get("verified"):
            # Update database
            supabase_admin.table("user_custom_domains") \
                .update({
                    "verification_status": "verified",
                    "verified_at": "now()"
                }) \
                .eq("user_id", user_id) \
                .execute()

            return {
                "status": "verified",
                "message": "Domain verified successfully! Your custom domain is now active."
            }
        else:
            # Check if there are specific verification instructions
            verification_info = vercel_status.get("verification", [])

            return {
                "status": "pending",
                "message": "Domain not yet verified. Please ensure your CNAME record is correctly configured.",
                "instructions": f"Add a CNAME record pointing {domain} to cname.vercel-dns.com",
                "verification_details": verification_info
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("")
async def remove_custom_domain(auth_data: Dict = Depends(get_current_user)):
    """Remove user's custom domain"""
    try:
        user_id = auth_data["user_id"]

        # Get current domain
        result = supabase_admin.table("user_custom_domains") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()

        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="No custom domain configured")

        domain = result.data[0]["domain"]

        # Remove from Vercel
        await remove_domain_from_vercel(domain)

        # Remove from database
        supabase_admin.table("user_custom_domains") \
            .delete() \
            .eq("user_id", user_id) \
            .execute()

        logger.info(f"Custom domain removed: {domain} for user {user_id}")

        return {"success": True, "message": "Custom domain removed successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing custom domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lookup/{domain}", response_model=DomainLookupResponse)
async def lookup_domain(domain: str):
    """
    Public endpoint: Look up branding by domain.
    Used by frontend to fetch branding before customer login.
    """
    try:
        # Normalize domain
        domain = domain.lower().strip().replace('https://', '').replace('http://', '').rstrip('/')

        # Call database function
        result = supabase_admin.rpc('get_branding_by_domain', {'p_domain': domain}).execute()

        if not result.data or len(result.data) == 0:
            return DomainLookupResponse(found=False)

        branding_data = result.data[0]

        return DomainLookupResponse(
            found=True,
            user_id=branding_data["user_id"],
            branding={
                "logo_url": branding_data.get("logo_url"),
                "primary_color": branding_data.get("primary_color", "#f97316"),
                "secondary_color": branding_data.get("secondary_color", "#ea580c")
            }
        )

    except Exception as e:
        logger.error(f"Error looking up domain: {e}")
        return DomainLookupResponse(found=False)
