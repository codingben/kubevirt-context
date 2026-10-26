PYTHON ?= python3
VENV := .venv
WORKSPACE := $(VENV)/bin/workspace

$(WORKSPACE): pyproject.toml $(shell find src -name '*.py')
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e .
	touch $(WORKSPACE)

.PHONY: setup
setup: $(WORKSPACE) ## Clone all repositories, build the CodeGraph index, and install MCP config.
	$(WORKSPACE) setup

.PHONY: clean
clean: $(WORKSPACE) ## Remove everything created by setup (clones, index, MCP config).
	$(WORKSPACE) clean

.PHONY: clones
clones: $(WORKSPACE) ## Clone (or confirm) all repositories without updating existing ones.
	$(WORKSPACE) clones

.PHONY: sync
sync: $(WORKSPACE) ## Fast-forward update all repositories and refresh the CodeGraph index.
	$(WORKSPACE) sync

.PHONY: index
index: $(WORKSPACE) ## Build or refresh the CodeGraph index at the project root.
	$(WORKSPACE) index

.PHONY: status
status: $(WORKSPACE) ## Report discovered/cloned repository counts and index/MCP state.
	$(WORKSPACE) status

.PHONY: mcp
mcp: $(WORKSPACE) ## Install APM skills and MCP config for all configured agents.
	$(WORKSPACE) mcp

.PHONY: doctor
doctor: $(WORKSPACE) ## Check prerequisite executables and services.
	$(WORKSPACE) doctor
