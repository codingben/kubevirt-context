"""Portable, read-only source workspace manager for the KubeVirt organization."""

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

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

import yaml

ORGANIZATION = "kubevirt"
AGENT_EXECUTABLES = ("claude", "codex", "cursor-agent", "opencode", "pi")

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
    """Run a command without a shell. Streams output, unless `capture=True`
    returns stdout and folds stderr into any error.
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
    """Return `OCI_BIN` if set, else the first of podman/docker whose daemon
    responds. Mirrors scripts/run-github-mcp.sh's own detection.
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
    """Discover org repos, save the inventory, then clone (or fast-forward pull
    if `update`) up to `max_repos` (0 = all) concurrently.

    Archived/forked repos are recorded but never cloned. Call `preflight()` first.
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
    """Run `codegraph init` (first run) or `sync` against the project root.

    Relies on codegraph.json's includeIgnored entry, since clones are gitignored.
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


def _read_apm_mcp(paths: Paths) -> list[dict]:
    """Return apm.yml's dependencies.mcp list, the source of truth for every agent's MCP servers."""
    manifest_path = paths.root / "apm.yml"

    try:
        manifest = yaml.safe_load(manifest_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise WorkspaceError(f"cannot read {manifest_path}: {exc}") from exc

    try:
        servers = manifest["dependencies"]["mcp"]
    except (KeyError, TypeError) as exc:
        raise WorkspaceError(f"{manifest_path} has no dependencies.mcp list") from exc

    if not isinstance(servers, list):
        raise WorkspaceError(f"{manifest_path}: dependencies.mcp must be a list")

    return servers


def _server_name(server: object) -> str | None:
    """Return an MCP entry's server name, or None if unreadable.

    Entries may be a bare string (registry package name) or a dict that omits
    `name`; reading defensively keeps one bad entry from breaking the rest
    (see write_pi_mcp() and clean()).
    """
    if isinstance(server, str):
        return server

    if isinstance(server, dict) and isinstance(server.get("name"), str):
        return server["name"]

    return None


def write_pi_mcp(paths: Paths) -> None:
    """Generate .pi/mcp.json from apm.yml's dependencies.mcp, since APM has no Pi adapter.

    Merges into any existing file so a user's own servers survive; only entries
    named in apm.yml are touched. Entries without a literal command/url (e.g.
    registry references apm resolves itself) are skipped for Pi with a warning,
    rather than failing the whole command.
    """
    config_path = paths.root / ".pi" / "mcp.json"

    try:
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read {config_path}: {exc}") from exc

    if not isinstance(config, dict):
        raise WorkspaceError(f"{config_path}: root must be a JSON object")

    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise WorkspaceError(f"{config_path}: mcpServers must be a JSON object")

    for server in _read_apm_mcp(paths):
        name = _server_name(server)

        if name is None:
            logger.warning("apm.yml: skipping an unreadable MCP dependency for Pi: %r", server)
            continue

        if not isinstance(server, dict) or not (server.get("command") or server.get("url")):
            logger.warning(
                "apm.yml: MCP server %r has no command/url for Pi to use directly (likely a "
                "registry reference apm resolves itself); skipping it for Pi",
                name,
            )
            continue

        transport = server.get("transport", "stdio")

        if transport == "stdio":
            entry = {
                "command": server["command"],
                "args": server.get("args", []),
                "cwd": str(paths.root),
            }
            if env := server.get("env"):
                entry["env"] = env
            servers[name] = entry
        elif transport in ("http", "streamable-http"):
            entry = {"url": server["url"]}
            if headers := server.get("headers"):
                entry["headers"] = headers
            servers[name] = entry
        else:
            logger.warning("apm.yml: MCP server %r uses a transport Pi does not support (%r); skipping it for Pi", name, transport)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.rename(config_path)


def mcp(paths: Paths) -> None:
    """Install APM skills and MCP config for every configured agent (per apm.yml's
    targets: list), then generate Pi's.

    Claude Code, Codex, Cursor, and OpenCode launch the github server with a cwd-relative
    path that only resolves when launched from the project root; apm.yml can't fix this,
    and patching the generated configs gets flagged as drift on the next `apm install`.
    write_pi_mcp() instead gives Pi an absolute cwd directly, since this workspace fully
    owns that file — but unlike the other four, Pi doesn't search upward for its config,
    so launched from a subdirectory it loses every server, not just github.
    """
    _require("apm")
    _require_container_runtime()
    logger.info("Installing APM skills and MCP config ...")
    _run(["apm", "install"])
    write_pi_mcp(paths)


def _clean_shared_mcp_json(paths: Paths, rel_path: str, servers_key: str, names: set[str]) -> bool:
    """Remove this workspace's entries from a shared agent MCP config, deleting
    the file only if its server map ends up empty. Returns whether anything changed.
    """
    config_path = paths.root / rel_path

    if not config_path.exists():
        return False

    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%s: cannot parse (%s); left in place", rel_path, exc)
        return False

    if not isinstance(config, dict):
        logger.warning("%s: root is not a JSON object; left in place", rel_path)
        return False

    servers = config.get(servers_key)
    if not isinstance(servers, dict) or not any(name in servers for name in names):
        return False

    for name in names:
        servers.pop(name, None)

    if servers or set(config) - {servers_key}:
        tmp = config_path.with_name(config_path.name + ".tmp")
        tmp.write_text(json.dumps(config, indent=2) + "\n")
        tmp.rename(config_path)
        logger.info("removed MCP entries from %s", rel_path)
    else:
        config_path.unlink()
        logger.info("removing %s", rel_path)

    return True


def _clean_codex_config(paths: Paths, names: set[str]) -> bool:
    """Delete .codex/config.toml only if it holds nothing but this workspace's entries.

    No stdlib TOML writer exists, so unlike the JSON configs this file is never
    surgically edited — it's all-or-nothing. Returns whether it was removed.
    """
    config_path = paths.root / ".codex" / "config.toml"
    rel = config_path.relative_to(paths.root)

    if not config_path.exists():
        return False

    if tomllib is None:
        logger.info("%s: Python 3.11+ required to inspect; remove %s manually if present", rel, ", ".join(sorted(names)))
        return False

    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("%s: cannot parse (%s); left in place", rel, exc)
        return False

    servers = config.get("mcp_servers")
    if not isinstance(servers, dict) or not any(name in servers for name in names):
        return False

    if set(servers) - names or set(config) - {"mcp_servers"}:
        logger.info(
            "%s: holds more than this workspace's entries; left in place, remove %s manually",
            rel,
            ", ".join(sorted(names & set(servers))),
        )
        return False

    config_path.unlink()
    logger.info("removing %s", rel)
    return True


def clean(paths: Paths) -> None:
    """Remove everything `setup` creates: cached clones, the CodeGraph index, this
    workspace's APM skill, and its own entries in each agent's MCP config.

    Shared MCP config files and the .cursor/.opencode directories are left otherwise
    intact, since users or agents may add their own content there.
    """
    owned = [
        paths.cache,
        paths.root / "apm_modules",
        paths.root / ".apm-local",
        paths.root / "apm.lock.yaml",
        paths.root / ".agents" / "skills" / "kubevirt-architecture",
        paths.root / ".claude" / "skills" / "kubevirt-architecture",
        *sorted(paths.root.glob(".codegraph*")),
    ]

    removed = False

    for target in owned:
        if not target.is_symlink() and not target.exists():
            continue

        removed = True
        rel = target.relative_to(paths.root)
        logger.info("removing %s", rel)

        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    for empty_dir in (
        paths.root / ".agents" / "skills",
        paths.root / ".agents",
        paths.root / ".claude" / "skills",
        paths.root / ".claude",
    ):
        if empty_dir.is_dir() and not any(empty_dir.iterdir()):
            empty_dir.rmdir()

    try:
        raw_servers = _read_apm_mcp(paths)
    except WorkspaceError as exc:
        logger.warning("cannot read apm.yml's MCP dependencies (%s); leaving agent MCP config in place", exc)
        raw_servers = []

    names = {name for server in raw_servers if (name := _server_name(server)) is not None}

    if names:
        shared_json = [
            (".mcp.json", "mcpServers"),
            (".cursor/mcp.json", "mcpServers"),
            ("opencode.json", "mcp"),
            (".pi/mcp.json", "mcpServers"),
        ]

        for rel_path, servers_key in shared_json:
            if _clean_shared_mcp_json(paths, rel_path, servers_key, names):
                removed = True

        if _clean_codex_config(paths, names):
            removed = True

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

    mcp_configs = {
        "claude": (".mcp.json", "claude"),
        "codex": (".codex/config.toml", "codex"),
        "cursor": (".cursor/mcp.json", "cursor-agent"),
        "opencode": ("opencode.json", "opencode"),
        "pi": (".pi/mcp.json", "pi"),
    }

    inspect_cmds = []

    for agent, (rel_path, executable) in mcp_configs.items():
        state = "configured" if (paths.root / rel_path).exists() else "not configured"
        logger.info("MCP (%s): %s (%s)", agent, state, rel_path)

        if shutil.which(executable):
            inspect_cmds.append(f"`{executable} mcp list`")

    if inspect_cmds:
        logger.info("Inspect with: %s.", ", ".join(inspect_cmds))


def doctor() -> None:
    """Report which prerequisite executables and services are available."""
    missing: list[str] = []

    for name in ("git", "gh", "codegraph", "apm"):
        path = shutil.which(name)
        logger.info("%s: %s", name, f"OK ({path})" if path else "MISSING")

        if not path:
            missing.append(name)

    agents_found: list[str] = []

    for name in AGENT_EXECUTABLES:
        path = shutil.which(name)
        logger.info("%s: %s", name, f"OK ({path})" if path else "not installed")

        if path:
            agents_found.append(name)

    if not agents_found:
        logger.info("No supported agent installed: %s.", ", ".join(AGENT_EXECUTABLES))
        missing.append("agent")

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
