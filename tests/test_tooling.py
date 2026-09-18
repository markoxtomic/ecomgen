"""Guard the documented development workflow (report issue m1)."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _ci_workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _ci_commands(workflow: dict) -> str:
    return "\n".join(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", ())
        if "run" in step
    ).lower()


def test_dev_extra_provides_coverage_plugin() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dev = project["optional-dependencies"]["dev"]

    assert any(
        requirement.split(">")[0].split("=")[0].strip() == "pytest-cov" for requirement in dev
    )


def test_ci_runs_suite_on_linux_and_windows_for_supported_pythons() -> None:
    workflow = _ci_workflow()
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
    commands = _ci_commands(workflow)

    assert {"ubuntu-latest", "windows-latest"} <= set(matrix["os"])
    assert {"3.11", "3.14"} <= {str(version) for version in matrix["python-version"]}
    assert "pytest" in commands and "--cov" in commands


def test_ci_checks_ruff_lint_and_formatting() -> None:
    commands = _ci_commands(_ci_workflow())

    assert re.search(r"\bruff\s+check(?:\s|$)", commands)
    assert re.search(r"\bruff\s+format\s+--check(?:\s|$)", commands)


def test_ci_builds_the_package() -> None:
    commands = _ci_commands(_ci_workflow())

    assert re.search(r"\bpython\s+-m\s+build(?:\s|$)", commands)


def test_ci_smoke_tests_cli_generate_and_validate() -> None:
    commands = _ci_commands(_ci_workflow())
    cli = r"(?:ecomgen|python\s+-m\s+ecomgen(?:\.cli)?)"

    assert re.search(rf"\b{cli}\s+generate(?:\s|$)", commands)
    assert re.search(rf"\b{cli}\s+validate(?:\s|$)", commands)
