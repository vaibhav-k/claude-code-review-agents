import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import discovery


def test_detect_test_runners_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    runners = discovery.detect_test_runners(tmp_path)
    assert any(r.name == "pytest" for r in runners)


def test_detect_test_runners_multi_ecosystem(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pom.xml").write_text("<project/>")
    runners = {r.name for r in discovery.detect_test_runners(tmp_path)}
    assert runners == {"npm test", "maven"}


def test_detect_test_runners_dotnet_via_csproj(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>")
    runners = {r.name for r in discovery.detect_test_runners(tmp_path)}
    assert runners == {"dotnet"}


def test_detect_test_runners_empty_repo(tmp_path):
    assert discovery.detect_test_runners(tmp_path) == []
