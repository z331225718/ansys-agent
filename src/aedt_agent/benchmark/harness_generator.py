from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

from aedt_agent.benchmark.tool_usage import analyze_tool_usage


@dataclass(frozen=True)
class HarnessGroupConfig:
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    add_dirs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessGeneration:
    code: str
    stdout_path: str
    stderr_path: str
    transcript_path: str
    tool_usage_path: str
    tool_usage: dict[str, Any]


class HarnessGenerationError(RuntimeError):
    def __init__(self, message: str, generation: HarnessGeneration | None = None) -> None:
        super().__init__(message)
        self.generation = generation


class HarnessGenerator:
    def __init__(
        self,
        command: str,
        timeout: int,
        work_dir: Path,
        group_configs: dict[str, HarnessGroupConfig],
        subprocess_runner: Callable = subprocess.run,
        repo_root: Path | None = None,
        variables: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.timeout = timeout
        self.work_dir = Path(work_dir)
        self.group_configs = group_configs
        self.subprocess_runner = subprocess_runner
        self.repo_root = Path(repo_root or Path.cwd())
        self.variables = variables or {}

    def generate(self, context: str, filename: str | None = None) -> str:
        artifact_dir = self.work_dir / "adhoc"
        result = self.generate_attempt(
            context,
            task_id=Path(filename or "adhoc.py").stem,
            group="A",
            attempt=1,
            artifact_dir=artifact_dir,
            filename=filename,
        )
        return result.code

    def generate_attempt(
        self,
        context: str,
        task_id: str,
        group: str,
        attempt: int,
        artifact_dir: Path,
        filename: str | None = None,
        previous_code: str = "",
        previous_log: str = "",
    ) -> HarnessGeneration:
        return self._generate_attempt(
            context=context,
            task_id=task_id,
            group=group,
            attempt=attempt,
            artifact_dir=artifact_dir,
            filename=filename,
            previous_code=previous_code,
            previous_log=previous_log,
            extract_python=True,
        )

    def generate_text_attempt(
        self,
        context: str,
        task_id: str,
        group: str,
        attempt: int,
        artifact_dir: Path,
        filename: str | None = None,
        previous_code: str = "",
        previous_log: str = "",
    ) -> HarnessGeneration:
        return self._generate_attempt(
            context=context,
            task_id=task_id,
            group=group,
            attempt=attempt,
            artifact_dir=artifact_dir,
            filename=filename,
            previous_code=previous_code,
            previous_log=previous_log,
            extract_python=False,
        )

    def _generate_attempt(
        self,
        context: str,
        task_id: str,
        group: str,
        attempt: int,
        artifact_dir: Path,
        filename: str | None = None,
        previous_code: str = "",
        previous_log: str = "",
        extract_python: bool = True,
    ) -> HarnessGeneration:
        del filename, previous_code, previous_log
        cfg = self.group_configs.get(group, HarnessGroupConfig())
        artifact_dir.mkdir(parents=True, exist_ok=True)
        cwd = self._resolve_path(self._expand_value(cfg.cwd, context)) if cfg.cwd else self.work_dir / task_id / group / f"attempt_{attempt}"
        cwd.mkdir(parents=True, exist_ok=True)

        stdout_path = artifact_dir / f"attempt_{attempt}_harness_stdout.txt"
        stderr_path = artifact_dir / f"attempt_{attempt}_harness_stderr.txt"
        transcript_path = artifact_dir / f"attempt_{attempt}_transcript.txt"
        tool_usage_path = artifact_dir / f"attempt_{attempt}_tool_usage.json"

        command = [cfg.command or self.command, *self._expand_args(cfg.args, context)]
        input_text = None if _args_include_prompt(cfg.args) else context
        env = dict(os.environ)
        env.update({key: self._expand_value(str(value), context) for key, value in cfg.env.items()})

        try:
            completed = self.subprocess_runner(
                command,
                input=input_text,
                cwd=cwd,
                env=env,
                timeout=self.timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _to_text(exc.stdout)
            stderr = _to_text(exc.stderr)
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr + f"\nHarness timed out after {self.timeout}s\n", encoding="utf-8")
            transcript_path.write_text(_join_transcript(stdout, stderr), encoding="utf-8")
            tool_usage = analyze_tool_usage(_join_transcript(stdout, stderr))
            tool_usage_path.write_text(json.dumps(tool_usage, indent=2), encoding="utf-8")
            generation = HarnessGeneration(
                code="",
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                transcript_path=str(transcript_path),
                tool_usage_path=str(tool_usage_path),
                tool_usage=tool_usage,
            )
            raise HarnessGenerationError(f"Harness CLI timed out after {self.timeout}s", generation) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        transcript = _join_transcript(stdout, stderr)
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        transcript_path.write_text(transcript, encoding="utf-8")
        if completed.returncode != 0:
            tool_usage = analyze_tool_usage(transcript)
            tool_usage_path.write_text(json.dumps(tool_usage, indent=2), encoding="utf-8")
            generation = HarnessGeneration(
                code="",
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                transcript_path=str(transcript_path),
                tool_usage_path=str(tool_usage_path),
                tool_usage=tool_usage,
            )
            raise HarnessGenerationError(
                f"Harness CLI failed with exit code {completed.returncode}: {stderr.strip() or stdout.strip()}",
                generation,
            )

        code = extract_code(transcript) if extract_python else extract_text(transcript)
        tool_usage = analyze_tool_usage(transcript, code)
        tool_usage_path.write_text(json.dumps(tool_usage, indent=2), encoding="utf-8")
        return HarnessGeneration(
            code=code,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            transcript_path=str(transcript_path),
            tool_usage_path=str(tool_usage_path),
            tool_usage=tool_usage,
        )

    def _resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.repo_root / path

    def _expand_args(self, args: list[str], prompt: str) -> list[str]:
        return [self._expand_value(arg, prompt) for arg in args]

    def _expand_value(self, value: str, prompt: str) -> str:
        expanded = value.replace("{prompt}", prompt)
        for key, replacement in self.variables.items():
            expanded = expanded.replace("{" + key + "}", replacement)
        return expanded


def extract_code(output: str) -> str:
    text = _text_from_json_stream(output) or output
    fenced = re.findall(r"```(?:python|py)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    candidate = fenced[-1] if fenced else text
    candidate = _trim_metadata(candidate).strip()
    if not _is_parseable_python(candidate):
        candidate = _extract_parseable_python_region(candidate)
    if not _looks_like_python(candidate):
        raise ValueError("Harness output did not contain plausible Python code")
    return candidate


def extract_text(output: str) -> str:
    return (_text_from_json_stream(output) or output).strip()


def load_harness_group_config(path: Path) -> HarnessGroupConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return HarnessGroupConfig(
        command=str(data.get("command", "")),
        args=[str(item) for item in data.get("args", [])],
        cwd=str(data.get("cwd", "")),
        env={str(key): str(value) for key, value in data.get("env", {}).items()},
        add_dirs=[str(item) for item in data.get("add_dirs", [])],
    )


def _text_from_json_stream(output: str) -> str:
    final_result = ""
    parts: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "result" and isinstance(payload.get("result"), str):
            final_result = payload["result"]
            continue
        message = payload.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            _collect_assistant_text(message.get("content"), parts)
    if final_result.strip():
        return final_result.strip()
    return "\n".join(part for part in parts if part).strip()


def _collect_assistant_text(value: Any, parts: list[str]) -> None:
    if isinstance(value, dict):
        if value.get("type") in {"text", "output_text"} and isinstance(value.get("text"), str):
            parts.append(value["text"])
        for child in value.values():
            _collect_assistant_text(child, parts)
    elif isinstance(value, list):
        for child in value:
            _collect_assistant_text(child, parts)


def _trim_metadata(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("`") and stripped.endswith("`") and not stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
    lines = stripped.splitlines()
    while lines and lines[0].strip().lower() in {"python", "py"}:
        lines.pop(0)
    while lines and lines[0].strip().startswith(("Here is", "Sure,", "I'll ")):
        lines.pop(0)
    return "\n".join(lines)


def _extract_parseable_python_region(text: str) -> str:
    lines = text.strip().splitlines()
    for start in range(len(lines)):
        if not _line_can_start_python(lines[start]):
            continue
        for end in range(len(lines), start, -1):
            candidate = "\n".join(lines[start:end]).strip()
            if _looks_like_python(candidate) and _is_parseable_python(candidate):
                return candidate
    return text.strip()


def _line_can_start_python(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    starters = (
        "#",
        "app.",
        "setup",
        "port",
        "wg",
        "body",
        "face",
        "for ",
        "if ",
        "def ",
        "import ",
        "from ",
    )
    return stripped[0].isalpha() or stripped.startswith(starters)


def _is_parseable_python(text: str) -> bool:
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def _looks_like_python(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    markers = ("app.", "=", "def ", "for ", "if ", "assert ", "import ", "from ")
    return any(marker in stripped for marker in markers)


def _args_include_prompt(args: list[str]) -> bool:
    return any("{prompt}" in arg for arg in args)


def _join_transcript(stdout: str, stderr: str) -> str:
    return "\n".join(part for part in (stdout, stderr) if part)


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
