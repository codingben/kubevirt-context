#!/usr/bin/env sh
set -eu

OCI_BIN="${OCI_BIN:-$(if podman ps >/dev/null 2>&1; then echo podman; elif docker ps >/dev/null 2>&1; then echo docker; fi)}"

if [ -z "$OCI_BIN" ]; then
    echo "GitHub MCP: no running container runtime found (checked podman, docker); start one or set OCI_BIN." >&2
    exit 1
fi

exec "$OCI_BIN" run --rm -i \
    -e GITHUB_PERSONAL_ACCESS_TOKEN="$(gh auth token)" \
    -e GITHUB_READ_ONLY=1 \
    -e GITHUB_TOOLSETS=repos,issues,pull_requests \
    ghcr.io/github/github-mcp-server
