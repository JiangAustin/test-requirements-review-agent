from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ReviewException
from .rules import load_bundled_rule_pack

SERVER_NAME = "requirements-review"
MCP_SERVER = {
    "type": "stdio",
    "command": "uvx",
    "args": [
        "--from",
        "git+https://github.com/JiangAustin/test-requirements-review-agent.git",
        "requirements-review-mcp",
    ],
}
DEVELOPMENT_MCP_SERVER = {
    "type": "stdio",
    "command": "uv",
    "args": ["run", "requirements-review-mcp"],
}
IGNORE_ENTRIES = (".runs/", ".env", "inputs/")


def _resource_text(*parts: str) -> str:
    return files("requirements_review_agent").joinpath("resources", *parts).read_text(
        encoding="utf-8"
    )


def _read_mcp_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"servers": {}}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取 {path}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("servers"), dict):
        raise SystemExit(f"{path} 必须包含 JSON object 'servers'")
    return config


def _init(workspace: Path) -> int:
    workspace = workspace.resolve()
    agent_path = workspace / ".github" / "agents" / "requirements-review.agent.md"
    mcp_path = workspace / ".vscode" / "mcp.json"
    gitignore_path = workspace / ".gitignore"
    agent_text = _resource_text("requirements-review.agent.md")
    config = _read_mcp_config(mcp_path)

    if agent_path.exists() and agent_path.read_text(encoding="utf-8") != agent_text:
        raise SystemExit(f"{agent_path} 已存在不同内容；未覆盖用户文件")
    existing_server = config["servers"].get(SERVER_NAME)
    if existing_server is not None and existing_server != MCP_SERVER:
        raise SystemExit(f"{mcp_path} 已包含不同的 {SERVER_NAME} 配置；未覆盖用户配置")

    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(agent_text, encoding="utf-8", newline="\n")
    config["servers"][SERVER_NAME] = MCP_SERVER
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    existing_ignores = (
        gitignore_path.read_text(encoding="utf-8").splitlines() if gitignore_path.exists() else []
    )
    missing_ignores = [entry for entry in IGNORE_ENTRIES if entry not in existing_ignores]
    if missing_ignores:
        prefix = "\n" if existing_ignores and existing_ignores[-1] else ""
        with gitignore_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(prefix + "\n".join(missing_ignores) + "\n")

    print(f"Requirements Review 已初始化：{workspace}")
    print("请在 VS Code 中 Reload Window，然后选择 Requirements Review Agent。")
    return 0


def _doctor(workspace: Path) -> int:
    workspace = workspace.resolve()
    agent_path = workspace / ".github" / "agents" / "requirements-review.agent.md"
    mcp_path = workspace / ".vscode" / "mcp.json"
    checks: list[tuple[str, bool]] = []
    checks.append(("uvx", shutil.which("uvx") is not None))
    checks.append(
        (
            "Agent",
            agent_path.exists()
            and agent_path.read_text(encoding="utf-8")
            == _resource_text("requirements-review.agent.md"),
        )
    )
    try:
        config = _read_mcp_config(mcp_path)
        configured_server = config["servers"].get(SERVER_NAME)
        mcp_ok = configured_server in (MCP_SERVER, DEVELOPMENT_MCP_SERVER)
    except SystemExit:
        mcp_ok = False
    checks.append(("MCP", mcp_ok))
    try:
        load_bundled_rule_pack("home-iot-v1")
        rules_ok = True
    except ReviewException:
        rules_ok = False
    checks.append(("Built-in rule pack", rules_ok))

    for name, passed in checks:
        print(f"{name}: {'OK' if passed else 'FAIL'}")
    return 0 if all(passed for _, passed in checks) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rra", description="Requirements Review setup CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "doctor"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "init":
        return _init(arguments.workspace)
    return _doctor(arguments.workspace)


if __name__ == "__main__":
    sys.exit(main())