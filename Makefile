.PHONY: run backend frontend install install-backend install-frontend clean

BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV := $(BACKEND_DIR)/.venv
PY := $(VENV)/bin/python

# Install all dependencies (backend venv + frontend node_modules)
install: install-backend install-frontend

install-backend:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r $(BACKEND_DIR)/requirements.txt

install-frontend:
	cd $(FRONTEND_DIR) && npm install

# Serve backend (9900) and frontend (5173) together; Ctrl-C stops both
run:
	@echo "Starting backend on :9900 and frontend on :5173 (Ctrl-C to stop)"
	@trap 'kill 0' INT TERM EXIT; \
	$(PY) -m uvicorn main:app --reload --port 9900 --app-dir $(BACKEND_DIR) & \
	cd $(FRONTEND_DIR) && npm run dev & \
	wait

backend:
	$(PY) -m uvicorn main:app --reload --port 9900 --app-dir $(BACKEND_DIR)

frontend:
	cd $(FRONTEND_DIR) && npm run dev

clean:
	rm -rf $(VENV) $(FRONTEND_DIR)/node_modules $(BACKEND_DIR)/todos.db
