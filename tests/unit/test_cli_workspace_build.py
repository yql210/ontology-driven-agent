from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspacePublishStatus


@dataclass(frozen=True)
class _Outcome:
    status: WorkspacePublishStatus


@dataclass(frozen=True)
class _Result:
    outcome: _Outcome

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": "task-1",
            "generation_id": "generation-1",
            "outcome": {"status": self.outcome.status.value},
        }


class _Service:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.manifests: list[Path] = []
        self.closed = False

    def build(self, manifest: Path) -> _Result:
        self.manifests.append(manifest)
        return self.result

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_workspace_build_help_is_available(runner: CliRunner) -> None:
    result = runner.invoke(main, ["workspace-build", "--help"])

    assert result.exit_code == 0
    assert "workspace-build" in result.output
    assert "manifest" in result.output.lower()


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [(WorkspacePublishStatus.ACTIVE, 0), (WorkspacePublishStatus.BLOCKED, 2), (WorkspacePublishStatus.FAILED, 2)],
)
def test_workspace_build_routes_to_service_and_maps_publication_status(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: WorkspacePublishStatus, exit_code: int
) -> None:
    # Arrange
    manifest = tmp_path / "workspace.json"
    manifest.write_text("{}", encoding="utf-8")
    service = _Service(_Result(_Outcome(status)))
    monkeypatch.setattr("ontoagent.api.cli.create_workspace_build_service", lambda config: service)

    # Act
    result = runner.invoke(main, ["workspace-build", str(manifest)])

    # Assert
    assert result.exit_code == exit_code
    assert json.loads(result.output)["outcome"]["status"] == status.value
    assert service.manifests == [manifest]
    assert service.closed
