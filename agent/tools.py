"""Custom tools for the Strands code-editor agent.

These complement the vended ``strands_tools`` (file_read, file_write, editor,
shell, python_repl) with project-navigation helpers tailored to editing a
Python application. All paths are resolved against the agent's working
directory, which agent.py sets to the shared workspace.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from strands import tool

# Directories that are never interesting when navigating a Python project.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".eggs",
    "site-packages",
}


def _workspace_root() -> Path:
    """The workspace the agent operates in (its current working directory)."""
    return Path(os.getcwd()).resolve()


def _resolve_in_workspace(path: str) -> Path:
    root = _workspace_root()
    target = (root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"path '{path}' is outside the workspace")
    return target


def _should_skip_dir(entry: Path) -> bool:
    return entry.name in _SKIP_DIRS or entry.name.endswith(".egg-info")


@tool
def list_files(path: str = ".", max_depth: int = 4) -> str:
    """List files and directories under a path as an indented tree.

    Use this to explore the project structure before editing. It skips build
    artifacts, virtualenvs, and caches.

    Args:
        path: Relative path from the workspace root to list. Defaults to ".".
        max_depth: Maximum recursion depth. Defaults to 4. Keep it small to
            avoid large outputs for big trees.

    Returns:
        A newline-separated tree of paths relative to the workspace root.
        Directories are suffixed with "/". Returns "(empty)" if nothing to show.
    """
    try:
        base = _resolve_in_workspace(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not base.exists():
        return f"Error: '{path}' does not exist"
    if not base.is_dir():
        return f"Error: '{path}' is not a directory"

    lines: list[str] = []

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(
                current.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.is_dir() and _should_skip_dir(entry):
                continue
            rel = entry.relative_to(_workspace_root()).as_posix()
            if entry.is_dir():
                lines.append(f"{rel}/")
                walk(entry, depth + 1)
            else:
                lines.append(rel)

    walk(base, 0)
    return "\n".join(lines) if lines else "(empty)"


@tool
def grep_files(
    pattern: str,
    path: str = ".",
    include: str = "*.py",
    max_results: int = 100,
) -> str:
    """Search file contents for a regular expression.

    Args:
        pattern: A Python regular expression to search for in file contents.
        path: Relative path from the workspace root to search under. Defaults
            to "." (the whole workspace).
        include: Glob pattern for files to include, e.g. "*.py" or "*.txt".
            Defaults to "*.py". Use "*" to search every file regardless of type.
        max_results: Maximum number of matching lines to return. Defaults to 100.

    Returns:
        Matching lines as "relative/path:lineno: <line>", truncated to
        max_results. Returns "(no matches)" when nothing matches.
    """
    try:
        base = _resolve_in_workspace(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if not base.exists():
        return f"Error: '{path}' does not exist"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"

    root = _workspace_root()
    count = 0
    out: list[str] = []
    for entry in base.rglob(include):
        if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in entry.parts):
            continue
        if not entry.is_file():
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = entry.relative_to(root).as_posix()
                out.append(f"{rel}:{i}: {line[:300]}")
                count += 1
                if count >= max_results:
                    out.append(f"... (truncated at {max_results} matches)")
                    return "\n".join(out)
    return "\n".join(out) if out else "(no matches)"
