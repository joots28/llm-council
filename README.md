# Nexus AI API — LLM Council

A private, authenticated API around the original three-stage LLM Council. It keeps the existing React + Vite interface and FastAPI backend, while introducing a versioned API contract and a provider adapter for OpenRouter.

> **Development status:** this branch is an implementation in progress. It has not been deployed. JSON conversation storage remains a local-development implementation; PostgreSQL migrations, persistent client administration, rate limiting/quotas, and a full deployment validation are still pending. Do not expose this branch to the public internet until those items are completed and tested.

## How the Council works

1. **Independent responses:** configured models answer the prompt.
2. **Peer review:** responses are anonymized and ranked.
3. **Final synthesis:** the chairman model produces the consolidated answer.

## Current implementation

- Private bearer-key authentication using SHA-256 hashes configured on the server.
- Fail-closed behavior: protected routes return an error if client-key authentication is not configured.
- Client-scoped conversation listing, reading, creation, and writing.
- UUID validation and path-traversal protection for conversation files.
- Versioned API endpoints and an OpenAI-style non-streaming Chat Completions response.
- Dedicated Server-Sent Events (SSE) endpoint for Council stages.
- Provider adapter boundary, with OpenRouter as the initial provider.
- Frontend API base URL configuration, a session-only API-key entry screen, and an SSE frame parser that handles split network chunks.
- Automated security regression tests.

## API endpoints

All endpoints below require `Authorization: Bearer YOUR_CLIENT_KEY`, except `GET /` and `GET /health`, which expose only a minimal liveness response.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Minimal health check |
| GET | `/v1/models` | List the logical Council model and configured model allowlist |
| POST | `/v1/chat/completions` | OpenAI-style response using `nexus-council` or an allowlisted configured model |
| POST | `/v1/council/completions` | Return stages 1–3 and ranking metadata |
| POST | `/v1/council/completions/stream` | Stream Council stage events as SSE |
| GET | `/api/conversations` | List only the authenticated client's conversations |
| POST | `/api/conversations` | Create a conversation for the authenticated client |
| GET | `/api/conversations/{id}` | Read only a conversation owned by the authenticated client |
| POST | `/api/conversations/{id}/message` | Run the Council and save the result |
| POST | `/api/conversations/{id}/message/stream` | Run the Council with streamed stage events |

### Chat Completions example

```http
POST /v1/chat/completions
Authorization: Bearer YOUR_CLIENT_KEY
Content-Type: application/json
```

```json
{
  "model": "nexus-council",
  "messages": [
    {
      "role": "user",
      "content": "Explain hexagonal architecture."
    }
  ],
  "stream": false
}
```

The `/v1/chat/completions` endpoint currently supports `stream: false`. Use the dedicated Council streaming endpoint to receive stage events progressively.

## Configuration

1. Copy the root `.env.example` to `.env`.
2. Set `OPENROUTER_API_KEY` in the server environment.
3. Generate a client key and its SHA-256 hash locally. For example:

   ```bash
   python -c "import secrets,hashlib; k=secrets.token_urlsafe(32); print('CLIENT_KEY='+k); print('SHA256='+hashlib.sha256(k.encode()).hexdigest())"
   ```

4. Save the generated raw `CLIENT_KEY` securely and configure only its hash on the server:

   ```env
   API_CLIENT_KEY_HASHES=your-client-id:PASTE_SHA256_HASH_HERE
   ```

   Multiple clients can be configured as comma-separated `client-id:hash` pairs. Never put a raw client key or the OpenRouter key into source control.

5. Set `CORS_ALLOWED_ORIGINS` to the exact origins allowed to access the API.

For the frontend, copy `frontend/.env.example` to `frontend/.env.local` when you need a non-default API URL. The API key entered on the login screen is kept in `sessionStorage` for the current browser session. It is a client credential, not the OpenRouter master key.

## Local development

Backend:

```bash
uv sync
uv run python -m backend.main
```

Frontend, in another terminal:

```bash
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api`, `/v1`, and `/health` to `http://127.0.0.1:8001`.

## Tests

Run the security regression suite after installing project dependencies:

```bash
uv run python -m unittest discover -s tests -v
```

The tests cover missing/valid API keys, protection of legacy routes, conversation ownership, and conversation ID path traversal. The test suite has been added to the repository, but it must still be executed in a real Python environment before this branch is considered verified.

## Security and production readiness

- Raw client keys are not stored in the repository; server configuration contains their SHA-256 hashes.
- The OpenRouter master key stays server-side.
- Existing conversation routes are authenticated as well as the new `/v1` routes.
- CORS is not authentication and must be configured narrowly.
- Existing JSON conversation files created before ownership metadata was added are not automatically assigned to a client; migrate them deliberately if they need to be retained.
- JSON storage is not suitable for multi-instance production deployment. PostgreSQL-backed storage and migrations remain a required next phase.
- Persistent key administration, rate limiting, usage quotas, concurrency controls, deployment checks, and full integration tests are not yet complete.
- No deployment has been triggered by this work.

## Technology

- Python 3.10+
- FastAPI and Uvicorn
- httpx
- React and Vite
- OpenRouter as the initial model provider
