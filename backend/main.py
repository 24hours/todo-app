import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "todos.db"

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
                done INTEGER NOT NULL DEFAULT 0
            )
            """
        )


init_db()


class TodoCreate(BaseModel):
    title: str


class TodoUpdate(BaseModel):
    title: str | None = None
    done: bool | None = None


class Todo(BaseModel):
    id: int
    title: str
    done: bool


def row_to_todo(row: sqlite3.Row) -> Todo:
    return Todo(id=row["id"], title=row["title"], done=bool(row["done"]))


@app.get("/api/debug/sentry")
def trigger_error():
    """Deliberately raise an unhandled error to verify Sentry reporting."""
    raise RuntimeError("Sentry backend test error")


@app.get("/api/todos", response_model=list[Todo])
def list_todos():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM todos ORDER BY id").fetchall()
    return [row_to_todo(r) for r in rows]


@app.post("/api/todos", response_model=Todo, status_code=201)
def create_todo(payload: TodoCreate):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title must not be empty")
    with get_db() as conn:
        cur = conn.execute("INSERT INTO todos (title, done) VALUES (?, 0)", (title,))
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
        conn.execute(
            "UPDATE todos SET title = ?, done = ? WHERE id = ?", (title, done, todo_id)
        )
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return row_to_todo(row)


@app.delete("/api/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return None
