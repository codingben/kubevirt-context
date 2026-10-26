"""Click CLI for the KubeVirt organization research workspace, wiring the
commands to :mod:`workspace`.
"""

from __future__ import annotations

import logging
import sys
import click
import workspace as ws


def _clone_options(f):
    f = click.option(
        "--max-repos", type=click.IntRange(min=1), default=None, help="for a small sample; omit for all repos"
    )(f)
    f = click.option(
        "--workers", type=click.IntRange(1, 16), default=4, show_default=True, help="concurrent git operations"
    )(f)
    return f


@click.group()
def cli() -> None:
    """KubeVirt organization research workspace CLI."""


@cli.command()
@_clone_options
def setup(max_repos: int | None, workers: int) -> None:
    """Clone all repositories, build the CodeGraph index, and install MCP config."""
    paths = ws.locate()
    ws.preflight(needs_index=True, needs_apm=True)
    ws.clone_all(paths, update=False, max_repos=max_repos or 0, workers=workers)
    ws.index(paths, init=True)
    ws.mcp(paths)


@cli.command()
@_clone_options
def clones(max_repos: int | None, workers: int) -> None:
    """Clone (or confirm) all repositories without updating existing ones."""
    paths = ws.locate()
    ws.preflight(needs_index=False, needs_apm=False)
    ws.clone_all(paths, update=False, max_repos=max_repos or 0, workers=workers)


@cli.command()
@_clone_options
def sync(max_repos: int | None, workers: int) -> None:
    """Fast-forward update all repositories and refresh the CodeGraph index."""
    paths = ws.locate()
    ws.preflight(needs_index=True, needs_apm=False)
    ws.clone_all(paths, update=True, max_repos=max_repos or 0, workers=workers)
    ws.index(paths, init=True)


@cli.command()
def index() -> None:
    """Build or refresh the CodeGraph index at the project root."""
    ws.index(ws.locate(), init=True)


@cli.command()
def status() -> None:
    """Report discovered/cloned repository counts and index/MCP state."""
    ws.status(ws.locate())


@cli.command()
def mcp() -> None:
    """Install APM skills and MCP config for all configured agents."""
    ws.mcp(ws.locate())


@cli.command()
def doctor() -> None:
    """Check prerequisite executables and services."""
    ws.doctor()


@cli.command()
def clean() -> None:
    """Remove everything created by setup (clones, index, MCP config)."""
    ws.clean(ws.locate())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        cli()
    except (ws.WorkspaceError, OSError) as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
