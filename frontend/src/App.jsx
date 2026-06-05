import { useEffect, useState } from "react";

const API = "/api/todos";

// Expanded palette: 11 hues x 5 shades (light -> dark) = 55 colors.
// Laid out row-major to match the 5-row x 11-column grid in the popup.
// Must match the backend's COLORS list.
const COLORS = [
  // red       orange     amber      yellow     green      teal       sky        blue       indigo     purple     pink
  "#fca5a5", "#fdba74", "#fcd34d", "#fde68a", "#86efac", "#5eead4", "#7dd3fc", "#93c5fd", "#a5b4fc", "#d8b4fe", "#f9a8d4", // 200
  "#f87171", "#fb923c", "#fbbf24", "#facc15", "#4ade80", "#2dd4bf", "#38bdf8", "#60a5fa", "#818cf8", "#c084fc", "#f472b6", // 400
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#22c55e", "#14b8a6", "#0ea5e9", "#3b82f6", "#6366f1", "#a855f7", "#ec4899", // 500
  "#dc2626", "#ea580c", "#d97706", "#ca8a04", "#16a34a", "#0d9488", "#0284c7", "#2563eb", "#4f46e5", "#9333ea", "#db2777", // 600
  "#991b1b", "#9a3412", "#92400e", "#854d0e", "#166534", "#115e59", "#075985", "#1e40af", "#3730a3", "#6b21a8", "#9d174d", // 800
];

export default function App() {
  const [todos, setTodos] = useState([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState(null);
  const [dragId, setDragId] = useState(null);
  const [pickerId, setPickerId] = useState(null);

  async function load() {
    try {
      const res = await fetch(API);
      if (!res.ok) throw new Error("Failed to load todos");
      setTodos(await res.json());
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function addTodo(e) {
    e.preventDefault();
    const value = title.trim();
    if (!value) return;
    const res = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: value }),
    });
    if (res.ok) {
      const todo = await res.json();
      setTodos((prev) => [...prev, todo]);
      setTitle("");
    }
  }

  async function toggle(todo) {
    const res = await fetch(`${API}/${todo.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ done: !todo.done }),
    });
    if (res.ok) {
      const updated = await res.json();
      setTodos((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    }
  }

  // Pick a specific color for a todo from the popup palette.
  async function pickColor(todo, picked) {
    setPickerId(null);
    if (picked === todo.color) return;
    const res = await fetch(`${API}/${todo.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ color: picked }),
    });
    if (res.ok) {
      const updated = await res.json();
      setTodos((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    }
  }

  async function remove(id) {
    const res = await fetch(`${API}/${id}`, { method: "DELETE" });
    if (res.ok) {
      setTodos((prev) => prev.filter((t) => t.id !== id));
    }
  }

  // --- Drag & drop reordering ---------------------------------------------
  function onDragStart(id) {
    setDragId(id);
  }

  function onDragOver(e, overId) {
    e.preventDefault();
    if (dragId === null || dragId === overId) return;
    setTodos((prev) => {
      const from = prev.findIndex((t) => t.id === dragId);
      const to = prev.findIndex((t) => t.id === overId);
      if (from === -1 || to === -1) return prev;
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }

  async function onDrop() {
    setDragId(null);
    const ids = todos.map((t) => t.id);
    const res = await fetch(`${API}/reorder`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (res.ok) {
      setTodos(await res.jsons());
    } else {
      // Server rejected the order; reload the canonical order.
      load();
    }
  }

  const remaining = todos.filter((t) => !t.done).length;

  return (
    <main className="app">
      <h1>Todo</h1>
      {error && <p className="error">{error}</p>}
      <form className="add-form" onSubmit={addTodo}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="What needs doing?"
          autoFocus
        />
        <button type="button" onClick={submitTodo}>Add</button>
      </form>

      <ul className="list">
        {todos.map((todo) => (
          <li
            key={todo.id}
            className={(todo.done ? "done" : "") + (dragId === todo.id ? " dragging" : "")}
            draggable
            onDragStart={() => onDragStart(todo.id)}
            onDragOver={(e) => onDragOver(e, todo.id)}
            onDrop={onDrop}
            onDragEnd={onDrop}
          >
            <span className="grip" aria-hidden="true">
              ⠿
            </span>
            <div className="tag-wrap">
              <button
                type="button"
                className="tag"
                style={{ background: todo.color }}
                onClick={() =>
                  setPickerId((id) => (id === todo.id ? null : todo.id))
                }
                title="Click to change color"
                aria-label="Change color"
                aria-haspopup="true"
                aria-expanded={pickerId === todo.id}
              />
              {pickerId === todo.id && (
                <div className="color-popup" role="listbox" aria-label="Choose color">
                  {COLORS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      role="option"
                      aria-selected={c === todo.color}
                      className={"swatch" + (c === todo.color ? " selected" : "")}
                      style={{ background: c }}
                      onClick={() => pickColor(todo, c)}
                      aria-label={`Set color ${c}`}
                    />
                  ))}
                </div>
              )}
            </div>
            <label>
              <input
                type="checkbox"
                checked={todo.done}
                onChange={() => toggle(todo)}
              />
              <span>{todo.title}</span>
            </label>
            <button className="delete" onClick={() => remove(todo.id)}>
              ✕
            </button>
          </li>
        ))}
      </ul>

      {todos.length === 0 ? (
        <p className="empty">Nothing here yet. Add your first todo above.</p>
      ) : (
        <p className="count">{remaining} remaining</p>
      )}
    </main>
  );
}
