# Disaster Monitor backend

Run locally with `uv run uvicorn app.main:app --host 0.0.0.0 --port 8001`.

## Deployment

Railway uses `railway.toml` to start the API and checks `/health`. Configure
`DATABASE_URL`, `ALLOWED_ORIGINS`, `GEE_SERVICE_ACCOUNT`, `GEE_KEY_JSON`,
`PLANETARY_COMPUTER_STAC_URL`, `PLANETARY_MOSAIC_URL`, `OPENWEATHER_API_KEY`,
and the Gemini/DeepSeek credentials used by the agents. `ALLOWED_ORIGINS` must
be a JSON list such as `["https://example.vercel.app"]`.

Mount persistent storage at `/app/session` to retain chat sessions. Never commit
the Google service-account JSON; store its complete contents in `GEE_KEY_JSON`.
