# KubeVirt Context

AI-powered KubeVirt code intelligence with cross-repository analysis and GitHub history using [OpenCode](https://opencode.ai/) and MCP.

## Introduction

Every repository in the [KubeVirt](https://github.com/kubevirt) organization is cloned locally with only the latest commit and indexed into a single [CodeGraph](https://github.com/colbymchenry/codegraph), allowing the AI agent to understand how code is connected across repositories.

A local [GitHub MCP server](https://github.com/github/github-mcp-server) adds the missing half — Pull requests, issues, and review history — and [Agent Package Manager](https://microsoft.github.io/apm/) wires both into OpenCode. The [kubevirt-architecture skill](.apm/skills/kubevirt-architecture/SKILL.md) guides the AI agent in understanding KubeVirt's architecture and tracing behavior across repositories.

Everything stays read-only. The workspace traces code, it never builds, tests, or edits it.

## Example Q&A

User asked:

> Are there duplicate implementations of the VM object graph?

AI agent (_using the GLM 5.3 model_) replied:

> **Yes — there appear to be two**. [VEP 41](https://github.com/kubevirt/enhancements/issues/41) introduced the `objectgraph` API in 2025 (Alpha) while the Velero plugin has its own `kvgraph` implementation (2023). The two implementations appear to share similar logic. [VEP 41](https://github.com/kubevirt/enhancements/issues/41) targets Beta graduation, but whether that includes merging the implementations should be verified against the proposal.

## Prerequisites

| Tool                                                              | Summary                                                 |
| ----------------------------------------------------------------- | ------------------------------------------------------- |
| [Python 3.10+](https://www.python.org/)                           | Running the workspace CLI and cloning sources           |
| [GitHub CLI](https://cli.github.com/)                             | Discovering and cloning the organization's repositories |
| [OpenCode](https://opencode.ai/)                                  | The agent you ask questions                             |
| [APM](https://microsoft.github.io/apm/)                           | Installing the MCP servers and research skill           |
| [CodeGraph](https://github.com/colbymchenry/codegraph)            | Indexing symbols and references across clones           |
| [Podman](https://podman.io/) or [Docker](https://www.docker.com/) | Running the GitHub MCP server locally                   |

## Installation

**Step 1 — Install `gh` and Podman via your package manager, then install APM**:

```sh
curl -sSL https://aka.ms/apm-unix | sh
```

**Step 2 — Install CodeGraph**:

```sh
npm install -g @colbymchenry/codegraph
```

**Step 3 — Install OpenCode**:

```sh
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

**Step 8 — Launch OpenCode**:

```sh
opencode
```

## Usage

| Command       | Description                                                            |
| ------------- | ---------------------------------------------------------------------- |
| `make setup`  | Clone every repository, build the index, install MCP config (one-time) |
| `make status` | Inventory + graph status                                               |
| `make sync`   | Refresh clones, discover new repositories, update the graph            |
| `make index`  | Rebuild the CodeGraph index                                            |
| `make mcp`    | Redeploy MCP/skill config via APM                                      |
| `make doctor` | Check dependencies and auth                                            |
| `make clean`  | Remove everything `make setup` created                                 |

For a smaller trial run, use the CLI directly: `.venv/bin/workspace clones --max-repos 4 && make index`.

## Notes

- Clones are shallow (`--depth 1`), read-only snapshots: no history, no builds/tests, no edits.
- `apm.yml` is the source of truth for MCP servers; don't run `codegraph install` separately against this project.
- GitHub MCP container runs with `GITHUB_READ_ONLY=1` and is scoped to the `repos`, `issues`, and `pull_requests` toolsets, so the agent cannot write to GitHub.

## License

[Apache License 2.0](LICENSE)
