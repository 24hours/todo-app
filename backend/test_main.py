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
    assert todo == {
        "id": todo["id"],
        "title": "buy milk",
        "done": False,
        "color": main.DEFAULT_COLOR,
    }

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


def test_create_with_color(client):
    color = main.COLORS[3]
    todo = client.post("/api/todos", json={"title": "tagged", "color": color}).json()
    assert todo["color"] == color


def test_create_with_unknown_color_falls_back_to_default(client):
    todo = client.post("/api/todos", json={"title": "x", "color": "#123456"}).json()
    assert todo["color"] == main.DEFAULT_COLOR


def test_patch_color(client):
    todo = client.post("/api/todos", json={"title": "x"}).json()
    new = main.COLORS[5]
    res = client.patch(f"/api/todos/{todo['id']}", json={"color": new})
    assert res.status_code == 200
    assert res.json()["color"] == new


def test_patch_unknown_color_rejected(client):
    todo = client.post("/api/todos", json={"title": "x"}).json()
    assert client.patch(f"/api/todos/{todo['id']}", json={"color": "#000"}).status_code == 400


def test_reorder(client):
    a = client.post("/api/todos", json={"title": "a"}).json()
    b = client.post("/api/todos", json={"title": "b"}).json()
    c = client.post("/api/todos", json={"title": "c"}).json()

    res = client.put("/api/todos/reorder", json={"ids": [c["id"], a["id"], b["id"]]})
    assert res.status_code == 200
    assert [t["title"] for t in res.json()] == ["c", "a", "b"]
    # Order persists on a fresh list call.
    assert [t["title"] for t in client.get("/api/todos").json()] == ["c", "a", "b"]


def test_reorder_rejects_mismatched_ids(client):
    a = client.post("/api/todos", json={"title": "a"}).json()
    assert client.put("/api/todos/reorder", json={"ids": [a["id"], 999]}).status_code == 400
