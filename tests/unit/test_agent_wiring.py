from app.agents.compute_agent import compute_agent
from app.agents.management_agent import management_agent
from app.agents.vision_agent import vision_agent
from app.core import llm_clients


def _tool_names(agent) -> set[str]:
    return {getattr(tool, "name", "") for tool in agent.tools}


def test_management_agent_exposes_all_top_level_capabilities() -> None:
    assert _tool_names(management_agent) == {
        "get_bbox_from_input",
        "get_coordinates_from_input",
        "compute_tool",
        "vision_tool",
    }


def test_compute_agent_exposes_flood_and_index_capabilities() -> None:
    assert _tool_names(compute_agent) == {
        "get_gfm_flood_analysis",
        "compute_NDVI_tool",
        "compute_NDBI_tool",
        "compute_NBR_tool",
        "compute_DVDI_tool",
        "compute_VHI_MODIS_tool",
        "compute_TCI_MODIS_tool",
        "compute_VCI_MODIS_tool",
        "compute_dNBR_tool",
        "compute_MNDWI_tool",
        "compute_NDWI_tool",
        "compute_NDVI_MODIS_tool",
    }


def test_vision_agent_exposes_single_and_comparison_analysis() -> None:
    assert _tool_names(vision_agent) == {"analyze_image_comparison", "analyze_image"}


def test_ollama_agent_settings_disable_thinking_and_parallel_calls(monkeypatch) -> None:
    monkeypatch.setattr(llm_clients.settings, "LLM_PROVIDER", "ollama")

    settings = llm_clients.get_agent_model_settings()

    assert settings.temperature == 0
    assert settings.parallel_tool_calls is False
    assert settings.extra_body == {"think": False}
