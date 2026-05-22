from typing import List

from agents import Agent, OpenAIChatCompletionsModel
from pydantic import BaseModel, Field
from typing import Optional
from app.core.llm_clients import get_deepseek_client, get_gemini_model
from app.core.llm_clients import get_deepseek_model
# from app.agents.image_analyst_agent import analyze_image_tool
from app.agents.tools.image_analyst_tool import analyze_image
from app.agents.tools.geo_coding_tool import get_bbox_from_input
from app.agents.tools.geo_coding_location_tool import get_coordinates_from_input
from app.agents.compute_agent import compute_agent
from app.agents.vision_agent import vision_agent
from app.agents.prompts.management_prompt import instruction_v4, vision_tool_description_v2, compute_tool_description_v2

gemini_model = get_gemini_model("gemini-2.5-flash-lite")
deepseek_model = get_deepseek_model("deepseek-chat")

# tool1 = analyze_image_tool
# tool2 = analyze_image
tool1 = get_bbox_from_input
tool2 = get_coordinates_from_input
tool3 = compute_agent.as_tool(tool_name="compute_tool",
                              tool_description=compute_tool_description_v2,
                              parameters=None, include_input_schema=False)
tool4 = vision_agent.as_tool(tool_name="vision_tool",
                              tool_description=vision_tool_description_v2,
                              parameters=None, include_input_schema=False)


class VisualizationItem(BaseModel):
    label: Optional[str] = Field(default=None, description="Nhãn mô tả hình ảnh hoặc lớp bản đồ")
    image_url: Optional[str] = Field(default=None, description="URL ảnh tĩnh PNG/JPG")
    tile_url: Optional[str] = Field(default=None, description="TileJSON URL hoặc tile layer URL")
    legend: Optional[str] = Field(default=None, description="Thông tin bảng màu hoặc chú thích dữ liệu")

class ManagementResponse(BaseModel):
    analysis_type: str = Field(description="Loại phân tích được thực hiện")
    area: str = Field(description="Khu vực được phân tích")
    response: str = Field(description="Kết quả phân tích tổng hợp")
    visualizations: Optional[List[VisualizationItem]] = Field(default=None, description="Danh sách hình ảnh hoặc lớp bản đồ trực quan")

management_agent = Agent(name="Management Agent", model=deepseek_model, instructions=instruction_v4, tools = [tool1, tool2, tool3, tool4])
