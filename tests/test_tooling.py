"""Guard the documented development workflow (report issue m1)."""

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dev_extra_provides_coverage_plugin() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dev = project["optional-dependencies"]["dev"]

    assert any(
        requirement.split(">")[0].split("=")[0].strip() == "pytest-cov" for requirement in dev
    )


def test_ci_runs_suite_on_linux_and_windows_for_supported_pythons() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
    steps = " ".join(step.get("run", "") for step in workflow["jobs"]["test"]["steps"])

    assert {"ubuntu-latest", "windows-latest"} <= set(matrix["os"])
    assert {"3.11", "3.13"} <= set(matrix["python-version"])
    assert "pytest" in steps and "--cov" in steps
    assert "ruff check" in steps
