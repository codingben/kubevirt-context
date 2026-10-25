"""Portable, read-only source workspace manager for the KubeVirt organization.

No builds/tests/generators are executed in any cloned upstream repository.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ORGANIZATION = "kubevirt"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9_.-]*$")

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """A workspace command failure meant to be reported to the user without a traceback."""


@dataclasses.dataclass
class Repository:
    """A discovered GitHub repository."""

    name: str
    url: str
    archived: bool
    fork: bool


@dataclasses.dataclass
class Paths:
    """The workspace's filesystem locations, rooted at the project root."""

    root: Path
    cache: Path
    clones: Path
    inventory: Path


def locate() -> Paths:
    root = _project_root()
    cache = root / ".sources"
    return Paths(
        root=root,
        cache=cache,
        clones=cache / "kubevirt",
        inventory=cache / "inventory.json",
    )


def _project_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        out = result.stdout.strip()

        if out:
            return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return Path.cwd()


def _run(args: list[str], cwd: Path | None = None, *, capture: bool = False) -> str:
    """Run a command. By default stream its output to the terminal; with
    `capture=True`, return stdout instead and fold stderr into the error.

    Never uses a shell, so arguments informed by remote repository names
    cannot be interpreted.
    """
    result = subprocess.run(args, cwd=cwd, capture_output=capture, text=capture)

    if result.returncode != 0:
        message = f"command failed ({result.returncode}): {' '.join(args)}"

        if capture and (detail := result.stderr.strip()):
            message += f" — {detail}"

        raise WorkspaceError(message)

    return result.stdout if capture else ""


def _require(name: str) -> None:
    if not shutil.which(name):
        raise WorkspaceError(f"missing `{name}` executable. See README prerequisites.")


def _container_runtime() -> str | None:
    """Return the configured (`OCI_BIN`) or detected container runtime binary.

    Mirrors scripts/run-github-mcp.sh's own podman/docker detection: an
    explicit `OCI_BIN` is trusted as-is, otherwise the first of podman/docker
    whose daemon actually responds wins.
    """
    override = os.environ.get("OCI_BIN")

    if override:
        return override

    for candidate in ("podman", "docker"):
        try:
            if subprocess.run([candidate, "ps"], capture_output=True).returncode == 0:
                return candidate
        except FileNotFoundError:
            pass

    return None


def _require_container_runtime() -> None:
    if not _container_runtime():
        raise WorkspaceError(
            "missing a usable container runtime (checked `podman ps`, `docker ps`); "
            "start one or set OCI_BIN. See README prerequisites."
        )


def preflight(needs_index: bool, needs_apm: bool) -> None:
    """Check that core tools exist and that `gh` is authenticated."""
    for name in ("git", "gh"):
        _require(name)

    if needs_index:
        _require("codegraph")

    if needs_apm:
        _require("apm")
        _require_container_runtime()

    _run(["gh", "auth", "status"], capture=True)


def discover() -> list[Repository]:
    """Fetch all visible org repos using GitHub API pagination, including forks/archives."""
    out = _run(
        [
            "gh",
            "api",
            "--paginate",
            f"orgs/{ORGANIZATION}/repos?per_page=100&type=all",
            "--jq",
            ".[] | [.name, .clone_url, .archived, .fork] | @tsv",
        ],
        capture=True,
    )
    repos: list[Repository] = []
    seen: set[str] = set()
    prefix = f"https://github.com/{ORGANIZATION}/"

    for line in out.splitlines():
        if not line:
            continue

        values = line.split("\t")
        if len(values) != 4:
            raise WorkspaceError("unexpected GitHub API response; cannot safely parse repository record")

        name, url, archived, fork = values
        if not _SAFE_NAME.match(name) or name in (".", ".."):
            raise WorkspaceError(f"unexpected repository name: {name!r}")

        if not url.startswith(prefix) or not url.endswith(".git"):
            raise WorkspaceError(f"unexpected GitHub clone URL for {name}")

        if name in seen:
            raise WorkspaceError(f"duplicate repository name in GitHub API response: {name!r}")

        seen.add(name)
        repos.append(Repository(name=name, url=url, archived=archived == "true", fork=fork == "true"))

    if not repos:
        raise WorkspaceError("repository inventory is empty; GitHub API returned no repositories")

    repos.sort(key=lambda r: r.name.lower())
    return repos


def _save_inventory(paths: Paths, repos: list[Repository]) -> None:
    paths.cache.mkdir(parents=True, exist_ok=True)
    payload = {
        "organization": ORGANIZATION,
        "discovered_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repositories": [dataclasses.asdict(repo) for repo in repos],
    }
    tmp = paths.inventory.with_name(paths.inventory.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.rename(paths.inventory)


def _checkout(paths: Paths, repo: Repository, update: bool) -> str:
    dest = paths.clones / repo.name

    if dest.is_dir():
        if not (dest / ".git").exists():
            raise WorkspaceError(f"refusing to overwrite a non-Git directory: {dest}")

        if update:
            # Never reset/force/update branches destructively. Failure reports local divergence.
            _run(["git", "pull", "--ff-only", "--depth", "1"], cwd=dest)
            return f"updated {repo.name}"

        return f"present {repo.name}"

    _run(["git", "clone", "--depth", "1", "--no-tags", repo.url, str(dest)], cwd=paths.root)
    return f"cloned {repo.name}"


def clone_all(paths: Paths, update: bool, max_repos: int, workers: int) -> None:
    """Discover org repositories, save the inventory, and clone or (if update) fast-forward
    pull each one, up to max_repos (0 means all), using the given concurrency.

    Archived and forked repositories are recorded in the inventory but never cloned.

    Callers must run `preflight()` first.
    """
    logger.info("Discovering repositories in github.com/%s ...", ORGANIZATION)
    repos = discover()
    _save_inventory(paths, repos)
    logger.info("Discovered %d repositories (including archives and forks).", len(repos))

    skipped = [repo.name for repo in repos if repo.archived or repo.fork]
    repos = [repo for repo in repos if not repo.archived and not repo.fork]

    if skipped:
        logger.info("Skipping %d archived/forked repositories (not cloned): %s", len(skipped), ", ".join(skipped))

    if max_repos > 0 and max_repos < len(repos):
        repos = repos[:max_repos]
        logger.info("Sample mode: cloning/updating only %d repositories.", len(repos))

    paths.clones.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_checkout, paths, repo, update): repo for repo in repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                logger.info("%s", future.result())
            except (WorkspaceError, OSError) as exc:
                failures.append(repo.name)
                logger.error("%s: %s", repo.name, exc)

    if failures:
        shown = ", ".join(failures[:8])
        raise WorkspaceError(f"{len(failures)} repositories failed ({shown}); retry the command")


def index(paths: Paths, init: bool) -> None:
    """Run `codegraph init` (first run) or `codegraph sync` against the project root.

    Cloned repositories are otherwise excluded from indexing, so the
    includeIgnored entry in codegraph.json is essential here.
    """
    _require("codegraph")
    any_git_repo = paths.clones.is_dir() and any((e / ".git").exists() for e in paths.clones.iterdir())

    if not any_git_repo:
        raise WorkspaceError("no cloned repositories found; run `make clones` first")

    sub = "sync"
    if init and not (paths.root / ".codegraph").exists():
        sub = "init"

    cmd = ["codegraph", sub, str(paths.root)]
    logger.info("Running %s", " ".join(cmd))
    _run(cmd, cwd=paths.root)


def mcp() -> None:
    """Install APM skills and MCP config for OpenCode."""
    _require("apm")
    _require_container_runtime()
    logger.info("Installing APM skills and MCP config for OpenCode ...")
    _run(["apm", "install", "--target", "opencode"])


def clean(paths: Paths) -> None:
    """Remove everything Setup creates.

    clone_all writes cache, index writes .codegraph/ (sync also leaves dated
    .codegraph-* snapshots), and mcp writes the rest via `apm install`.
    .opencode/ is pre-existing and version-controlled, so it is left alone.
    """
    targets = [
        paths.cache,
        paths.root / "apm_modules",
        paths.root / ".agents",
        paths.root / ".apm-local",
        paths.root / "apm.lock.yaml",
        paths.root / "opencode.json",
        *sorted(paths.root.glob(".codegraph*")),
    ]

    removed = False

    for target in targets:
        if not target.is_symlink() and not target.exists():
            continue

        removed = True
        rel = target.relative_to(paths.root)
        logger.info("removing %s", rel)

        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    logger.info("Clean." if removed else "Nothing to clean.")


def status(paths: Paths) -> None:
    """Report discovered/cloned repository counts and index/MCP state."""
    if paths.inventory.exists():
        try:
            inventory = json.loads(paths.inventory.read_text())
            repos = inventory["repositories"]
            installed = sum(1 for repo in repos if (paths.clones / repo["name"] / ".git").exists())
            discovered_at = inventory.get("discovered_at_utc") or "unknown"
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise WorkspaceError(f"{paths.inventory} is corrupt; run `make clones` to regenerate it") from exc
        logger.info("GitHub org: %s; %d discovered, %d cloned", ORGANIZATION, len(repos), installed)
        logger.info("Inventory as of: %s", discovered_at)
    else:
        logger.info("No inventory yet. Run `make clones`.")

    if (paths.root / ".codegraph").exists() and shutil.which("codegraph"):
        _run(["codegraph", "status", str(paths.root)], cwd=paths.root)
    else:
        logger.info("No CodeGraph index at project root yet.")

    logger.info("MCP: inspect using `opencode mcp list`.")


def doctor() -> None:
    """Report which prerequisite executables and services are available."""
    missing: list[str] = []

    for name in ("git", "gh", "codegraph", "apm", "opencode"):
        path = shutil.which(name)
        logger.info("%s: %s", name, f"OK ({path})" if path else "MISSING")

        if not path:
            missing.append(name)

    if shutil.which("gh"):
        try:
            _run(["gh", "auth", "status"], capture=True)
            logger.info("GitHub authentication: OK")
        except WorkspaceError:
            logger.info("GitHub authentication: FAILED (run `gh auth login`)")
            missing.append("gh auth")

    runtime = _container_runtime()

    if not runtime:
        logger.info("Container runtime: MISSING (checked podman, docker; or set OCI_BIN)")
        missing.append("container runtime")
    else:
        try:
            _run([runtime, "info"], capture=True)
            logger.info("Container runtime (%s): OK (%s)", runtime, shutil.which(runtime))
        except (WorkspaceError, OSError):
            logger.info("Container runtime (%s): UNAVAILABLE", runtime)
            missing.append(runtime)

    if missing:
        raise WorkspaceError(f"missing/unavailable prerequisites: {', '.join(missing)}")

    logger.info("Prerequisites available. Run `make setup`.")
