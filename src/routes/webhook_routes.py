import json
import os
from fastapi import APIRouter, Request, BackgroundTasks, Header, HTTPException, Depends
from src.utils.supabase_utils import process_single_row
from src.services.automation_worker import process_automations_cron

router = APIRouter()

async def verify_webhook_secret(authorization: str = Header(None)):
    """
    Dependency to verify the webhook secret.
    Requires an Authorization header in the format 'Bearer YOUR_SECRET'.
    """
    expected_secret = os.getenv("WEBHOOK_SECRET")
    
    if not expected_secret:
        print("[WARNING] WEBHOOK_SECRET not set in environment!")
        raise HTTPException(status_code=500, detail="Server webhook configuration error")
        
    if authorization != f"Bearer {expected_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    return True

@router.post("/webhooks/process-embeddings", dependencies=[Depends(verify_webhook_secret)])
async def process_embeddings_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Called by the Supabase pg_net trigger when a new row is inserted
    into organisation_services_3 with a related_document but no content.
    Processes the document in the background so the response returns immediately.
    """
    try:
        payload = await request.json()
        print(f"[WEBHOOK] process-embeddings called")
        print(f"[WEBHOOK] Payload: {json.dumps(payload, indent=2)}")

        row_id = payload.get("organisation_services_id")
        org_id = payload.get("organisation_id")
        doc_link = payload.get("related_document")
        table_name = payload.get("table_name", "organisation_services_3")

        if not all([row_id, org_id, doc_link]):
            print(f"[WEBHOOK] Missing required fields in payload")
            return {"status": "error", "message": "Missing required fields: organisation_services_id, organisation_id, related_document"}

        # Process in background so the trigger doesn't time out
        background_tasks.add_task(process_single_row, table_name, row_id, org_id, doc_link)

        print(f"[WEBHOOK] Queued background processing for row {row_id}")
        return {"status": "accepted", "message": f"Processing queued for row {row_id}"}

    except Exception as e:
        print(f"[WEBHOOK] Error: {e}")
        return {"status": "error", "message": str(e)}

@router.api_route("/automations/trigger", methods=["GET", "POST"], dependencies=[Depends(verify_webhook_secret)])
async def trigger_automations_cron(background_tasks: BackgroundTasks):
    """
    Endpoint for Supabase pg_cron to hit at regular intervals to process
    active automated workflows.
    """
    try:
        print("[CRON] Triggering automations worker...")
        background_tasks.add_task(process_automations_cron)
        return {"status": "accepted", "message": "Automations processing queued"}
    except Exception as e:
        print(f"[CRON] Error queuing automations: {e}")
        return {"status": "error", "message": str(e)}
