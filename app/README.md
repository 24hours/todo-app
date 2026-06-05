# Todo App

A simple full-stack todo app.

- **Frontend:** React + Vite
- **Backend:** FastAPI
- **Storage:** SQLite

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 9900
```

The API runs at `http://localhost:9900` (interactive docs at `/docs`).

### Endpoints

| Method | Path              | Description        |
| ------ | ----------------- | ------------------ |
| GET    | `/api/todos`      | List all todos     |
| POST   | `/api/todos`      | Create a todo      |
| PATCH  | `/api/todos/{id}` | Update title/done  |
| DELETE | `/api/todos/{id}` | Delete a todo      |

## Frontend

```bash
cd frontend
npm install
npm run dev
```

The app runs at `http://localhost:5173` and proxies `/api` to the backend.
