"""Customer portal permissions management endpoints for business owners"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, List, Optional
import logging
import httpx

from app.middleware.auth import get_current_user
from app.database import supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/types")
async def get_permission_types(auth_data: Dict = Depends(get_current_user)):
    """Get all available permission types"""
    try:
        result = supabase_admin.table("portal_permission_types").select("*").execute()
        return {"types": result.data or []}
    except Exception as e:
        logger.error(f"Get permission types error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/assignment/{assignment_type}/{assignment_id}")
async def get_assignment_permissions(
    assignment_type: str,
    assignment_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """Get permissions for a specific customer assignment"""
    try:
        user_id = auth_data["user_id"]

        # Validate assignment type
        if assignment_type not in ["voice", "chatbot", "whatsapp"]:
            raise HTTPException(status_code=400, detail="Invalid assignment type")

        # Verify user owns the agent
        if assignment_type == "voice":
            assignment = supabase_admin.table("customer_assistant_assignments").select(
                "id, assistant_id, customer_id, voice_assistants(user_id), customers(email, full_name)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data:
                raise HTTPException(status_code=404, detail="Assignment not found")

            if assignment.data.get("voice_assistants", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "assistant_assignment_id"

        elif assignment_type == "chatbot":
            assignment = supabase_admin.table("customer_chatbot_assignments").select(
                "id, chatbot_id, customer_id, chatbots(user_id), customers(email, full_name)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data:
                raise HTTPException(status_code=404, detail="Assignment not found")

            if assignment.data.get("chatbots", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "chatbot_assignment_id"

        else:  # whatsapp
            assignment = supabase_admin.table("customer_whatsapp_agent_assignments").select(
                "id, agent_id, customer_id, whatsapp_agents(user_id), customers(email, full_name)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data:
                raise HTTPException(status_code=404, detail="Assignment not found")

            if assignment.data.get("whatsapp_agents", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "whatsapp_assignment_id"

        # Get current permissions
        perms = supabase_admin.table("customer_portal_permissions").select(
            "id, permission_type_id, is_enabled, granted_at, portal_permission_types(id, name, category, description, agent_type)"
        ).eq(column, assignment_id).execute()

        # Get all applicable permission types for this agent type
        agent_type_filter = assignment_type if assignment_type != "voice" else "voice"
        all_types = supabase_admin.table("portal_permission_types").select("*").or_(
            f"agent_type.eq.{agent_type_filter},agent_type.eq.all"
        ).execute()

        # Build response with all types and their current status
        perm_map = {p["permission_type_id"]: p for p in (perms.data or [])}
        permissions = []
        for ptype in (all_types.data or []):
            existing = perm_map.get(ptype["id"])
            permissions.append({
                "permission_type_id": ptype["id"],
                "name": ptype["name"],
                "category": ptype["category"],
                "description": ptype["description"],
                "is_enabled": existing["is_enabled"] if existing else False,
                "is_set": existing is not None
            })

        return {
            "assignment": {
                "id": assignment_id,
                "type": assignment_type,
                "customer": assignment.data.get("customers", {})
            },
            "permissions": permissions
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get assignment permissions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/assignment/{assignment_type}/{assignment_id}")
async def set_assignment_permissions(
    assignment_type: str,
    assignment_id: str,
    data: Dict,
    auth_data: Dict = Depends(get_current_user)
):
    """Set permissions for a specific customer assignment"""
    try:
        user_id = auth_data["user_id"]
        permissions = data.get("permissions", [])

        if not permissions:
            raise HTTPException(status_code=400, detail="Permissions list is required")

        # Validate assignment type
        if assignment_type not in ["voice", "chatbot", "whatsapp"]:
            raise HTTPException(status_code=400, detail="Invalid assignment type")

        # Verify user owns the agent
        if assignment_type == "voice":
            assignment = supabase_admin.table("customer_assistant_assignments").select(
                "id, voice_assistants(user_id)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data or assignment.data.get("voice_assistants", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "assistant_assignment_id"

        elif assignment_type == "chatbot":
            assignment = supabase_admin.table("customer_chatbot_assignments").select(
                "id, chatbots(user_id)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data or assignment.data.get("chatbots", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "chatbot_assignment_id"

        else:  # whatsapp
            assignment = supabase_admin.table("customer_whatsapp_agent_assignments").select(
                "id, whatsapp_agents(user_id)"
            ).eq("id", assignment_id).single().execute()

            if not assignment.data or assignment.data.get("whatsapp_agents", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

            column = "whatsapp_assignment_id"

        # Upsert each permission
        for perm in permissions:
            perm_type_id = perm.get("permission_type_id")
            is_enabled = perm.get("is_enabled", False)

            if not perm_type_id:
                continue

            perm_data = {
                column: assignment_id,
                "permission_type_id": perm_type_id,
                "is_enabled": is_enabled,
                "granted_by": user_id
            }

            # Use upsert with on_conflict
            supabase_admin.table("customer_portal_permissions").upsert(
                perm_data,
                on_conflict=f"{column},permission_type_id"
            ).execute()

        return {"success": True, "message": "Permissions updated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Set assignment permissions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content/pending")
async def get_pending_content(
    agent_type: Optional[str] = None,
    auth_data: Dict = Depends(get_current_user)
):
    """Get pending content submissions for review"""
    try:
        user_id = auth_data["user_id"]

        pending_content = []

        # Get voice assistant content
        if agent_type in [None, "voice"]:
            # Get user's voice assistants
            assistants = supabase_admin.table("voice_assistants").select(
                "id"
            ).eq("user_id", user_id).execute()

            assistant_ids = [a["id"] for a in (assistants.data or [])]

            if assistant_ids:
                content = supabase_admin.table("customer_contributed_content").select(
                    "*, customers(email, full_name), voice_assistants:assistant_id(id, name)"
                ).in_("assistant_id", assistant_ids).eq("status", "pending").order(
                    "created_at", desc=True
                ).execute()

                for item in (content.data or []):
                    item["agent_type"] = "voice"
                    item["agent_name"] = item.get("voice_assistants", {}).get("name", "Unknown")
                    pending_content.append(item)

        # Get chatbot content
        if agent_type in [None, "chatbot"]:
            chatbots = supabase_admin.table("chatbots").select(
                "id"
            ).eq("user_id", user_id).execute()

            chatbot_ids = [c["id"] for c in (chatbots.data or [])]

            if chatbot_ids:
                content = supabase_admin.table("customer_contributed_content").select(
                    "*, customers(email, full_name), chatbots:chatbot_id(id, name)"
                ).in_("chatbot_id", chatbot_ids).eq("status", "pending").order(
                    "created_at", desc=True
                ).execute()

                for item in (content.data or []):
                    item["agent_type"] = "chatbot"
                    item["agent_name"] = item.get("chatbots", {}).get("name", "Unknown")
                    pending_content.append(item)

        return {"content": pending_content}

    except Exception as e:
        logger.error(f"Get pending content error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/{content_id}/review")
async def review_content(
    content_id: str,
    data: Dict,
    auth_data: Dict = Depends(get_current_user)
):
    """Approve or reject customer content submission"""
    try:
        user_id = auth_data["user_id"]
        action = data.get("action")  # 'approve' or 'reject'
        notes = data.get("notes", "")

        if action not in ["approve", "reject"]:
            raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

        # Get the content
        content = supabase_admin.table("customer_contributed_content").select(
            "*, voice_assistants:assistant_id(id, name, user_id)"
        ).eq("id", content_id).single().execute()

        if not content.data:
            raise HTTPException(status_code=404, detail="Content not found")

        # Verify user owns the agent
        if content.data.get("assistant_id"):
            if content.data.get("voice_assistants", {}).get("user_id") != user_id:
                raise HTTPException(status_code=403, detail="Access denied")
        elif content.data.get("chatbot_id"):
            chatbot = supabase_admin.table("chatbots").select(
                "user_id"
            ).eq("id", content.data["chatbot_id"]).single().execute()
            if not chatbot.data or chatbot.data["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Access denied")

        if content.data["status"] != "pending":
            raise HTTPException(status_code=400, detail="Content has already been reviewed")

        if action == "reject":
            # Simply update status to rejected
            supabase_admin.table("customer_contributed_content").update({
                "status": "rejected",
                "reviewed_by": user_id,
                "reviewed_at": "now()",
                "review_notes": notes
            }).eq("id", content_id).execute()

            return {"success": True, "action": "rejected"}

        # For 'approve' action with voice assistant, apply FAQ to VAPI
        if content.data.get("assistant_id") and content.data["content_type"] == "faq":
            assistant_id = content.data["assistant_id"]

            # Get user's VAPI connection
            connection = supabase_admin.table("voice_connections").select(
                "api_key"
            ).eq("user_id", user_id).eq("is_active", True).limit(1).execute()

            if not connection.data:
                raise HTTPException(status_code=400, detail="No active VAPI connection found")

            api_key = connection.data[0]["api_key"]

            # Fetch current assistant from VAPI
            async with httpx.AsyncClient() as client:
                vapi_response = await client.get(
                    f"https://api.vapi.ai/assistant/{assistant_id}",
                    headers={"Authorization": f"Bearer {api_key}"}
                )

                if vapi_response.status_code != 200:
                    raise HTTPException(status_code=500, detail="Failed to fetch assistant from VAPI")

                assistant = vapi_response.json()

                # Get current system prompt
                current_prompt = ""
                model = assistant.get("model", {})
                messages = model.get("messages", [])
                for msg in messages:
                    if msg.get("role") == "system":
                        current_prompt = msg.get("content", "")
                        break

                # Build FAQ section to append
                faq_section = f"\n\n## Customer FAQ\nQ: {content.data['title']}\nA: {content.data['content']}"
                updated_prompt = current_prompt + faq_section

                # Update VAPI assistant
                update_payload = {
                    "model": {
                        **model,
                        "messages": [{"role": "system", "content": updated_prompt}]
                    }
                }

                update_response = await client.patch(
                    f"https://api.vapi.ai/assistant/{assistant_id}",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json=update_payload
                )

                if update_response.status_code not in [200, 201]:
                    logger.error(f"VAPI update error: {update_response.text}")
                    raise HTTPException(status_code=500, detail="Failed to update assistant in VAPI")

            # Update content status to applied
            supabase_admin.table("customer_contributed_content").update({
                "status": "applied",
                "reviewed_by": user_id,
                "reviewed_at": "now()",
                "review_notes": notes,
                "applied_at": "now()"
            }).eq("id", content_id).execute()

            return {"success": True, "action": "applied", "message": "FAQ added to voice assistant"}

        else:
            # Just mark as approved for non-voice or non-FAQ content
            supabase_admin.table("customer_contributed_content").update({
                "status": "approved",
                "reviewed_by": user_id,
                "reviewed_at": "now()",
                "review_notes": notes
            }).eq("id", content_id).execute()

            return {"success": True, "action": "approved"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Review content error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/customers/{customer_id}/assignments")
async def get_customer_assignments_with_permissions(
    customer_id: str,
    auth_data: Dict = Depends(get_current_user)
):
    """Get all assignments for a customer with their permissions (for business owner UI)"""
    try:
        user_id = auth_data["user_id"]

        assignments = []

        # Get voice assistant assignments
        voice = supabase_admin.table("customer_assistant_assignments").select(
            "id, assistant_id, voice_assistants(id, name, user_id)"
        ).eq("customer_id", customer_id).execute()

        for a in (voice.data or []):
            if a.get("voice_assistants", {}).get("user_id") == user_id:
                perms = supabase_admin.table("customer_portal_permissions").select(
                    "permission_type_id, is_enabled"
                ).eq("assistant_assignment_id", a["id"]).execute()

                assignments.append({
                    "type": "voice",
                    "assignment_id": a["id"],
                    "agent_id": a.get("voice_assistants", {}).get("id"),
                    "agent_name": a.get("voice_assistants", {}).get("name", "Unknown"),
                    "permissions": perms.data or []
                })

        # Get chatbot assignments
        chatbot = supabase_admin.table("customer_chatbot_assignments").select(
            "id, chatbot_id, chatbots(id, name, user_id)"
        ).eq("customer_id", customer_id).execute()

        for a in (chatbot.data or []):
            if a.get("chatbots", {}).get("user_id") == user_id:
                perms = supabase_admin.table("customer_portal_permissions").select(
                    "permission_type_id, is_enabled"
                ).eq("chatbot_assignment_id", a["id"]).execute()

                assignments.append({
                    "type": "chatbot",
                    "assignment_id": a["id"],
                    "agent_id": a.get("chatbots", {}).get("id"),
                    "agent_name": a.get("chatbots", {}).get("name", "Unknown"),
                    "permissions": perms.data or []
                })

        # Get WhatsApp assignments
        whatsapp = supabase_admin.table("customer_whatsapp_agent_assignments").select(
            "id, agent_id, whatsapp_agents(id, name, user_id)"
        ).eq("customer_id", customer_id).execute()

        for a in (whatsapp.data or []):
            if a.get("whatsapp_agents", {}).get("user_id") == user_id:
                perms = supabase_admin.table("customer_portal_permissions").select(
                    "permission_type_id, is_enabled"
                ).eq("whatsapp_assignment_id", a["id"]).execute()

                assignments.append({
                    "type": "whatsapp",
                    "assignment_id": a["id"],
                    "agent_id": a.get("whatsapp_agents", {}).get("id"),
                    "agent_name": a.get("whatsapp_agents", {}).get("name", "Unknown"),
                    "permissions": perms.data or []
                })

        return {"assignments": assignments}

    except Exception as e:
        logger.error(f"Get customer assignments with permissions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
