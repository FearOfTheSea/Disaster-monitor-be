# Disaster Monitor backend

Run locally with `uv run uvicorn app.main:app --host 0.0.0.0 --port 8001`.

## Local Ollama chat

The management, compute, and vision agents use the provider selected by
`LLM_PROVIDER`. For local chat, use Ollama with:

```text
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen3:1.7b
```

Ensure Ollama is running and the model is available with `ollama list` before
starting the backend. The optional satellite image-analysis tools still use
Gemini when those tools are invoked.

## Geocoding

Place names use the keyless Nominatim provider by default:

```text
GEOCODER_PROVIDER=nominatim
GEOCODER_COUNTRYCODES=vn
```

The backend adds a custom user agent, caches successful lookups, and limits
public Nominatim requests to one per second. This is suitable for a small MVP;
keep the existing LocationIQ provider for higher-volume or production use by
setting `GEOCODER_PROVIDER=locationiq` and providing `IQ_LOCATION_API_KEY`.
Keep OpenStreetMap attribution visible in the frontend.

## AI-agent tests

The AI-agent test scope covers chat sessions, multilingual requests, geocoded
flood analysis, structured satellite-analysis responses, API validation, and
removal of leaked model reasoning. Tests are deterministic and do not require
Ollama or external data services:

```powershell
uv sync --frozen
uv run pytest -q
```

See [docs/AI_AGENT_TESTING.md](docs/AI_AGENT_TESTING.md) for the test matrix,
API end-to-end setup, and live smoke-check guidance.

The minimal local stack does not require PostgreSQL, Earth Engine, OpenWeather,
LocationIQ credentials, or Planetary Computer credentials. Chat sessions use SQLite under
`SESSION_DIR` (default: `session`). Configure those integrations only when you
enable the corresponding feature.

## Deployment

Railway uses `railway.toml` to start the API and checks `/health`. Set
`ALLOWED_ORIGINS` and one reachable LLM provider. PostgreSQL is optional unless
another feature uses it. `ALLOWED_ORIGINS` must be a JSON list such as
`["https://example.vercel.app"]`.

Mount persistent storage at `/app/session` to retain chat sessions. Never commit
the Google service-account JSON; store its complete contents in `GEE_KEY_JSON`.
