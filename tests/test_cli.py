from __future__ import annotations

import json
from pathlib import Path

import pytest

from requirements_review_agent.cli import main


def test_init_creates_portable_workspace_configuration(tmp_path: Path) -> None:
    exit_code = main(["init", "--workspace", str(tmp_path)])

    assert exit_code == 0
    agent = tmp_path / ".github" / "agents" / "requirements-review.agent.md"
    config = json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert agent.read_text(encoding="utf-8").startswith("---\nname: Requirements Review")
    assert config["servers"]["requirements-review"] == {
        "type": "stdio",
        "command": "uvx",
        "args": [
            "--from",
            "git+https://github.com/JiangAustin/test-requirements-review-agent.git",
            "requirements-review-mcp",
        ],
    }
    assert {".runs/", ".env", "inputs/", "requirements/*", "!requirements/.gitkeep"} <= set(
        (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    )
    assert (tmp_path / "requirements" / ".gitkeep").is_file()


def test_init_is_idempotent_and_preserves_other_mcp_servers(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"servers": {"existing": {"type": "stdio", "command": "other"}}}),
        encoding="utf-8",
    )

    assert main(["init", "--workspace", str(tmp_path)]) == 0
    assert main(["init", "--workspace", str(tmp_path)]) == 0

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["servers"]["existing"]["command"] == "other"


def test_init_refuses_to_replace_conflicting_agent(tmp_path: Path) -> None:
    agent = tmp_path / ".github" / "agents" / "requirements-review.agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("user content\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="已存在不同内容"):
        main(["init", "--workspace", str(tmp_path)])

    assert agent.read_text(encoding="utf-8") == "user content\n"


def test_init_refuses_to_replace_conflicting_mcp_server(tmp_path: Path) -> None:
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "requirements-review": {"type": "stdio", "command": "custom-command"}
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="未覆盖用户配置"):
        main(["init", "--workspace", str(tmp_path)])

    assert json.loads(config_path.read_text(encoding="utf-8"))["servers"][
        "requirements-review"
    ]["command"] == "custom-command"


def test_packaged_agent_matches_repository_definition() -> None:
    repository_agent = Path(".github/agents/requirements-review.agent.md").read_text(
        encoding="utf-8"
    )
    generated_workspace = Path("src/requirements_review_agent/resources")
    packaged_agent = (generated_workspace / "requirements-review.agent.md").read_text(
        encoding="utf-8"
    )

    assert packaged_agent == repository_agent


def test_doctor_passes_after_init(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0

    assert main(["doctor", "--workspace", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Agent: OK" in output
    assert "MCP: OK" in output
    assert "Requirements inbox: OK" in output
    assert "Built-in rule pack: OK" in output


def test_init_treats_crlf_agent_as_identical(tmp_path: Path) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    agent = tmp_path / ".github" / "agents" / "requirements-review.agent.md"
    agent.write_bytes(agent.read_bytes().replace(b"\n", b"\r\n"))

    assert main(["init", "--workspace", str(tmp_path)]) == 0
    assert main(["doctor", "--workspace", str(tmp_path)]) == 0


def test_doctor_accepts_repository_development_mcp_config(tmp_path: Path) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "requirements-review-agent"

[project.scripts]
requirements-review-mcp = "requirements_review_agent.server:main"
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "requirements-review": {
                        "type": "stdio",
                        "command": "uv",
                        "args": ["run", "requirements-review-mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["doctor", "--workspace", str(tmp_path)]) == 0


def test_doctor_rejects_development_mcp_config_outside_repository(tmp_path: Path) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    config_path = tmp_path / ".vscode" / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": {
                    "requirements-review": {
                        "type": "stdio",
                        "command": "uv",
                        "args": ["run", "requirements-review-mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["doctor", "--workspace", str(tmp_path)]) == 1


def test_doctor_returns_failure_for_uninitialized_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor", "--workspace", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "Agent: FAIL" in output
    assert "MCP: FAIL" in output


def test_doctor_returns_failure_when_requirements_inbox_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    (tmp_path / "requirements" / ".gitkeep").unlink()
    (tmp_path / "requirements").rmdir()

    assert main(["doctor", "--workspace", str(tmp_path)]) == 1
    assert "Requirements inbox: FAIL" in capsys.readouterr().out


def test_doctor_returns_failure_when_inbox_protection_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".runs/\n.env\n", encoding="utf-8")

    assert main(["doctor", "--workspace", str(tmp_path)]) == 1
    assert "Requirements inbox: FAIL" in capsys.readouterr().out