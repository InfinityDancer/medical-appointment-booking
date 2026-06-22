import json
from fastapi import APIRouter, Request, BackgroundTasks
from src.utils.supabase_utils import process_single_row

router = APIRouter()

@router.post("/webhooks/process-embeddings")
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
