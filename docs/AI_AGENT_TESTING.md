# AI agent testing

This test suite covers the backend AI-agent boundary only. It does not test the
frontend, map rendering, or deployment services.

## What is covered

- General conversation and SQLite session creation.
- Vietnamese and Japanese flood requests.
- Place and date extraction, including date ranges and `latest` requests.
- Geocoding and flood-tool orchestration through mocked service boundaries.
- Structured analysis responses, tile URLs, and visualizations.
- Management, compute, and vision-agent tool registration.
- Ollama settings that disable thinking traces and parallel tool calls.
- Removal of leaked model reasoning, planning, Markdown fences, and `<think>` blocks.
- JSON response cleanup and fallback handling for plain-text model output.
- API request validation.
- Safe API behavior when the configured model is unreachable.

The tests are intentionally deterministic. Unit tests mock external geocoding,
GFM, and model calls. API end-to-end tests use the real FastAPI application and
ASGI transport, while replacing only the external boundary that the test is
checking. This keeps the suite fast and prevents Ollama, Nominatim, GFM, or
Gemini availability from changing the result.

## Run the suite

From `Disaster-monitor-be/`:

```powershell
uv sync --frozen
uv run pytest -q
```

Run one layer while developing:

```powershell
uv run pytest tests/unit -q
uv run pytest tests/e2e -q
```

## Live smoke checks

Live checks are useful for validating Ollama and current external data, but are
not part of the default test suite because their results can change over time.
Start the backend and send a request to:

```text
POST http://127.0.0.1:8001/api/v1/maps/agent-response
```

Example request body:

```json
{
  "input": "ベトナム・ハノイの洪水に関する最新情報を教えてください。",
  "session_id": "manual-smoke-test"
}
```

Verify that the response is a final user-facing answer in the requested
language. It must not contain planning text such as `Let's handle this`, tool
names, `please wait`, or private reasoning.

## Adding coverage

For a new tool-backed capability:

1. Add unit tests for input normalization and result formatting.
2. Mock the tool boundary and test the orchestration path.
3. Add an API end-to-end test that posts a realistic user request.
4. Assert the user-visible `response` and preserve any structured fields needed
   by the frontend, such as `tile_url` or `visualizations`.
5. Add a manual live smoke prompt only when the feature depends on current
   external data or the local LLM.
