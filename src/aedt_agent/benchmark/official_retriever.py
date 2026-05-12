from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib import request

from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.knowledge.sqlite_provider import SQLiteKnowledgeProvider


@dataclass(frozen=True)
class EvidenceItem:
    source_type: str
    path_or_url: str
    title: str
    snippet: str
    query: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalBundle:
    queries: list[str]
    evidence: list[EvidenceItem]

    def to_dict(self) -> dict:
        return {
            "queries": list(self.queries),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    def to_prompt_context(self) -> str:
        if not self.evidence:
            return "Official retrieval evidence: no evidence found."
        parts = ["Official retrieval evidence:"]
        for index, item in enumerate(self.evidence, start=1):
            parts.append(
                f"[{index}] {item.source_type}: {item.title}\n"
                f"Source: {item.path_or_url}\n"
                f"Query: {item.query}\n"
                f"Snippet:\n{item.snippet}"
            )
        return "\n\n".join(parts)


class OfficialKnowledgeRetriever:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    def retrieve(self, task: BenchmarkTask) -> str:
        parts = [
            "Official sources to use:",
            "- PyAEDT GitHub: https://github.com/ansys/pyaedt",
            "- PyAEDT docs: https://aedt.docs.pyansys.com/",
            "- PyAEDT examples: https://github.com/ansys/pyaedt-examples",
        ]
        if self.db_path and self.db_path.exists():
            provider = SQLiteKnowledgeProvider(self.db_path)
            seen: set[str] = set()
            for category in task.required_api_categories + task.expected_workflow:
                for item in provider.search_api(category, limit=3):
                    if item.fqname in seen:
                        continue
                    seen.add(item.fqname)
                    parts.append(f"- {item.fqname}: {item.signature}")
        if task.reference_script and Path(task.reference_script).exists():
            snippet = Path(task.reference_script).read_text(encoding="utf-8")[:2000]
            parts.append("Official/reference example snippet:")
            parts.append(snippet)
        return "\n".join(parts)

    def retrieve_bundle(self, task: BenchmarkTask, previous_log: str = "") -> RetrievalBundle:
        return RetrievalBundle(
            queries=[task.requirement],
            evidence=[
                EvidenceItem(
                    source_type="legacy",
                    path_or_url="api_semantics/reference_script",
                    title="Legacy official context",
                    snippet=self.retrieve(task),
                    query=task.requirement,
                )
            ],
        )


class GitNexusOfficialRetriever:
    def __init__(
        self,
        pyaedt_repo: Path,
        examples_repo: Path | None = None,
        backend: str = "gitnexus_cli",
        gitnexus_url: str = "http://127.0.0.1:4848",
        top_k: int = 8,
        timeout: int = 60,
        subprocess_runner: Callable = subprocess.run,
        http_post: Callable[[str, dict, int], str] | None = None,
    ) -> None:
        self.pyaedt_repo = Path(pyaedt_repo)
        self.examples_repo = Path(examples_repo) if examples_repo else None
        self.backend = backend
        self.gitnexus_url = gitnexus_url.rstrip("/")
        self.top_k = top_k
        self.timeout = timeout
        self.subprocess_runner = subprocess_runner
        self.http_post = http_post or _http_post_text

    def retrieve(self, task: BenchmarkTask) -> str:
        return self.retrieve_bundle(task).to_prompt_context()

    def retrieve_bundle(self, task: BenchmarkTask, previous_log: str = "") -> RetrievalBundle:
        queries = self._build_queries(task, previous_log)
        evidence: list[EvidenceItem] = []
        for query in queries:
            evidence.extend(self._query_gitnexus_http(query) if self.backend == "gitnexus_http" else self._query_gitnexus_cli(query))
            evidence.extend(self._search_examples(query))
            if len(evidence) >= self.top_k:
                break
        return RetrievalBundle(queries=queries, evidence=evidence[: self.top_k])

    def _build_queries(self, task: BenchmarkTask, previous_log: str) -> list[str]:
        combined = " ".join(
            part
            for part in [
                task.task_id.replace("_", " "),
                task.task_id,
                task.requirement,
                " ".join(task.expected_workflow),
                " ".join(task.required_api_categories),
                _compact(previous_log, 220) if previous_log else "",
            ]
            if part.strip()
        )
        seeds = [
            combined,
            task.requirement,
            " ".join(task.expected_workflow),
            " ".join(task.required_api_categories),
        ]
        if previous_log:
            seeds.append(_compact(previous_log, 220))
        queries: list[str] = []
        for seed in seeds:
            if not seed.strip():
                continue
            query = f"PyAEDT HFSS {seed}".strip()
            if query not in queries:
                queries.append(query)
        return queries[:4]

    def _query_gitnexus_http(self, query: str) -> list[EvidenceItem]:
        try:
            query_text = self.http_post(
                f"{self.gitnexus_url}/tool/query",
                {"query": query, "repo": "pyaedt"},
                self.timeout,
            )
        except Exception:
            return self._query_gitnexus_cli(query)

        items = [
            EvidenceItem(
                source_type="gitnexus_http",
                path_or_url=self.gitnexus_url,
                title="GitNexus query",
                snippet=_compact(query_text, 2500),
                query=query,
            )
        ]
        symbol = _first_symbol_name(query_text)
        if symbol:
            try:
                context_text = self.http_post(
                    f"{self.gitnexus_url}/tool/context",
                    {"name": symbol, "repo": "pyaedt"},
                    self.timeout,
                )
                items.append(
                    EvidenceItem(
                        source_type="gitnexus_http_context",
                        path_or_url=self.gitnexus_url,
                        title=f"GitNexus context: {symbol}",
                        snippet=_compact(context_text, 3000),
                        query=query,
                    )
                )
            except Exception:
                pass
        return items

    def _query_gitnexus_cli(self, query: str) -> list[EvidenceItem]:
        result = self.subprocess_runner(
            ["gitnexus", "query", query],
            timeout=self.timeout,
            capture_output=True,
            text=True,
            cwd=self.pyaedt_repo if self.pyaedt_repo.exists() else None,
        )
        if getattr(result, "returncode", 1) != 0:
            return [
                EvidenceItem(
                    source_type="gitnexus_error",
                    path_or_url=str(self.pyaedt_repo),
                    title="GitNexus query failed",
                    snippet=_compact(getattr(result, "stderr", ""), 1000),
                    query=query,
                )
            ]
        payload = _parse_json(getattr(result, "stdout", ""))
        items: list[EvidenceItem] = []
        for entry in payload.get("definitions", [])[: self.top_k]:
            title = str(entry.get("name") or entry.get("id") or "definition")
            path = str(entry.get("filePath") or self.pyaedt_repo)
            line_info = ""
            if entry.get("startLine"):
                line_info = f":{entry.get('startLine')}-{entry.get('endLine', entry.get('startLine'))}"
            items.append(
                EvidenceItem(
                    source_type="gitnexus",
                    path_or_url=f"{path}{line_info}",
                    title=title,
                    snippet=json.dumps(entry, ensure_ascii=False),
                    query=query,
                )
            )
        for entry in payload.get("processes", [])[: max(self.top_k - len(items), 0)]:
            items.append(
                EvidenceItem(
                    source_type="gitnexus",
                    path_or_url=str(entry.get("filePath") or self.pyaedt_repo),
                    title=str(entry.get("name") or entry.get("id") or "process"),
                    snippet=json.dumps(entry, ensure_ascii=False),
                    query=query,
                )
            )
        return items

    def _search_examples(self, query: str) -> list[EvidenceItem]:
        if not self.examples_repo or not self.examples_repo.exists():
            return []
        stop_terms = {"pyaedt", "hfss", "create", "using", "with", "task", "line"}
        terms = [
            term.lower()
            for term in query.replace("_", " ").split()
            if len(term) > 3 and term.lower() not in stop_terms
        ]
        scored: list[tuple[int, Path, str]] = []
        search_root = self.examples_repo / "examples"
        if not search_root.exists():
            search_root = self.examples_repo
        for path in sorted(search_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            lowered = text.lower()
            score = sum(1 for term in terms if term in lowered or term in path.name.lower())
            if score == 0:
                continue
            scored.append((score, path, text))
        items: list[EvidenceItem] = []
        for _score, path, text in sorted(scored, key=lambda item: (-item[0], str(item[1])))[: self.top_k]:
            items.append(
                EvidenceItem(
                    source_type="example",
                    path_or_url=str(path),
                    title=path.name,
                    snippet=_best_snippet(text, terms),
                    query=query,
                )
            )
        return items


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _best_snippet(text: str, terms: list[str], limit: int = 1200) -> str:
    lowered = text.lower()
    starts = [lowered.find(term) for term in terms if term in lowered]
    first_match = min(starts) if starts else 0
    start = max(first_match - 300, 0)
    return text[start : start + limit]


def _compact(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def _http_post_text(url: str, payload: dict, timeout: int) -> str:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _first_symbol_name(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        for marker in ("Symbol ", "Method ", "Function ", "Class "):
            if stripped.startswith(marker):
                rest = stripped[len(marker) :]
                return rest.split(" ", 1)[0]
        if " Hfss." in stripped:
            return stripped.split(" Hfss.", 1)[1].split(" ", 1)[0]
    return ""
