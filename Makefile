VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: install chat ui test eval eval-report lint format

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

chat:
	$(PY) -m emporio_agent.cli

ui:
	$(VENV)/bin/streamlit run app.py

test:
	$(PY) -m pytest

eval:
	$(PY) -m evals.run $(ARGS)

eval-report:
	$(PY) -m evals.report

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
