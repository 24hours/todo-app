import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "todos.db"

# Expanded palette: 11 hues x 5 shades (light -> dark) = 55 colors.
# Laid out so the frontend can render it as a 5-row x 11-column grid.
# Must stay in sync with frontend/src/App.jsx COLORS.
COLORS = [
    # red          orange       amber        yellow       green        teal         sky          blue         indigo       purple       pink
    "#fca5a5",   "#fdba74",   "#fcd34d",   "#fde68a",   "#86efac",   "#5eead4",   "#7dd3fc",   "#93c5fd",   "#a5b4fc",   "#d8b4fe",   "#f9a8d4",  # 200
    "#f87171",   "#fb923c",   "#fbbf24",   "#facc15",   "#4ade80",   "#2dd4bf",   "#38bdf8",   "#60a5fa",   "#818cf8",   "#c084fc",   "#f472b6",  # 400
    "#ef4444",   "#f97316",   "#f59e0b",   "#eab308",   "#22c55e",   "#14b8a6",   "#0ea5e9",   "#3b82f6",   "#6366f1",   "#a855f7",   "#ec4899",  # 500
    "#dc2626",   "#ea580c",   "#d97706",   "#ca8a04",   "#16a34a",   "#0d9488",   "#0284c7",   "#2563eb",   "#4f46e5",   "#9333ea",   "#db2777",  # 600
    "#991b1b",   "#9a3412",   "#92400e",   "#854d0e",   "#166534",   "#115e59",   "#075985",   "#1e40af",   "#3730a3",   "#6b21a8",   "#9d174d",  # 800
]
# Keep the previous default (#ef4444 red-500) so existing rows and tests stay valid.
DEFAULT_COLOR = "#ef4444"

# Initialize Sentry before the app is created so the FastAPI integration
# (auto-enabled) can hook into the ASGI lifecycle. DSN is overridable via env.
sentry_sdk.init(
    dsn=os.environ.get(
        "SENTRY_DSN",
        "https://8ac8d7b77e480723dd4a2ae7bf1d1e08@o64703.ingest.us.sentry.io/4511510329622528",
    ),
    # Capture request/user context on errors.
    send_default_pii=True,
    # Performance tracing. Lower this in production (e.g. 0.1).
    traces_sample_rate=1.0,
    environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
)

app = FastAPI(title="Todo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                color TEXT NOT NULL DEFAULT '%s',
                position INTEGER NOT NULL DEFAULT 0
            )
            """
            % DEFAULT_COLOR
        )
        # Migrate older tables that predate the color / position columns.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(todos)").fetchall()}
        if "color" not in cols:
            conn.execute(
                "ALTER TABLE todos ADD COLUMN color TEXT NOT NULL DEFAULT ?",
                (DEFAULT_COLOR,),
            )
        if "position" not in cols:
            conn.execute(
                "ALTER TABLE todos ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
            )
            # Seed positions from existing id order so reordering has a baseline.
            conn.execute("UPDATE todos SET position = id")


init_db()


class TodoCreate(BaseModel):
    title: str
    color: str | None = None


class TodoUpdate(BaseModel):
    title: str | None = None
    done: bool | None = None
    color: str | None = None


class TodoReorder(BaseModel):
    # Todo ids in the desired display order.
    ids: list[int]


class Todo(BaseModel):
    id: int
    title: str
    done: bool
    color: str


def row_to_todo(row: sqlite3.Row) -> Todo:
    return Todo(
        id=row["id"],
        title=row["title"],
        done=bool(row["done"]),
        color=row["color"],
    )


@app.get("/api/debug/sentry")
def trigger_error():
    """Deliberately raise an unhandled error to verify Sentry reporting."""
    raise RuntimeError("Sentry backend test error")


@app.get("/api/todos", response_model=list[Todo])
def list_todos():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM todos ORDER BY position, id").fetchall()
    return [row_to_todo(r) for r in rows]


@app.post("/api/todos", response_model=Todo, status_code=201)
def create_todo(payload: TodoCreate):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title must not be empty")
    color = payload.color if payload.color in COLORS else DEFAULT_COLOR
    with get_db() as conn:
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM todos"
        ).fetchone()["pos"]
        cur = conn.execute(
            "INSERT INTO todos (title, done, color, position) VALUES (?, 0, ?, ?)",
            (title, color, next_pos),
        )
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_todo(row)


@app.patch("/api/todos/{todo_id}", response_model=Todo)
def update_todo(todo_id: int, payload: TodoUpdate):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Todo not found")
        title = row["title"] if payload.title is None else payload.title.strip()
        done = row["done"] if payload.done is None else int(payload.done)
        if payload.color is None:
            color = row["color"]
        elif payload.color in COLORS:
            color = payload.color
        else:
            raise HTTPException(status_code=400, detail="Unknown color")
        conn.execute(
            "UPDATE todos SET title = ?, done = ?, color = ? WHERE id = ?",
            (title, done, color, todo_id),
        )
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return row_to_todo(row)


@app.put("/api/todos/reorder", response_model=list[Todo])
def reorder_todos(payload: TodoReorder):
    with get_db() as conn:
        existing = {r["id"] for r in conn.execute("SELECT id FROM todos").fetchall()}
        if set(payload.ids) != existing:
            raise HTTPException(
                status_code=400, detail="ids must match the full set of todos exactly"
            )
        for pos, todo_id in enumerate(payload.ids):
            conn.execute(
                "UPDATE todos SET position = ? WHERE id = ?", (pos, todo_id)
            )
        rows = conn.execute("SELECT * FROM todos ORDER BY position, id").fetchall()
    return [row_to_todo(r) for r in rows]


@app.delete("/api/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return None
