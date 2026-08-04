import json

from app.services.ai_service import (
    _normalize_agent_payload,
    _parse_agent_output,
    _strip_agent_reasoning,
)


def test_strip_agent_reasoning_keeps_only_final_response() -> None:
    raw_output = """
Okay, let's handle this.
The user is asking if I can respond in Japanese.
No tool calls are needed here.

Final response:
"はい、日本語で回答できます。ご質問は何かありますか？"
"""

    assert _strip_agent_reasoning(raw_output) == "はい、日本語で回答できます。ご質問は何かありますか？"


def test_parse_agent_output_removes_think_tags() -> None:
    result = _parse_agent_output(
        '<think>Private planning that must not be shown.</think>{"response":"こんにちは。"}'
    )

    assert result == {"response": "こんにちは。"}


def test_parse_agent_output_accepts_fenced_json_with_metadata() -> None:
    result = _parse_agent_output(
        "```json\n"
        '{"analysis_type":"RGB analysis","area":"1,2,3,4",'
        '"response":"Analysis complete.","visualizations":[{"image_url":"https://example.test/image.png"}]}'
        "\n```"
    )

    assert result["analysis_type"] == "RGB analysis"
    assert result["area"] == "1,2,3,4"
    assert result["visualizations"][0]["image_url"] == "https://example.test/image.png"


def test_normalize_agent_payload_sanitizes_response_without_dropping_fields() -> None:
    payload = {
        "analysis_type": "Flood analysis",
        "area": "105,20,106,21",
        "response": "Reasoning: I need to summarize this.\nFinal answer: Flood data is available.",
        "tile_url": "https://example.test/tiles.json",
    }

    result = _normalize_agent_payload(payload)

    assert result["response"] == "Flood data is available."
    assert result["tile_url"] == "https://example.test/tiles.json"


def test_normalize_agent_payload_clears_placeholder_area() -> None:
    result = _normalize_agent_payload(
        {"analysis_type": "Greeting", "area": "area", "response": "Hello."}
    )

    assert result["area"] == ""


def test_parse_agent_output_falls_back_to_clean_plain_text() -> None:
    assert _parse_agent_output("  A concise answer.  ") == {"response": "A concise answer."}


def test_parse_agent_output_does_not_return_internal_json_as_plain_reasoning() -> None:
    payload = {"response": "Final response: The map is ready.", "visualizations": []}

    result = _parse_agent_output(json.dumps(payload))

    assert result == {"response": "The map is ready.", "visualizations": []}
