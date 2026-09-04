from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ReviewException
from .rules import load_bundled_rule_pack

SERVER_NAME = "requirements-review"
STATE_SCHEMA_VERSION = 1
LEGACY_AGENT_SHA256 = {
    "fdcd19fa755a1bed9abcbc3d8e76e6c2db6d960b9bf0f90ae62c4f8155266987",
}
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
IGNORE_ENTRIES = (
    ".runs/",
    ".env",
    "inputs/",
    "requirements/*",
    "!requirements/.gitkeep",
)


def _resource_text(*parts: str) -> str:
    return files("requirements_review_agent").joinpath("resources", *parts).read_text(
        encoding="utf-8"
    )


def _sha256_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_managed_agent_hashes(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取 {path}: {exc}") from exc
    agent_sha256 = state.get("agent_sha256") if isinstance(state, dict) else None
    pending_sha256 = state.get("pending_agent_sha256") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or type(state.get("schema_version")) is not int
        or state.get("schema_version") != STATE_SCHEMA_VERSION
        or not isinstance(agent_sha256, str)
        or not _is_sha256(agent_sha256)
        or (pending_sha256 is not None and not _is_sha256(pending_sha256))
        or not set(state) <= {"schema_version", "agent_sha256", "pending_agent_sha256"}
    ):
        raise SystemExit(f"{path} 不是有效的 Requirements Review 安装状态")
    hashes = {agent_sha256}
    if isinstance(pending_sha256, str):
        hashes.add(pending_sha256)
    return hashes


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(3):
            try:
                os.replace(temporary_path, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _state_text(agent_sha256: str, pending_sha256: str | None = None) -> str:
    state: dict[str, object] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "agent_sha256": agent_sha256,
    }
    if pending_sha256 is not None:
        state["pending_agent_sha256"] = pending_sha256
    return json.dumps(state, ensure_ascii=False, indent=2) + "\n"


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


def _is_development_workspace(workspace: Path) -> bool:
    try:
        project_file = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    project = project_file.get("project")
    if not isinstance(project, dict) or project.get("name") != "requirements-review-agent":
        return False
    scripts = project.get("scripts")
    return isinstance(scripts, dict) and scripts.get(
        "requirements-review-mcp"
    ) == "requirements_review_agent.server:main"


def _is_inbox_protected(workspace: Path) -> bool:
    requirements_path = workspace / "requirements"
    try:
        ignore_entries = set((workspace / ".gitignore").read_text(encoding="utf-8").splitlines())
    except OSError:
        return False
    return (
        requirements_path.is_dir()
        and (requirements_path / ".gitkeep").is_file()
        and {"requirements/*", "!requirements/.gitkeep"} <= ignore_entries
    )


def _init(workspace: Path) -> int:
    workspace = workspace.resolve()
    agent_path = workspace / ".github" / "agents" / "requirements-review.agent.md"
    mcp_path = workspace / ".vscode" / "mcp.json"
    state_path = workspace / ".vscode" / "requirements-review-state.json"
    gitignore_path = workspace / ".gitignore"
    requirements_keep = workspace / "requirements" / ".gitkeep"
    agent_text = _resource_text("requirements-review.agent.md")
    agent_sha256 = _sha256_text(agent_text)
    config = _read_mcp_config(mcp_path)
    installed_agent_sha256: str | None = None
    managed_agent_hashes = _read_managed_agent_hashes(state_path)

    if agent_path.exists():
        installed_agent_sha256 = _sha256_text(agent_path.read_text(encoding="utf-8"))
        if installed_agent_sha256 != agent_sha256:
            if managed_agent_hashes is not None:
                if installed_agent_sha256 not in managed_agent_hashes:
                    raise SystemExit(f"{agent_path} 包含用户修改；未自动覆盖")
            elif installed_agent_sha256 not in LEGACY_AGENT_SHA256:
                raise SystemExit(f"{agent_path} 已存在不同内容；未覆盖用户文件")
    existing_server = config["servers"].get(SERVER_NAME)
    if existing_server is not None and existing_server != MCP_SERVER:
        raise SystemExit(f"{mcp_path} 已包含不同的 {SERVER_NAME} 配置；未覆盖用户配置")

    transition_sha256 = installed_agent_sha256 or agent_sha256
    _write_text_atomic(state_path, _state_text(transition_sha256, agent_sha256))
    _write_text_atomic(agent_path, agent_text)
    requirements_keep.parent.mkdir(parents=True, exist_ok=True)
    requirements_keep.touch(exist_ok=True)
    config["servers"][SERVER_NAME] = MCP_SERVER
    _write_text_atomic(mcp_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    existing_ignores = (
        gitignore_path.read_text(encoding="utf-8").splitlines() if gitignore_path.exists() else []
    )
    missing_ignores = [entry for entry in IGNORE_ENTRIES if entry not in existing_ignores]
    if missing_ignores:
        prefix = "\n" if existing_ignores and existing_ignores[-1] else ""
        existing_text = (
            gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
        )
        _write_text_atomic(
            gitignore_path,
            existing_text + prefix + "\n".join(missing_ignores) + "\n",
        )

    _write_text_atomic(state_path, _state_text(agent_sha256))

    print(f"Requirements Review 已初始化：{workspace}")
    print(f"请将需求 PDF 上传到：{requirements_keep.parent}")
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
        mcp_ok = configured_server == MCP_SERVER or (
            configured_server == DEVELOPMENT_MCP_SERVER
            and _is_development_workspace(workspace)
        )
    except SystemExit:
        mcp_ok = False
    checks.append(("MCP", mcp_ok))
    checks.append(("Requirements inbox", _is_inbox_protected(workspace)))
    try:
        for rule_pack in ("home-iot-v1", "automotive-ecu-v1"):
            load_bundled_rule_pack(rule_pack)
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