# CLAUDE.md

Guidance for working in this repo (a simple full-stack todo app).

## Stack & layout

- **Frontend** — React + Vite, in `frontend/`. Entry `src/main.jsx`, UI in `src/App.jsx`,
  styles in `src/index.css`. Sentry is initialized in `src/main.jsx`.
- **Backend** — FastAPI + SQLite, in `backend/main.py`. SQLite file `backend/todos.db`
  (gitignored, auto-created on startup). Sentry is initialized at module load.
- **Storage** — single `todos` table (`id`, `title`, `done`), created via
  `CREATE TABLE IF NOT EXISTS` in `init_db()`.

## Running

The frontend dev server is hosted at **http://localhost:5173** (Vite). It proxies
`/api` to the backend at **http://localhost:9900** (see `frontend/vite.config.js`).

```bash
make install   # one-time: backend venv + frontend node_modules
make run       # backend on :9900 and frontend on :5173 together (Ctrl-C stops both)
```

Individual pieces: `make backend`, `make frontend`. The backend's port is set by the
`uvicorn --port 9900` invocation in the `Makefile` — it is not hardcoded in `main.py`.

## API

Base path `/api`. Endpoints in `backend/main.py`:

| Method | Path              | Notes                                  |
| ------ | ----------------- | -------------------------------------- |
| GET    | `/api/todos`      | List all todos                         |
| POST   | `/api/todos`      | Create; empty/whitespace title → 400   |
| PATCH  | `/api/todos/{id}` | Update title/done; missing id → 404    |
| DELETE | `/api/todos/{id}` | Delete; missing id → 404 (204 on ok)   |

There is also `GET /api/debug/sentry`, which deliberately raises to test Sentry.

## Tests

- **Backend** (`pytest`): `cd backend && pytest -q`. Tests in `backend/test_main.py` use
  FastAPI `TestClient` with an isolated temp DB per test (autouse fixture monkeypatches
  `main.DB_PATH`). Dev deps in `backend/requirements-dev.txt`.
- **Frontend** (`vitest`): `cd frontend && npm test`. Tests in `src/App.test.jsx`
  (jsdom + Testing Library), `fetch` mocked. Config lives in `frontend/vite.config.js`
  under `test:`, with `src/setupTests.js` loading jest-dom.

CI runs both on push to `master` and on PRs: `.github/workflows/ci.yml`.

## Conventions

- Keep changes minimal and consistent with the surrounding code.
- Backend: validate input and return proper status codes (400/404) as the existing
  handlers do; use the `get_db()` context manager for DB access.
- Frontend: keep API calls going through the `/api` proxy (don't hardcode the backend
  origin). Match the existing functional-component + hooks style.
- After changing code, run the relevant test suite above before considering it done.
