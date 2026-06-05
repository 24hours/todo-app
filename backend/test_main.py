import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def client(tmp_path, monkeypatch):
    """Point the app at a fresh throwaway SQLite file for every test."""
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.db")
    main.init_db()
    return TestClient(main.app)


def test_list_starts_empty(client):
    res = client.get("/api/todos")
    assert res.status_code == 200
    assert res.json() == []


def test_create_then_list(client):
    res = client.post("/api/todos", json={"title": "buy milk"})
    assert res.status_code == 201
    todo = res.json()
    assert todo == {"id": todo["id"], "title": "buy milk", "done": False}

    res = client.get("/api/todos")
    assert [t["title"] for t in res.json()] == ["buy milk"]


def test_create_trims_and_rejects_empty(client):
    assert client.post("/api/todos", json={"title": "  spaced  "}).json()["title"] == "spaced"
    assert client.post("/api/todos", json={"title": "   "}).status_code == 400


def test_toggle_done(client):
    todo = client.post("/api/todos", json={"title": "task"}).json()
    res = client.patch(f"/api/todos/{todo['id']}", json={"done": True})
    assert res.status_code == 200
    assert res.json()["done"] is True


def test_update_title(client):
    todo = client.post("/api/todos", json={"title": "old"}).json()
    res = client.patch(f"/api/todos/{todo['id']}", json={"title": "new"})
    assert res.json()["title"] == "new"
    assert res.json()["done"] is False


def test_patch_missing_returns_404(client):
    assert client.patch("/api/todos/999", json={"done": True}).status_code == 404


def test_delete(client):
    todo = client.post("/api/todos", json={"title": "gone"}).json()
    assert client.delete(f"/api/todos/{todo['id']}").status_code == 204
    assert client.get("/api/todos").json() == []


def test_delete_missing_returns_404(client):
    assert client.delete("/api/todos/999").status_code == 404
