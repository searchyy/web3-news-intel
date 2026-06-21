from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_WORKFLOWS = {
    "ci.yml",
    "source-adapter-contracts.yml",
    "ai-integration-mock.yml",
    "frontend-performance.yml",
    "live-source-canary.yml",
    "deepseek-test.yml",
}
REQUIRED_LOW_CARDINALITY_METRICS = {
    "web3_news_source_fetch_total",
    "web3_news_source_parse_total",
    "web3_news_source_items_total",
    "web3_news_event_search_duration_seconds",
    "web3_news_event_search_total",
    "web3_news_ai_request_total",
    "web3_news_ai_request_duration_seconds",
    "web3_news_ai_tokens_total",
    "web3_news_ai_budget_rejected_total",
    "web3_news_ai_json_validation_failure_total",
    "web3_news_feishu_report_total",
    "web3_news_feishu_report_event_count",
    "web3_news_frontend_build_size_bytes",
}
FORBIDDEN_METRIC_LABELS = {
    "url",
    "full_url",
    "source_url",
    "title",
    "event_id",
    "chat_id",
    "api_key",
    "apikey",
    "secret",
    "token",
    "error",
    "error_message",
    "message",
    "symbol",
}
SECRET_PATTERNS = {
    "deepseek_like_api_key": re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    "feishu_webhook": re.compile(
        r"https://(?:open\.)?(?:feishu|larksuite)\.[^/\s]+"
        r"/open-apis/bot/v2/hook/[A-Za-z0-9-]+",
        re.I,
    ),
    "plaintext_secret_assignment": re.compile(
        r"(?im)^\s*(?:DEEPSEEK_API_KEY|OPENAI_API_KEY|FEISHU_APP_SECRET|"
        r"FIELD_ENCRYPTION_KEY|ADMIN_SESSION_SECRET)\s*[:=][\t ]*"
        r"(?!\$\{\{\s*secrets\.|<|change[-_ ]?me|your[-_ ]?|example|__)"
        r"[\"']?[A-Za-z0-9_\-/.+=]{12,}"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--strict-required-metrics", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    summary: dict[str, Any] = {"issues": [], "warnings": [], "metrics": {}, "workflows": {}}
    _audit_workflows(repo_root, summary)
    _audit_metrics(repo_root, summary, strict_required=args.strict_required_metrics)
    _audit_secret_literals(repo_root, summary)
    _audit_docs(repo_root, summary)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(summary)
    return 1 if summary["issues"] else 0


def _audit_workflows(repo_root: Path, summary: dict[str, Any]) -> None:
    workflow_dir = repo_root / ".github" / "workflows"
    present = {path.name for path in workflow_dir.glob("*.yml")}
    missing = REQUIRED_WORKFLOWS - present
    if missing:
        summary["issues"].append(f"缺少必要 workflow: {sorted(missing)}")
    for path in sorted(workflow_dir.glob("*.yml")):
        try:
            data = (
                yaml.load(
                    path.read_text(encoding="utf-8"),
                    Loader=_github_actions_loader(),
                )
                or {}
            )
        except Exception as exc:
            summary["issues"].append(f"{path}: YAML 解析失败: {type(exc).__name__}: {exc}")
            continue
        triggers = _trigger_names(data)
        jobs = sorted((data.get("jobs") or {}).keys())
        summary["workflows"][path.name] = {"triggers": sorted(triggers), "jobs": jobs}
        if path.name == "live-source-canary.yml" and "pull_request" in triggers:
            summary["issues"].append("live-source-canary.yml 禁止在 PR 强制访问外部源")
        if path.name == "deepseek-test.yml":
            if triggers != {"workflow_dispatch"}:
                summary["issues"].append("deepseek-test.yml 必须仅允许 workflow_dispatch")
            _require_deepseek_environment(path, data, summary)


def _audit_metrics(
    repo_root: Path,
    summary: dict[str, Any],
    *,
    strict_required: bool,
) -> None:
    path = repo_root / "app" / "observability" / "metrics.py"
    if not path.exists():
        summary["issues"].append("缺少 app/observability/metrics.py")
        return
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    metrics: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id not in {
            "Counter",
            "Gauge",
            "Histogram",
        }:
            continue
        name = _literal_string(node.args[0]) if node.args else None
        if not name:
            continue
        labels = _metric_labels(node)
        metrics[name] = labels
        forbidden = sorted(set(label.lower() for label in labels) & FORBIDDEN_METRIC_LABELS)
        if forbidden:
            summary["issues"].append(f"{path}:{name}: 禁止高基数或敏感 label {forbidden}")
    missing = sorted(REQUIRED_LOW_CARDINALITY_METRICS - set(metrics))
    summary["metrics"] = {"defined": sorted(metrics), "missing_required": missing}
    if missing and strict_required:
        summary["issues"].append(f"缺少本期要求的低基数指标: {missing}")
    elif missing:
        summary["warnings"].append(f"待业务实现接线的本期指标: {missing}")


def _audit_secret_literals(repo_root: Path, summary: dict[str, Any]) -> None:
    for path in _tracked_text_files(repo_root):
        if _is_skipped_path(path) or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(repo_root).as_posix()
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                summary["issues"].append(f"{rel}: 疑似提交明文秘密 ({name})")


def _audit_docs(repo_root: Path, summary: dict[str, Any]) -> None:
    required_docs = {
        "docs/MULTI_SOURCE_CATALOG.md",
        "docs/PHASE_MULTI_SOURCE_AI_ACCEPTANCE.md",
        "docs/SECURITY_MULTI_SOURCE_AI.md",
    }
    missing = sorted(path for path in required_docs if not (repo_root / path).exists())
    if missing:
        summary["warnings"].append(f"安全/验收文档尚未齐全: {missing}")


def _metric_labels(node: ast.Call) -> list[str]:
    if len(node.args) >= 3:
        labels = _literal_string_list(node.args[2])
        if labels is not None:
            return labels
    for keyword in node.keywords:
        if keyword.arg == "labelnames":
            labels = _literal_string_list(keyword.value)
            if labels is not None:
                return labels
    return []


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_string_list(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        output: list[str] = []
        for item in node.elts:
            value = _literal_string(item)
            if value is None:
                return None
            output.append(value)
        return output
    return None


def _tracked_text_files(repo_root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        return [path for path in repo_root.rglob("*") if path.is_file()]
    return [repo_root / line for line in completed.stdout.splitlines() if line.strip()]


def _is_skipped_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts & {".git", "node_modules", "dist", "reports", ".venv", "__pycache__"}:
        return True
    suffix = path.suffix.lower()
    return suffix in {".sqlite", ".db", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip"}


def _require_deepseek_environment(
    path: Path,
    data: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    jobs = data.get("jobs") or {}
    if not isinstance(jobs, dict) or not jobs:
        summary["issues"].append(f"{path}: deepseek workflow 缺少 jobs")
        return
    has_environment = any(isinstance(job, dict) and job.get("environment") for job in jobs.values())
    if not has_environment:
        summary["issues"].append("deepseek-test.yml 必须配置 GitHub Environment protection")
    text = path.read_text(encoding="utf-8")
    if "secrets.DEEPSEEK_API_KEY" not in text:
        summary["issues"].append("deepseek-test.yml 必须通过 GitHub secret 读取 DEEPSEEK_API_KEY")
    if "pull_request" in text:
        summary["issues"].append("deepseek-test.yml 禁止 pull_request 触发")


def _trigger_names(data: dict[str, Any]) -> set[str]:
    triggers = data.get("on")
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return {str(item) for item in triggers}
    if isinstance(triggers, dict):
        return {str(key) for key in triggers}
    return set()


def _github_actions_loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        first_char: list(resolvers)
        for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    for first_char, resolvers in list(Loader.yaml_implicit_resolvers.items()):
        Loader.yaml_implicit_resolvers[first_char] = [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
    return Loader


def _print_summary(summary: dict[str, Any]) -> None:
    for warning in summary["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    for issue in summary["issues"]:
        print(f"ERROR: {issue}", file=sys.stderr)
    status = "PASS" if not summary["issues"] else "FAIL"
    print(f"security-acceptance: {status}")


if __name__ == "__main__":
    raise SystemExit(main())
