from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from pydantic import BaseModel
from app.services.ai_service import process_analysis, process_response
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

class AgentRequest(BaseModel):
    input: str
    session_id: str

@router.post("/agent-response")
async def agent_response(request: AgentRequest):
    try:
        logger.info(f"Nhận request với input: {request.input} cho session_id: {request.session_id}")
        result = await process_response(request.input, request.session_id)
        logger.info(f"Response từ service: {result}")
        return result
    except Exception as e:
        logger.error(f"Lỗi khi gọi agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý: {str(e)}")
