# KubeVirt Context

AI-powered KubeVirt code intelligence with cross-repository analysis and GitHub history, powered by MCP servers and compatible with [Pi](https://github.com/earendil-works/pi), [Codex](https://developers.openai.com/codex/cli/), [Claude Code](https://claude.com/claude-code), [Cursor Agent](https://cursor.com/), and [OpenCode](https://opencode.ai/).

## Overview

Every repository in the [KubeVirt](https://github.com/kubevirt) organization is shallow-cloned at the latest commit and indexed into a single [CodeGraph](https://github.com/colbymchenry/codegraph), enabling AI agents to understand how code is connected across repositories through the **CodeGraph MCP Server**.

The **GitHub MCP Server** provides access to pull requests, issues, and code review history. The [Agent Package Manager](https://microsoft.github.io/apm/) integrates both MCP servers with **Pi**, **Codex**, **Claude Code**, **Cursor Agent**, or **OpenCode**, guided by the [kubevirt-architecture skill](.apm/skills/kubevirt-architecture/SKILL.md).

## Example Q&A

User asked:

> Are there duplicate implementations of the VM object graph?

AI agent replied (_using the GLM 5.3 model_):

> **Yes — there appear to be two**. [VEP 41](https://github.com/kubevirt/enhancements/issues/41) introduced the `objectgraph` API in 2025 (Alpha) while the Velero plugin has its own `kvgraph` implementation (2023). The two implementations appear to share similar logic. [VEP 41](https://github.com/kubevirt/enhancements/issues/41) targets Beta graduation, but whether that includes merging the implementations should be verified against the proposal.

## Prerequisites

| Tool                                                                                                                                                                                                             | Summary                                                 |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| [Python 3.10+](https://www.python.org/)                                                                                                                                                                          | Running the workspace CLI and cloning sources           |
| [GitHub CLI](https://cli.github.com/)                                                                                                                                                                            | Discovering and cloning the organization's repositories |
| [Pi](https://github.com/earendil-works/pi), [Codex](https://developers.openai.com/codex/cli/), [Claude Code](https://claude.com/claude-code), [Cursor](https://cursor.com/), or [OpenCode](https://opencode.ai/) | The AI agent you ask questions                          |
| [APM](https://microsoft.github.io/apm/)                                                                                                                                                                          | Installing the MCP servers and research skill           |
| [CodeGraph](https://github.com/colbymchenry/codegraph)                                                                                                                                                           | Indexing symbols and references across clones           |
| [Podman](https://podman.io/) or [Docker](https://www.docker.com/)                                                                                                                                                | Running the GitHub MCP server locally                   |

## Installation

**Step 1 — Install `gh` and Podman via your package manager, then install APM**:

```sh
curl -sSL https://aka.ms/apm-unix | sh
```

**Step 2 — Install CodeGraph**:

```sh
npm install -g @colbymchenry/codegraph
```

**Step 3 — Install an agent** (pick one or more):

```sh
# Pi
curl -fsSL https://pi.dev/install.sh | sh

# Codex
npm install -g @openai/codex

# Claude Code
curl -fsSL https://claude.ai/install.sh | bash

# Cursor CLI
curl https://cursor.com/install -fsS | bash

# OpenCode
curl -fsSL https://opencode.ai/install | bash
```

**Step 4 — Authenticate the GitHub CLI**:

```sh
gh auth login
```

**Step 5 — Clone this repository**:

```sh
git clone https://github.com/codingben/kubevirt-context.git
cd kubevirt-context
```

**Step 6 — Verify prerequisites are installed and authenticated**:

```sh
make doctor
```

**Step 7 — Clone every kubevirt repository, build the CodeGraph index, and install the MCP config**:

```sh
make setup
```

**Step 8 — Launch your AI agent**:

```sh
opencode # or and: pi | codex | claude | cursor-agent
```

## Usage

| Command       | Description                                                            |
| ------------- | ---------------------------------------------------------------------- |
| `make setup`  | Clone every repository, build the index, install MCP config (one-time) |
| `make status` | Inventory and graph status                                             |
| `make sync`   | Refresh clones, discover new repositories, update the graph            |
| `make index`  | Rebuild the CodeGraph index                                            |
| `make mcp`    | Redeploy MCP/skill config for every configured agent                   |
| `make doctor` | Check dependencies and auth                                            |
| `make clean`  | Remove everything `make setup` created                                 |

For a smaller trial run, use the CLI directly: `.venv/bin/workspace clones --max-repos 4 && make index`.

## Notes

- Clones are shallow (`--depth 1`), read-only snapshots: no history, no builds/tests, no edits.
- `apm.yml` is the source of truth for MCP servers for every agent, including Pi: APM has no Pi adapter, so `make mcp` generates `.pi/mcp.json` from the same manifest.
- GitHub MCP container runs with `GITHUB_READ_ONLY=1` and is scoped to the `repos`, `issues`, and `pull_requests` toolsets, so the agent cannot write to GitHub.

## License

[Apache License 2.0](LICENSE)
