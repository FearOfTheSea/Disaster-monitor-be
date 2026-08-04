import json
import logging
import re
from pathlib import Path
import httpx
from agents import RunConfig, Runner, SessionSettings
from agents.extensions.memory import AsyncSQLiteSession
from app.agents.management_agent import management_agent
from app.core.config import settings
logger = logging.getLogger(__name__)
SESSION_DIR = Path(settings.SESSION_DIR)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(SESSION_DIR / "conversations.db")

async def process_response(input: str, session_id: str):
    try:
        session = AsyncSQLiteSession(session_id, DB_PATH)
        logger.info(f"Đang xử lý input: {input} cho session_id: {session_id}")

        try:
            result = await Runner.run(
                management_agent,
                input=input,
                session=session,
                max_turns=5,
                run_config=RunConfig(session_settings=SessionSettings(limit=50))
            )
        except (httpx.RequestError, httpx.HTTPStatusError, TimeoutError) as net_err:
            logger.error("Loi ket noi khi goi Agent.", exc_info=True)
            raise RuntimeError("Khong the ket noi den dich vu AI. Vui long thu lai.") from net_err
        logger.info(f"Agent result: {result}")
        
        raw_output = result.final_output
        logger.info(f"Final output raw: {raw_output}")

        if not raw_output:
            logger.warning("Agent trả về final_output rỗng")
            return "Lỗi: Agent không trả về phản hồi"

        try:
            return json.loads(raw_output)

        except json.JSONDecodeError:
            logger.warning("Parse JSON thất bại lần 1, đang tiến hành gọt chuỗi...")
            try:
                cleaned_text = re.sub(r'```(?:json)?\n?(.*?)\n?```', r'\1', raw_output, flags=re.DOTALL)

                start_idx = cleaned_text.find('{')
                end_idx = cleaned_text.rfind('}')

                if start_idx != -1 and end_idx != -1:
                    json_string = cleaned_text[start_idx:end_idx+1]
                    return json.loads(json_string)
                else:
                    raise ValueError("Không tìm thấy ngoặc nhọn cấu trúc JSON.")
                    
            except (json.JSONDecodeError, ValueError) as clean_error:
                logger.error(f"Gọt chuỗi thất bại: {clean_error}. Trả về fallback text.")
                return {
                    "response": raw_output.strip()
                }

    except Exception as e:
        logger.error(f"Lỗi khi lấy phản hồi từ Agent: {e}", exc_info=True)
        raise
