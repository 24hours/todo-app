import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App.jsx";

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("App", () => {
  it("renders the heading and empty state when there are no todos", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse([]));

    render(<App />);

    expect(screen.getByRole("heading", { name: "Todo" })).toBeInTheDocument();
    expect(await screen.findByText(/nothing here yet/i)).toBeInTheDocument();
  });

  it("renders todos returned by the API", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse([{ id: 1, title: "buy milk", done: false }])
    );

    render(<App />);

    expect(await screen.findByText("buy milk")).toBeInTheDocument();
    expect(screen.getByText(/1 remaining/i)).toBeInTheDocument();
  });

  it("adds a todo via the form", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(jsonResponse([])) // initial load
      .mockResolvedValueOnce(jsonResponse({ id: 2, title: "walk dog", done: false }, true, 201));

    render(<App />);
    await screen.findByText(/nothing here yet/i);

    await userEvent.type(screen.getByPlaceholderText(/what needs doing/i), "walk dog");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByText("walk dog")).toBeInTheDocument();

    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/todos");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ title: "walk dog" });
  });
});
