"""Confirms `requirements.txt` and `requirements-dev.txt` actually mirror
`pyproject.toml`'s `[project.dependencies]` and
`[project.optional-dependencies].dev` -- both files say "keep in sync
with pyproject.toml" in their own header comments, but until this test
existed nothing enforced it. That gap was real, not hypothetical: adding
`jsonschema` (for test_sarif_schema_validation.py) to pyproject.toml's
`dev` extra and forgetting to also add it to requirements-dev.txt is
exactly the drift this test exists to catch, and exactly what happened
during that same round before this test was written.

This is the same class of problem `test_default_rules_sync.py` (one
directory over conceptually -- CLAUDE.md/agents/*.md vs. the bundled
default_rules/ copies) already solves for prompt content: a value that
has to be hand-mirrored into a second file, with no single source of
truth a human can't simply forget to update. Same fix here: a test that
reads both sides and fails loudly the moment they disagree, instead of
a comment asking a future contributor to remember.

Deliberately NOT exact string-set equality (that was this test's first
draft, and it broke the very first time someone pinned exact versions in
requirements.txt/-dev.txt -- e.g. `anthropic==1.4.0` -- as a reproducible
lockfile, rather than repeating pyproject.toml's own looser `>=0.74.0`
bound verbatim). Pinning exact versions in a requirements file while
pyproject.toml states a looser compatible range for the installable
package is a normal, legitimate pattern (pyproject.toml describes what
versions the package *can* run against; a requirements file records
what a specific, reproducible install actually uses), so a test that
demanded byte-identical specifier strings was enforcing the wrong
invariant. What actually matters -- and what this test now checks --
is: the same set of packages appears on both sides, environment markers
match, and whatever version constraint a requirements file states is
compatible with pyproject.toml's (an exact pin must satisfy
pyproject.toml's specifier; anything else must match it exactly). That
still catches the original bug this test was written for (a forgotten
addition or removal shows up as a name-set mismatch) without breaking
every time someone re-pins a lockfile to a newer patch release.
"""

import sys
from pathlib import Path

# packaging is not declared as its own dependency here -- it's already a
# guaranteed transitive dependency of pytest>=7 (pytest itself requires
# it), which is already in [project.optional-dependencies].dev, so every
# environment this test runs in has it without this file needing to ask
# for it a second time.
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_CLI_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT_PATH = _CLI_ROOT / "pyproject.toml"
_REQUIREMENTS_PATH = _CLI_ROOT / "requirements.txt"
_REQUIREMENTS_DEV_PATH = _CLI_ROOT / "requirements-dev.txt"


def _load_pyproject() -> dict:
    with _PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def _active_requirements_by_name(path: Path) -> dict[str, Requirement]:
    """Every non-comment, non-blank line (minus `-r other_file.txt`
    include directives -- a file-wiring detail, not a dependency to
    mirror), parsed as a PEP 508 requirement and keyed by its
    canonicalized package name (so `types-PyYAML` and `types-pyyaml`,
    say, are recognized as the same package regardless of casing).
    """
    result: dict[str, Requirement] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        req = Requirement(line)
        result[canonicalize_name(req.name)] = req
    return result


def _assert_mirrors(
    pyproject_requirement_strings: list[str], requirements_path: Path, label: str
) -> None:
    expected = {
        canonicalize_name(Requirement(s).name): Requirement(s)
        for s in pyproject_requirement_strings
    }
    actual = _active_requirements_by_name(requirements_path)

    missing = expected.keys() - actual.keys()
    extra = actual.keys() - expected.keys()

    assert not missing, f"{label} is missing packages from pyproject.toml: {sorted(missing)}"
    assert not extra, f"{label} contains packages not listed in pyproject.toml: {sorted(extra)}"

    problems = []
    for name, exp_req in expected.items():
        act_req = actual[name]
        if str(exp_req.marker) != str(act_req.marker):
            problems.append(
                f"{name}: environment marker differs (pyproject.toml: "
                f"{exp_req.marker!r}, {label}: {act_req.marker!r})"
            )
            continue
        if str(exp_req.specifier) == str(act_req.specifier):
            continue  # byte-identical mirror -- the common case
        # Allow `label` to pin a single exact version instead of
        # repeating pyproject.toml's own looser bound, as long as that
        # pinned version actually satisfies pyproject.toml's specifier.
        act_specs = list(act_req.specifier)
        is_single_pin = len(act_specs) == 1 and act_specs[0].operator == "=="
        if is_single_pin and exp_req.specifier.contains(act_specs[0].version, prereleases=True):
            continue
        problems.append(
            f"{name}: {label} has {str(act_req.specifier) or '(unconstrained)'}, "
            f"which doesn't satisfy pyproject.toml's {exp_req.specifier}"
        )
    assert not problems, f"{label} has drifted from pyproject.toml:\n" + "\n".join(problems)


def test_requirements_txt_matches_pyproject_dependencies():
    pyproject = _load_pyproject()
    _assert_mirrors(pyproject["project"]["dependencies"], _REQUIREMENTS_PATH, "requirements.txt")


def test_requirements_dev_txt_matches_pyproject_dev_extra():
    pyproject = _load_pyproject()
    _assert_mirrors(
        pyproject["project"]["optional-dependencies"]["dev"],
        _REQUIREMENTS_DEV_PATH,
        "requirements-dev.txt",
    )


def test_requirements_dev_txt_still_includes_requirements_txt():
    # Not a version-mirroring concern like the two tests above -- this
    # just guards the "-r requirements.txt" include line itself, so a dev
    # install via `pip install -r requirements-dev.txt` alone still pulls
    # in the runtime dependencies too, without needing both files listed
    # in one pip invocation.
    text = _REQUIREMENTS_DEV_PATH.read_text(encoding="utf-8")
    assert "-r requirements.txt" in text
