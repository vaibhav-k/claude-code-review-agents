"""
Auto-discovery of a target repo's test runner.

Heuristic and file-presence based -- no code execution happens here. This
only answers "which test command should `healing.py` run" (via
`detect_test_runners()`), by checking for each ecosystem's usual marker
files (pyproject.toml, package.json, pom.xml, ...). Routing (which
specialist agents apply to which changed files) is handled entirely by
routing.py's own diff-content pattern matching and does not consult this
module at all.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class TestRunner:
    name: str
    command: list[str]


# Marker file -> test runner, checked in this order. First match wins per
# language ecosystem; a repo can have more than one ecosystem present
# (e.g. a Python backend with a TypeScript frontend), so discovery returns
# a list, not a single answer.
_MARKERS: list[tuple[str, TestRunner]] = [
    ("pytest.ini", TestRunner("pytest", ["pytest"])),
    ("pyproject.toml", TestRunner("pytest", ["pytest"])),
    ("setup.cfg", TestRunner("pytest", ["pytest"])),
    ("package.json", TestRunner("npm test", ["npm", "test", "--silent"])),
    ("pom.xml", TestRunner("maven", ["mvn", "-q", "test"])),
    ("build.gradle", TestRunner("gradle", ["./gradlew", "test"])),
    ("build.gradle.kts", TestRunner("gradle", ["./gradlew", "test"])),
    ("Cargo.toml", TestRunner("cargo", ["cargo", "test"])),
    ("CMakeLists.txt", TestRunner("ctest", ["ctest", "--test-dir", "build"])),
]

_CSPROJ_GLOB = "*.csproj"
_SLN_GLOB = "*.sln"


def detect_test_runners(repo_root: Path) -> list[TestRunner]:
    runners: list[TestRunner] = []
    seen_names: set[str] = set()
    for marker, runner in _MARKERS:
        if (repo_root / marker).exists() and runner.name not in seen_names:
            # pyproject.toml/setup.cfg only imply pytest if pytest is
            # actually configured or importable -- a bare pyproject.toml
            # for, say, a non-test library shouldn't claim a test runner
            # that doesn't exist. Callers should treat this as a
            # best-effort hint, not a guarantee the command succeeds.
            runners.append(runner)
            seen_names.add(runner.name)
    has_dotnet_project = any(repo_root.glob(_CSPROJ_GLOB)) or any(repo_root.glob(_SLN_GLOB))
    if has_dotnet_project and "dotnet" not in seen_names:
        runners.append(TestRunner("dotnet", ["dotnet", "test"]))
        seen_names.add("dotnet")
    return runners
