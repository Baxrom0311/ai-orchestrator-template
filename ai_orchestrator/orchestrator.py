#!/usr/bin/env python3
"""AI Agent Orchestrator — Kiro Planner + Builder + Reviewer

Three-level nested loop:
  Outer:  Kiro plan cycles            (plan_cycles, default 3)
  Middle: Build-review cycles         (review_cycles, default 5)
  Inner:  Build iterations            (build_iterations, default 10)

Features:
  - Checkpoint/Resume: crash dan keyin davom ettirish (--resume)
  - Error retry with exponential backoff
  - Cost/Budget tracking with warnings
  - Multi-model support (planner/builder/reviewer alohida model)
  - Separate reviewer agent
  - Metrics & observability (duration, tokens, pass rate)

Roles:
  - Kiro (ai-planner): Planner (Opus)
  - Kiro (ai-builder): Builder (Opus/Sonnet)
  - Kiro (ai-reviewer): Reviewer (Opus/Haiku)

Telegram bot notifications keep you informed without watching the terminal.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

# Handle import regardless of how the script is invoked.
_parent = str(Path(__file__).resolve().parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)
from telegram_notifier import TelegramNotifier
from context_store import ContextStore


# ── Data classes ───────────────────────────────────────────────────

@dataclasses.dataclass
class CommandResult:
    name: str
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def combined(self, limit: int = 24000) -> str:
        text = ""
        if self.stdout.strip():
            text += f"STDOUT:\n{self.stdout.strip()}\n"
        if self.stderr.strip():
            text += f"STDERR:\n{self.stderr.strip()}\n"
        if len(text) > limit:
            return text[:limit] + "\n...[truncated by orchestrator]"
        return text


class OrchestratorError(RuntimeError):
    pass


# ── Default configuration ─────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {
        "path": ".",
        "brief_file": "PROJECT_BRIEF.md",
        "test_command": "",
        "logs_dir": ".agentloop/runs",
        "exclude_dirs": [],
        "snapshot_exclude": ["\\.git/", "\\.agentloop/", "node_modules/", "\\.venv/", "__pycache__/"],
        "tests": [],  # [[project.tests]] array for parallel test execution
    },
    "loop": {
        "plan_cycles": 3,
        "review_cycles": 5,
        "build_iterations": 10,
        "no_change_limit": 2,
        "sleep_between_rounds_sec": 1,
        "max_total_builds": 50,
        "max_discovery_rounds": 2,
        "parallel_review": False,  # Run review + test in parallel (safe, read-only)
        "max_parallel_builders": 3,
    },
    "retry": {
        "max_attempts": 3,
        "backoff_base": 2,
        "backoff_max": 60,
        "jitter": True,
    },
    "budget": {
        "max_cost_usd": 0,  # 0 = unlimited
        "warn_at_pct": 80,
        "cost_per_build_usd": 0.15,  # taxminiy
        "cost_per_review_usd": 0.05,
        "cost_per_plan_usd": 0.10,
    },
    "agents": {
        "planner": {
            "command": "kiro-cli",
            "agent": "ai-planner",
            "timeout_sec": 3600,
            "trust_all_tools": True,
            "trust_tools": "",
            "require_mcp_startup": False,
        },
        "builder": {
            "command": "kiro-cli",
            "agent": "ai-builder",
            "timeout_sec": 7200,
            "trust_all_tools": True,
            "trust_tools": "read,write,grep,shell",
        },
        "reviewer": {
            "command": "kiro-cli",
            "agent": "ai-reviewer",
            "timeout_sec": 3600,
            "trust_all_tools": True,
            "trust_tools": "",
        },
    },
    "kiro": {
        "enabled": True,
        "command": "kiro-cli",
        "agent": "ai-planner",
        "timeout_sec": 3600,
        "resume": False,
        "trust_tools": "",
        "trust_all_tools": True,
        "require_mcp_startup": False,
        "instruction_arg": "Follow the orchestration instructions from STDIN exactly.",
    },
    "kiro_builder": {
        "enabled": True,
        "command": "kiro-cli",
        "agent": "ai-builder",
        "timeout_sec": 7200,
        "trust_tools": "read,write,grep,shell",
        "trust_all_tools": True,
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
    },
    "git": {
        "auto_push": False,
        "branch": "main",
        "commit_message": "",
    },
}


# ── Utility functions ──────────────────────────────────────────────

def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    if tomllib is None:
        raise OrchestratorError(
            "Python 3.11+ is required for TOML config, or install tomli."
        )
    with path.open("rb") as f:
        user_cfg = tomllib.load(f)
    # Validate: warn about unknown top-level keys
    known_keys = set(DEFAULT_CONFIG.keys())
    unknown = set(user_cfg.keys()) - known_keys
    if unknown:
        print(f"[agentloop:warn] Unknown config keys: {', '.join(sorted(unknown))}")
    # Validate required fields
    project = user_cfg.get("project", {})
    if project.get("path") and not Path(project["path"]).expanduser().exists():
        print(f"[agentloop:warn] project.path does not exist: {project['path']}")
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def ensure_project_root(project_path: Path) -> Path:
    root = project_path.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise OrchestratorError(f"Project path does not exist: {root}")
    return root


def now_slug() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def render_template(template_path: Path, **kwargs: str) -> str:
    text = read_text(template_path)
    if not text:
        raise OrchestratorError(f"Missing prompt template: {template_path}")
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def trim(text: str, limit: int = 20000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[middle truncated by orchestrator]...\n" + text[-half:]


# ── Retry with exponential backoff ─────────────────────────────────

def retry_call(func, config: Dict[str, Any], name: str = "agent") -> CommandResult:
    """Retry a function that returns CommandResult with exponential backoff."""
    retry_cfg = config.get("retry", {})
    max_attempts = int(retry_cfg.get("max_attempts", 3))
    backoff_base = float(retry_cfg.get("backoff_base", 2))
    backoff_max = float(retry_cfg.get("backoff_max", 60))
    jitter = retry_cfg.get("jitter", True)

    for attempt in range(1, max_attempts + 1):
        result = func()
        # Success or non-retryable (non-timeout, non-crash)
        if result.ok:
            return result
        if not result.timed_out and result.returncode not in (124, 137, -9, -15):
            return result  # logic error, don't retry
        if attempt == max_attempts:
            print(f"    [retry] {name} failed after {max_attempts} attempts")
            return result
        delay = min(backoff_base ** attempt, backoff_max)
        if jitter:
            delay += random.uniform(0, delay * 0.3)
        print(f"    [retry] {name} attempt {attempt} failed (rc={result.returncode}), retrying in {delay:.1f}s...")
        time.sleep(delay)
    return result  # type: ignore[possibly-undefined]


# ── Cost / Budget tracker ──────────────────────────────────────────

class CostTracker:
    """Track estimated costs and enforce budget limits."""

    def __init__(self, config: Dict[str, Any]):
        budget = config.get("budget", {})
        self.max_cost = float(budget.get("max_cost_usd", 0))
        self.warn_pct = float(budget.get("warn_at_pct", 80))
        self.cost_build = float(budget.get("cost_per_build_usd", 0.15))
        self.cost_review = float(budget.get("cost_per_review_usd", 0.05))
        self.cost_plan = float(budget.get("cost_per_plan_usd", 0.10))
        self.total_cost = 0.0
        self.warned = False

    def add(self, op_type: str) -> None:
        costs = {"build": self.cost_build, "review": self.cost_review, "plan": self.cost_plan}
        self.total_cost += costs.get(op_type, 0.0)

    def check(self) -> Tuple[bool, str]:
        """Returns (over_budget, message)."""
        if self.max_cost <= 0:
            return False, ""
        pct = (self.total_cost / self.max_cost) * 100
        if pct >= 100:
            return True, f"Budget exhausted: ${self.total_cost:.2f}/${self.max_cost:.2f}"
        if pct >= self.warn_pct and not self.warned:
            self.warned = True
            return False, f"Budget warning: ${self.total_cost:.2f}/${self.max_cost:.2f} ({pct:.0f}%)"
        return False, ""

    def to_dict(self) -> Dict[str, Any]:
        return {"total_cost_usd": round(self.total_cost, 4), "max_cost_usd": self.max_cost}


# ── Metrics collector ──────────────────────────────────────────────

class MetricsCollector:
    """Collect per-build and aggregate metrics."""

    def __init__(self):
        self.builds: List[Dict[str, Any]] = []
        self.reviews: List[Dict[str, Any]] = []
        self.start_time = time.monotonic()

    def record_build(self, plan_cycle: int, review_cycle: int, build_iter: int,
                     duration: float, files_changed: List[str], success: bool) -> None:
        self.builds.append({
            "plan_cycle": plan_cycle, "review_cycle": review_cycle,
            "build_iter": build_iter, "duration_sec": round(duration, 2),
            "files_changed": files_changed, "success": success,
            "timestamp": dt.datetime.now().isoformat(),
        })

    def record_review(self, plan_cycle: int, review_cycle: int,
                      verdict: str, duration: float) -> None:
        self.reviews.append({
            "plan_cycle": plan_cycle, "review_cycle": review_cycle,
            "verdict": verdict, "duration_sec": round(duration, 2),
            "timestamp": dt.datetime.now().isoformat(),
        })

    def summary(self) -> Dict[str, Any]:
        total_dur = time.monotonic() - self.start_time
        pass_count = sum(1 for r in self.reviews if r["verdict"] == "pass")
        return {
            "total_duration_sec": round(total_dur, 2),
            "total_builds": len(self.builds),
            "total_reviews": len(self.reviews),
            "review_pass_rate": round(pass_count / max(len(self.reviews), 1), 2),
            "avg_build_duration": round(
                sum(b["duration_sec"] for b in self.builds) / max(len(self.builds), 1), 2
            ),
            "total_files_changed": len(set(
                f for b in self.builds for f in b["files_changed"]
            )),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"builds": self.builds, "reviews": self.reviews, "summary": self.summary()}


# ── Checkpoint / Resume ────────────────────────────────────────────

class Checkpoint:
    """Save and restore orchestrator state for crash recovery."""

    def __init__(self, run_dir: Path):
        self.path = run_dir / "run_state.json"

    def save(self, state: Dict[str, Any]) -> None:
        state["_saved_at"] = dt.datetime.now().isoformat()
        write_text(self.path, json.dumps(state, indent=2, default=str))

    def load(self) -> Optional[Dict[str, Any]]:
        text = read_text(self.path)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def find_latest(logs_base: Path) -> Optional[Path]:
        """Find the most recent run directory with a checkpoint."""
        if not logs_base.exists():
            return None
        runs = sorted(logs_base.iterdir(), reverse=True)
        for run in runs:
            if (run / "run_state.json").exists():
                state = read_text(run / "run_state.json")
                if state:
                    data = json.loads(state)
                    if not data.get("done", False):
                        return run
        return None


# ── Shell & process helpers ────────────────────────────────────────

def run_command(
    name: str,
    cmd: List[str],
    cwd: Path,
    timeout_sec: int,
    input_text: Optional[str] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> CommandResult:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        return CommandResult(
            name, cmd, proc.returncode, proc.stdout or "", proc.stderr or "",
            time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            name, cmd, 124,
            (exc.stdout.decode("utf-8", errors="ignore") if isinstance(exc.stdout, bytes) else exc.stdout) or "",
            ((exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else exc.stderr) or "")
            + f"\nTimed out after {timeout_sec}s.",
            time.monotonic() - start,
            timed_out=True,
        )


def run_shell(name: str, command: str, cwd: Path, timeout_sec: int = 120) -> CommandResult:
    if sys.platform == "win32":
        return run_command(name, ["cmd", "/c", command], cwd=cwd, timeout_sec=timeout_sec)
    return run_command(name, ["bash", "-lc", command], cwd=cwd, timeout_sec=timeout_sec)


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def write_result(path: Path, result: CommandResult) -> None:
    body = "\n".join([
        f"# {result.name}",
        "",
        f"returncode: {result.returncode}",
        f"seconds: {result.seconds:.2f}",
        f"timed_out: {result.timed_out}",
        "",
        "## Command",
        "```text",
        " ".join(result.cmd),
        "```",
        "",
        "## STDOUT",
        "```text",
        result.stdout or "",
        "```",
        "",
        "## STDERR",
        "```text",
        result.stderr or "",
        "```",
    ])
    write_text(path, body)


# ── Preflight ──────────────────────────────────────────────────────

def preflight(config: Dict[str, Any], project_root: Path, run_dir: Path) -> None:
    checks: List[Dict[str, Any]] = []

    # Check new [agents.*] config
    agents_cfg = config.get("agents", {})
    for role in ("planner", "builder", "reviewer"):
        acfg = agents_cfg.get(role, {})
        cmd = str(acfg.get("command", "kiro-cli"))
        agent_name = acfg.get("agent", f"ai-{role}")
        agent_file = project_root / ".kiro" / "agents" / f"{agent_name}.json"
        checks.append({
            "component": f"agents.{role}", "command": cmd,
            "agent": agent_name, "agent_file_exists": agent_file.exists(),
            "ok": command_exists(cmd) and agent_file.exists(),
        })

    # Legacy checks
    for section in ("kiro", "kiro_builder"):
        cfg = config.get(section, {})
        if not cfg.get("enabled", False):
            checks.append({"component": section, "enabled": False, "ok": True})
            continue
        cmd = str(cfg.get("command", "kiro-cli"))
        checks.append({
            "component": section, "enabled": True,
            "command": cmd, "ok": command_exists(cmd),
        })

    checks.append({
        "component": "git", "ok": (project_root / ".git").exists() or any(
            (d / ".git").exists() for d in project_root.iterdir() if d.is_dir()
        ),
        "note": "recommended for change tracking",
    })
    checks.append({
        "component": "brief", "ok": (project_root / str(config["project"].get("brief_file", "PROJECT_BRIEF.md"))).exists(),
    })

    write_text(run_dir / "preflight.json", json.dumps(checks, indent=2))
    hard_fail = [
        c for c in checks
        if c.get("component", "").startswith("agents.") and not c.get("ok")
    ]
    if hard_fail:
        details = "; ".join(
            f"{c['component']}(cmd={c.get('command')}, file={c.get('agent_file_exists')})"
            for c in hard_fail
        )
        raise OrchestratorError(f"Preflight failed: {details}. See {run_dir / 'preflight.json'}")


# ── Repo inspection ───────────────────────────────────────────────

def collect_repo_snapshot(project_root: Path, test_output: str = "", config: Optional[Dict[str, Any]] = None) -> str:
    chunks = []
    # Detect sub-repos (directories with .git inside project_root)
    sub_repos = sorted(
        d.name for d in project_root.iterdir()
        if d.is_dir() and (d / ".git").exists()
    )
    if sub_repos:
        # Multi-repo mode: run git commands per sub-repo IN PARALLEL
        def _snapshot_repo(repo: str) -> str:
            repo_path = project_root / repo
            git_cmds = [
                ("status", "git status --short | head -40 || true"),
                ("diff", "git diff --stat | head -30 || true"),
            ]
            repo_chunks = []
            for label, cmd in git_cmds:
                res = run_shell(f"{repo}_{label}", cmd, cwd=repo_path, timeout_sec=30)
                out = (res.stdout + res.stderr).strip()
                if out:
                    repo_chunks.append(f"  {label}:\n{out}")
            if repo_chunks:
                return f"## {repo}/\n```text\n" + "\n".join(repo_chunks) + "\n```"
            return f"## {repo}/\n```text\nclean\n```"

        with ThreadPoolExecutor(max_workers=min(len(sub_repos), 4)) as ex:
            chunks = list(ex.map(_snapshot_repo, sub_repos))
    else:
        # Single-repo fallback
        for label, cmd in [
            ("git_status", "git status --short || true"),
            ("git_diff_stat", "git diff --stat || true"),
        ]:
            res = run_shell(label, cmd, cwd=project_root, timeout_sec=60)
            chunks.append(f"## {label}\n```text\n{trim(res.stdout + res.stderr, 6000)}\n```")
    # File listing
    # File listing with configurable exclusions
    exclude_patterns = (config or {}).get("project", {}).get(
        "snapshot_exclude",
        ["\\.git/", "\\.agentloop/", "node_modules/", "\\.venv/", "__pycache__/"]
    )
    grep_pattern = "|".join(exclude_patterns) if exclude_patterns else "^$"
    res = run_shell("recent_files",
        f"find . -maxdepth 3 -type f | sed 's#^./##' "
        f"| grep -Ev '({grep_pattern})' "
        f"| sort | head -150",
        cwd=project_root, timeout_sec=60)
    chunks.append(f"## recent_files\n```text\n{trim(res.stdout, 6000)}\n```")
    if test_output:
        chunks.append(f"## latest_test_output\n```text\n{trim(test_output, 12000)}\n```")
    return "\n\n".join(chunks)


def worktree_hash(project_root: Path) -> str:
    parts: List[str] = []
    sub_repos = sorted(
        d.name for d in project_root.iterdir()
        if d.is_dir() and (d / ".git").exists()
    )
    if sub_repos:
        def _hash_repo(repo: str) -> str:
            repo_path = project_root / repo
            out = []
            for cmd in [
                "git status --porcelain=v1 | head -50 || true",
                "git diff --stat | head -20 || true",
            ]:
                res = run_shell("hash", cmd, cwd=repo_path, timeout_sec=30)
                out.append(res.stdout)
            return "\n".join(out)

        with ThreadPoolExecutor(max_workers=min(len(sub_repos), 4)) as ex:
            parts = list(ex.map(_hash_repo, sub_repos))
    else:
        for cmd in [
            "git status --porcelain=v1 || true",
            "git diff --stat || true",
            "git ls-files --others --exclude-standard | head -200 || true",
        ]:
            res = run_shell("hash", cmd, cwd=project_root, timeout_sec=60)
            parts.append(res.stdout)
    return hashlib.sha256(
        "\n".join(parts).encode("utf-8", errors="ignore")
    ).hexdigest()


# ── Agent wrappers ─────────────────────────────────────────────────

def _build_kiro_cmd(agent_cfg: Dict[str, Any]) -> List[str]:
    """Build kiro-cli command from agent config."""
    cmd = [str(agent_cfg.get("command", "kiro-cli")), "chat", "--no-interactive"]
    if agent_cfg.get("agent"):
        cmd.extend(["--agent", str(agent_cfg["agent"])])
    if agent_cfg.get("require_mcp_startup"):
        cmd.append("--require-mcp-startup")
    if agent_cfg.get("trust_all_tools"):
        cmd.append("--trust-all-tools")
    elif agent_cfg.get("trust_tools"):
        cmd.append("--trust-tools=" + str(agent_cfg["trust_tools"]))
    return cmd


def _get_agent_cfg(config: Dict[str, Any], role: str) -> Dict[str, Any]:
    """Get agent config — prefer new [agents.X] format, fallback to legacy."""
    agents = config.get("agents", {})
    if role in agents and agents[role].get("command"):
        return agents[role]
    # Legacy fallback
    if role == "planner":
        return config.get("kiro", {})
    elif role == "builder":
        return config.get("kiro_builder", {})
    elif role == "reviewer":
        # Fallback: use planner config with reviewer agent
        cfg = dict(config.get("kiro", {}))
        cfg["agent"] = "ai-reviewer"
        return cfg
    return config.get("kiro", {})


def run_tests(config: Dict[str, Any], project_root: Path, log_dir: Path) -> Tuple[bool, str]:
    """Run tests — supports both single test_command and [[project.tests]] array."""
    tests_array = config["project"].get("tests", [])

    if tests_array:
        # Parallel test execution from [[project.tests]] array
        results: List[Tuple[str, CommandResult]] = []

        def _run_one(test_cfg: Dict[str, Any]) -> Tuple[str, CommandResult]:
            name = test_cfg.get("name", "test")
            cmd = test_cfg.get("command", "")
            cwd = project_root / test_cfg.get("cwd", ".")
            timeout = int(test_cfg.get("timeout_sec", 300))
            res = run_shell(f"test-{name}", cmd, cwd=cwd, timeout_sec=timeout)
            return name, res

        with ThreadPoolExecutor(max_workers=min(len(tests_array), 4)) as ex:
            futures = [ex.submit(_run_one, t) for t in tests_array]
            for f in as_completed(futures):
                results.append(f.result())

        all_ok = all(r.ok for _, r in results)
        combined_output = "\n".join(
            f"── {name} ({'✅' if r.ok else '❌'}) ──\n{r.combined(limit=8000)}"
            for name, r in results
        )
        for name, r in results:
            write_result(log_dir / f"test_{name}.md", r)
        return all_ok, combined_output

    # Fallback: single test_command
    command = str(config["project"].get("test_command", "")).strip()
    if not command:
        return False, "No test_command configured; skipped."
    result = run_shell("tests", command, cwd=project_root, timeout_sec=1800)
    write_result(log_dir / "tests.md", result)
    return result.ok, result.combined(limit=30000)


def kiro_planner(
    config: Dict[str, Any], project_root: Path, prompt: str,
) -> CommandResult:
    """Kiro used as planner (read-only, Opus model)."""
    cfg = _get_agent_cfg(config, "planner")
    cmd = _build_kiro_cmd(cfg)
    cmd.append(prompt)
    return run_command(
        "kiro-planner", cmd, cwd=project_root,
        timeout_sec=int(cfg.get("timeout_sec", 3600)),
    )


def kiro_builder(config: Dict[str, Any], project_root: Path, prompt: str) -> CommandResult:
    """Kiro used as the builder/code writer."""
    cfg = _get_agent_cfg(config, "builder")
    cmd = _build_kiro_cmd(cfg)
    cmd.append(prompt)
    return run_command(
        "kiro-builder", cmd, cwd=project_root,
        timeout_sec=int(cfg.get("timeout_sec", 7200)),
    )


def kiro_reviewer(config: Dict[str, Any], project_root: Path, prompt: str) -> CommandResult:
    """Kiro used as separate reviewer (read-only, can use faster/cheaper model)."""
    cfg = _get_agent_cfg(config, "reviewer")
    cmd = _build_kiro_cmd(cfg)
    cmd.append(prompt)
    return run_command(
        "kiro-reviewer", cmd, cwd=project_root,
        timeout_sec=int(cfg.get("timeout_sec", 3600)),
    )


def kiro_builder_parallel(
    config: Dict[str, Any], project_root: Path, prompt: str, sub_repos: List[str]
) -> CommandResult:
    """Run multiple builders in parallel — one per sub-repo focus area.

    Splits the prompt into repo-specific tasks and runs them concurrently.
    Falls back to single builder if only 1 repo needs changes.
    """
    if len(sub_repos) <= 1:
        return kiro_builder(config, project_root, prompt)

    cfg = _get_agent_cfg(config, "builder")
    timeout = int(cfg.get("timeout_sec", 7200))
    max_workers = int(config.get("loop", {}).get("max_parallel_builders", 3))

    def build_one(repo_focus: str) -> CommandResult:
        focused_prompt = (
            f"{prompt}\n\n## FOCUS\n"
            f"Focus ONLY on `{repo_focus}/` directory in this iteration. "
            f"Do not modify files outside `{repo_focus}/`."
        )
        cmd = _build_kiro_cmd(cfg)
        cmd.append(focused_prompt)
        return run_command(f"kiro-builder-{repo_focus}", cmd, cwd=project_root, timeout_sec=timeout)

    results: List[CommandResult] = []
    with ThreadPoolExecutor(max_workers=min(len(sub_repos), max_workers)) as executor:
        futures = {executor.submit(build_one, repo): repo for repo in sub_repos}
        for future in as_completed(futures):
            repo = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"    [parallel] {repo} done ({result.seconds:.0f}s)")
            except Exception as e:
                print(f"    [parallel] {repo} failed: {e}")

    # Merge results
    combined_stdout = "\n".join(r.stdout for r in results if r.stdout)
    combined_stderr = "\n".join(r.stderr for r in results if r.stderr)
    max_time = max((r.seconds for r in results), default=0)
    any_failed = any(not r.ok for r in results)

    return CommandResult(
        name="kiro-builder-parallel",
        cmd=["parallel", str(len(sub_repos)), "builders"],
        returncode=1 if any_failed else 0,
        stdout=combined_stdout,
        stderr=combined_stderr,
        seconds=max_time,
    )


# ── JSON extraction ────────────────────────────────────────────────

def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return None
    # Direct parse.
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    # Fenced code blocks.
    if "```" in text:
        for segment in text.split("```"):
            candidate = segment
            if candidate.lstrip().startswith("json"):
                candidate = candidate.lstrip()[4:]
            try:
                value = json.loads(candidate.strip())
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    # Balanced braces scan.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


# ── History formatting ─────────────────────────────────────────────

def compile_review_history(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "No review history available."
    parts: List[str] = []
    for h in history:
        parts.append(
            f"### Review cycle {h['review_cycle']}\n"
            f"- Builds completed: {h['builds_completed']}\n"
            f"- Tests OK: {h['tests_ok']}\n"
            f"- Builder summary: {h.get('builder_summary', 'N/A')}\n"
            f"- Kiro verdict: {h.get('kiro_verdict', 'N/A')}\n"
            f"- Kiro feedback: {h.get('kiro_feedback', 'N/A')[:500]}\n"
        )
    return "\n".join(parts)


# ── Main orchestrator loop ─────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:  # noqa: C901
    # Load .env file if present (simple implementation, no dotenv dependency)
    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    parser = argparse.ArgumentParser(
        description="AI Agent Orchestrator — nested build-review-plan loop.",
    )
    parser.add_argument("--config", default="agentloop.toml", help="Path to config TOML.")
    parser.add_argument("--project", default=None, help="Override project.path.")
    parser.add_argument("--brief", default=None, help="Override project.brief_file.")
    parser.add_argument("--plan-cycles", type=int, default=None, help="Override loop.plan_cycles.")
    parser.add_argument("--review-cycles", type=int, default=None, help="Override loop.review_cycles.")
    parser.add_argument("--build-iterations", type=int, default=None, help="Override loop.build_iterations.")
    parser.add_argument("--dry-run", action="store_true", help="Write prompts but skip AI CLI calls.")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip CLI/env checks.")
    parser.add_argument("--resume", nargs="?", const="latest", default=None,
                        help="Resume from checkpoint. Use 'latest' or path to run dir.")
    args = parser.parse_args(argv)

    # ── Config ──
    cfg_path = Path(args.config).expanduser().resolve()
    config = load_config(cfg_path)
    if args.project:
        config["project"]["path"] = args.project
    if args.brief:
        config["project"]["brief_file"] = args.brief
    if args.plan_cycles is not None:
        config["loop"]["plan_cycles"] = args.plan_cycles
    if args.review_cycles is not None:
        config["loop"]["review_cycles"] = args.review_cycles
    if args.build_iterations is not None:
        config["loop"]["build_iterations"] = args.build_iterations

    project_root = ensure_project_root(Path(config["project"]["path"]))
    logs_base = project_root / str(config["project"].get("logs_dir", ".agentloop/runs"))

    # ── Resume or new run ──
    resumed_state: Optional[Dict[str, Any]] = None
    if args.resume:
        if args.resume == "latest":
            resume_dir = Checkpoint.find_latest(logs_base)
        else:
            resume_dir = Path(args.resume).expanduser().resolve()
        if resume_dir and (resume_dir / "run_state.json").exists():
            run_dir = resume_dir
            ckpt = Checkpoint(run_dir)
            resumed_state = ckpt.load()
            print(f"[agentloop] Resuming from: {run_dir}")
        else:
            print("[agentloop] No checkpoint found, starting fresh.")
            run_dir = logs_base / now_slug()
            run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = logs_base / now_slug()
        run_dir.mkdir(parents=True, exist_ok=True)

    write_text(run_dir / "effective_config.json", json.dumps(config, indent=2))

    if not args.skip_preflight and not args.dry_run:
        preflight(config, project_root, run_dir)

    brief_path = project_root / str(config["project"].get("brief_file", "PROJECT_BRIEF.md"))
    brief = read_text(brief_path)
    if not brief.strip():
        raise OrchestratorError(f"Brief is empty or missing: {brief_path}")

    prompts_dir = Path(__file__).resolve().parents[1] / "prompts"

    # ── Telegram ──
    tg_cfg = config.get("telegram", {})
    tg = TelegramNotifier(
        bot_token=tg_cfg.get("bot_token", "") or os.environ.get("TG_BOT_TOKEN", ""),
        chat_id=tg_cfg.get("chat_id", "") or os.environ.get("TG_CHAT_ID", ""),
        enabled=tg_cfg.get("enabled", False),
    )

    # ── Initialize trackers ──
    cost = CostTracker(config)
    metrics = MetricsCollector()
    checkpoint = Checkpoint(run_dir)
    ctx_store = ContextStore(run_dir)

    # ── Loop parameters ──
    plan_cycles = int(config["loop"].get("plan_cycles", 3))
    review_cycles = int(config["loop"].get("review_cycles", 5))
    build_iterations = int(config["loop"].get("build_iterations", 10))
    no_change_limit = int(config["loop"].get("no_change_limit", 2))
    sleep_sec = float(config["loop"].get("sleep_between_rounds_sec", 1))
    max_total_builds = int(config["loop"].get("max_total_builds", 50))
    max_discovery_rounds = int(config["loop"].get("max_discovery_rounds", 2))

    kiro_enabled = config["kiro"].get("enabled", True)
    kiro_builder_enabled = config["kiro_builder"].get("enabled", True)

    # ── State (restore from checkpoint if resuming) ──
    if resumed_state:
        kiro_plan = resumed_state.get("kiro_plan", "")
        done = resumed_state.get("done", False)
        final_reason = resumed_state.get("final_reason", "Max plan cycles reached.")
        total_builds = resumed_state.get("total_builds", 0)
        discovery_rounds = resumed_state.get("discovery_rounds", 0)
        start_pc = resumed_state.get("plan_cycle", 1)
        start_rc = resumed_state.get("review_cycle", 1)
        start_bi = resumed_state.get("build_iteration", 1)
        cost.total_cost = resumed_state.get("total_cost_usd", 0.0)
        kiro_feedback = resumed_state.get("kiro_feedback", "")
        print(f"[agentloop] Resumed: pc={start_pc} rc={start_rc} bi={start_bi} builds={total_builds}")
    else:
        kiro_plan = ""
        done = False
        final_reason = "Max plan cycles reached."
        total_builds = 0
        discovery_rounds = 0
        start_pc = 1
        start_rc = 1
        start_bi = 1
        kiro_feedback = ""

    pc = 0

    print(f"[agentloop] project: {project_root}")
    print(f"[agentloop] logs:    {run_dir}")
    print(f"[agentloop] cycles:  plan={plan_cycles}  review={review_cycles}  build={build_iterations}")
    print(f"[agentloop] roles:   planner=ai-planner  builder=ai-builder  reviewer=ai-reviewer")
    if config.get("budget", {}).get("max_cost_usd", 0) > 0:
        print(f"[agentloop] budget:  ${config['budget']['max_cost_usd']:.2f}")

    tg.notify_start(project_root.name, plan_cycles, review_cycles, build_iterations)

    # ═══════════════════════════════════════════════════════════════
    #  OUTER LOOP: Kiro plan cycles
    # ═══════════════════════════════════════════════════════════════
    for pc in range(start_pc, plan_cycles + 1):
        plan_dir = run_dir / f"plan_{pc:02d}"
        plan_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"[agentloop] PLAN CYCLE {pc}/{plan_cycles}")
        print(f"{'=' * 60}")

        # ── Kiro initial plan (cycle 1 only, skip if resumed with plan) ──
        if pc == 1 and not kiro_plan:
            if kiro_enabled:
                plan_prompt = render_template(
                    prompts_dir / "planner_kiro.md", brief=brief,
                )
                write_text(plan_dir / "kiro_plan_prompt.md", plan_prompt)
                if args.dry_run:
                    kiro_plan = "DRY RUN: Kiro planner not executed."
                else:
                    result = retry_call(
                        lambda: kiro_planner(config, project_root, plan_prompt),
                        config, "planner"
                    )
                    write_result(plan_dir / "kiro_plan_output.md", result)
                    kiro_plan = result.combined(limit=30000)
                    cost.add("plan")
            else:
                kiro_plan = brief
            tg.notify_plan(pc, plan_cycles, kiro_plan[:500])

        # For pc > 1: kiro_plan was updated by previous cycle's replan.
        if pc > 1:
            write_text(plan_dir / "kiro_plan_carried.md", kiro_plan)
            tg.notify_plan(pc, plan_cycles, kiro_plan[:500])

        kiro_feedback = kiro_feedback if (pc == start_pc and kiro_feedback) else ""
        review_history: List[Dict[str, Any]] = []
        last_test_output = ""

        # ═══════════════════════════════════════════════════════════
        #  MIDDLE LOOP: Build + Review cycles
        # ═══════════════════════════════════════════════════════════
        _rc_start = start_rc if pc == start_pc else 1
        for rc in range(_rc_start, review_cycles + 1):
            review_dir = plan_dir / f"review_{rc:02d}"
            review_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n  {'-' * 50}")
            print(f"  [agentloop] REVIEW CYCLE {rc}/{review_cycles}")
            print(f"  {'-' * 50}")

            no_change_streak = 0
            builder_output = ""
            builds_completed = 0

            # ═══════════════════════════════════════════════════════
            #  INNER LOOP: Build iterations
            # ═══════════════════════════════════════════════════════
            _bi_start = start_bi if (pc == start_pc and rc == _rc_start) else 1
            for bi in range(_bi_start, build_iterations + 1):
                total_builds += 1
                builds_completed = bi
                print(f"    [agentloop] Build {bi}/{build_iterations}  (total #{total_builds})")

                # Budget check
                over, msg = cost.check()
                if over:
                    print(f"    [agentloop] {msg}")
                    tg.send(f"💰 {msg}")
                    done = True
                    final_reason = msg
                    break
                elif msg:
                    print(f"    [agentloop] ⚠️ {msg}")
                    tg.send(f"⚠️ {msg}")

                # Safety: max total builds limit
                if total_builds > max_total_builds:
                    print(f"    [agentloop] Max total builds ({max_total_builds}) reached — stopping")
                    done = True
                    final_reason = f"Max total builds limit ({max_total_builds}) reached."
                    break

                snapshot = collect_repo_snapshot(project_root)

                if bi == 1:
                    # First build: full prompt with plan + feedback
                    build_prompt = render_template(
                        prompts_dir / "builder_codex.md",
                        round_no=str(total_builds),
                        brief=trim(brief, 30000),
                        kiro_plan=trim(kiro_plan, 30000),
                        previous_feedback=trim(kiro_feedback, 30000),
                        previous_builder_output=trim(builder_output, 16000),
                        repo_snapshot=trim(snapshot, 24000),
                        next_prompt_override=ctx_store.get_context_summary(2000),
                    )
                else:
                    # Continue: lighter prompt
                    build_prompt = render_template(
                        prompts_dir / "continue_codex.md",
                        build_iter=str(bi),
                        total_build_iters=str(build_iterations),
                        review_cycle=str(rc),
                        brief=trim(brief, 12000),
                        kiro_plan=trim(kiro_plan, 12000),
                        previous_feedback=trim(kiro_feedback, 8000),
                        repo_snapshot=trim(snapshot, 12000),
                    )

                write_text(review_dir / f"build_{bi:02d}_prompt.md", build_prompt)

                if args.dry_run or not kiro_builder_enabled:
                    builder_output = "DRY RUN or builder disabled: not executed."
                else:
                    hash_before = worktree_hash(project_root)
                    build_start = time.monotonic()
                    # Detect sub-repos for parallel build
                    exclude_dirs = set(config.get("project", {}).get("exclude_dirs", []))
                    sub_repos = sorted(
                        d.name for d in project_root.iterdir()
                        if d.is_dir() and (d / ".git").exists()
                        and d.name not in exclude_dirs
                    )
                    if len(sub_repos) > 1 and bi > 1:
                        result = kiro_builder_parallel(config, project_root, build_prompt, sub_repos)
                    else:
                        result = retry_call(
                            lambda: kiro_builder(config, project_root, build_prompt),
                            config, "builder"
                        )
                    build_duration = time.monotonic() - build_start
                    write_result(review_dir / f"build_{bi:02d}_output.md", result)
                    builder_output = result.combined(limit=40000)
                    cost.add("build")

                    if not result.ok:
                        builder_output += (
                            "\n\n[orchestrator] Builder returned non-zero exit code."
                        )

                    # No-change detection
                    hash_after = worktree_hash(project_root)
                    report = extract_json_object(result.stdout or "")
                    files_changed = (report.get("files_changed", []) if report else [])
                    builder_reports_changes = bool(files_changed)

                    if hash_before == hash_after and not builder_reports_changes:
                        no_change_streak += 1
                        print(f"    [agentloop] No change (streak: {no_change_streak})")
                    else:
                        no_change_streak = 0
                        if builder_reports_changes:
                            print(f"    [agentloop] Builder changed: {files_changed[:3]}")
                            tg.notify_build_progress(pc, rc, total_builds, files_changed)

                    # Record metrics
                    metrics.record_build(pc, rc, bi, build_duration, files_changed, result.ok)

                    # Update context store from builder report
                    ctx_store.update_from_builder_report(report)

                    # Save checkpoint after each build
                    checkpoint.save({
                        "plan_cycle": pc, "review_cycle": rc, "build_iteration": bi + 1,
                        "total_builds": total_builds, "kiro_plan": kiro_plan[:5000],
                        "kiro_feedback": kiro_feedback[:2000], "done": False,
                        "final_reason": final_reason, "discovery_rounds": discovery_rounds,
                        "total_cost_usd": cost.total_cost,
                    })

                    if no_change_streak >= no_change_limit:
                        print("    [agentloop] No-change limit hit — ending build phase")
                        break

                    # Builder self-report: complete
                    if report and report.get("state") == "complete":
                        print("    [agentloop] Builder reports complete")
                        break

                    # Git push after each successful build with changes
                    git_cfg = config.get("git", {})
                    if git_cfg.get("auto_push", False) and no_change_streak == 0:
                        branch = str(git_cfg.get("branch", "main"))
                        summary = report.get("summary", f"Build #{total_builds}") if report else f"Build #{total_builds}"
                        commit_msg = f"[agentloop] Build #{total_builds}: {summary[:60]}"
                        run_shell("git-add", "git add -A", cwd=project_root, timeout_sec=60)
                        res = run_shell("git-commit", f'git commit -m "{commit_msg}"', cwd=project_root, timeout_sec=60)
                        if res.ok:
                            res = run_shell("git-push", f"git push -u origin {branch}", cwd=project_root, timeout_sec=120)
                            if res.ok:
                                print(f"    [agentloop] ✅ Pushed to origin/{branch}")
                                tg.notify_push(branch, commit_msg, True)
                            else:
                                print(f"    [agentloop] ⚠️ Push failed: {res.stderr[:100]}")
                                tg.notify_push(branch, commit_msg, False)

                if bi < build_iterations:
                    time.sleep(sleep_sec)

            # ── End of build iterations ──
            tg.notify_build_done(pc, rc, builds_completed)

            # Budget/limit reached — skip review and break middle loop
            if done:
                break

            # ── Run tests + collect snapshot (parallel if enabled) ──
            parallel_review = config["loop"].get("parallel_review", False)
            if parallel_review and not args.dry_run:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                    test_future = ex.submit(run_tests, config, project_root, review_dir)
                    snap_future = ex.submit(collect_repo_snapshot, project_root, "")
                    tests_ok, test_output = test_future.result()
                    snapshot_after = snap_future.result()
                # Re-collect with test output for full snapshot
                snapshot_after = collect_repo_snapshot(project_root, test_output)
            else:
                tests_ok, test_output = run_tests(config, project_root, review_dir)
                snapshot_after = collect_repo_snapshot(project_root, test_output)
            last_test_output = test_output

            # ── Kiro review (separate reviewer agent) ──
            kiro_verdict = "unknown"
            if kiro_enabled:
                review_prompt = render_template(
                    prompts_dir / "review_kiro.md",
                    round_no=str(rc),
                    brief=trim(brief, 30000),
                    builder_output=trim(builder_output, 24000),
                    repo_snapshot=trim(snapshot_after, 30000),
                    test_output=trim(test_output, 20000),
                )
                write_text(review_dir / "kiro_review_prompt.md", review_prompt)

                if args.dry_run:
                    kiro_feedback = "DRY RUN: Kiro reviewer not executed."
                else:
                    review_start = time.monotonic()
                    result = retry_call(
                        lambda: kiro_reviewer(config, project_root, review_prompt),
                        config, "reviewer"
                    )
                    review_duration = time.monotonic() - review_start
                    write_result(review_dir / "kiro_review_output.md", result)
                    kiro_feedback = result.combined(limit=40000)
                    cost.add("review")

                    verdict_json = extract_json_object(result.stdout or "")
                    if verdict_json:
                        kiro_verdict = str(verdict_json.get("verdict", "unknown"))
                        bp = verdict_json.get("builder_prompt", "")
                        if bp:
                            kiro_feedback = f"DIRECT INSTRUCTION: {bp}\n\nFull review:\n{kiro_feedback}"

                    metrics.record_review(pc, rc, kiro_verdict, review_duration)

                    # Update context store from review
                    ctx_store.update_from_review(verdict_json)

                tg.notify_review(pc, rc, review_cycles, kiro_feedback[:300])
            else:
                kiro_feedback = "Kiro reviewer disabled."

            review_history.append({
                "review_cycle": rc,
                "builds_completed": builds_completed,
                "tests_ok": tests_ok,
                "builder_summary": builder_output[:500],
                "kiro_verdict": kiro_verdict,
                "kiro_feedback": kiro_feedback[:800],
            })

            # Early stop: Kiro says pass
            if kiro_verdict == "pass":
                print("  [agentloop] Kiro verdict: pass — ending review cycles")
                break

            if rc < review_cycles:
                time.sleep(sleep_sec)

        # ── End of review cycles ──

        # Budget/limit reached during builds — skip replan
        if done:
            break

        # ═══════════════════════════════════════════════════════════
        #  Kiro replan (end of each plan cycle)
        # ═══════════════════════════════════════════════════════════
        if kiro_enabled:
            history_text = compile_review_history(review_history)
            snapshot = collect_repo_snapshot(project_root, last_test_output)
            replan_prompt = render_template(
                prompts_dir / "replan_kiro.md",
                plan_cycle=str(pc),
                total_plan_cycles=str(plan_cycles),
                brief=trim(brief, 30000),
                kiro_plan=trim(kiro_plan, 20000),
                history=trim(history_text, 30000),
                test_output=trim(last_test_output, 12000),
                repo_snapshot=trim(snapshot, 24000),
            )
            write_text(plan_dir / "kiro_replan_prompt.md", replan_prompt)

            if args.dry_run:
                replan_text = json.dumps({
                    "done": False, "reason": "dry run",
                    "updated_plan": "Continue implementation.",
                    "next_review_cycles": review_cycles,
                    "next_build_iterations": build_iterations,
                })
            else:
                replan_result = retry_call(
                    lambda: kiro_planner(config, project_root, replan_prompt),
                    config, "replanner"
                )
                write_result(plan_dir / "kiro_replan_output.md", replan_result)
                replan_text = replan_result.stdout or replan_result.stderr
                cost.add("plan")

            replan_json = extract_json_object(replan_text)
            write_text(
                plan_dir / "kiro_replan_parsed.json",
                json.dumps(replan_json or {}, indent=2),
            )

            if replan_json:
                done = bool(replan_json.get("done", False))
                final_reason = str(
                    replan_json.get("reason", f"Plan cycle {pc} complete.")
                )
                # Dynamic cycles: Kiro decides next cycle parameters
                if replan_json.get("next_review_cycles"):
                    review_cycles = min(int(replan_json["next_review_cycles"]), 10)
                    print(f"  [agentloop] Dynamic: next review_cycles={review_cycles}")
                if replan_json.get("next_build_iterations"):
                    build_iterations = min(int(replan_json["next_build_iterations"]), 20)
                    print(f"  [agentloop] Dynamic: next build_iterations={build_iterations}")

                if not done:
                    updated = str(replan_json.get("updated_plan", "")).strip()
                    if updated:
                        kiro_plan = updated
                    else:
                        kiro_plan = replan_text

            tg.notify_replan(
                pc, plan_cycles,
                (final_reason if done else kiro_plan)[:500],
            )

        # ═══════════════════════════════════════════════════════════
        #  Auto-discovery: when done, analyze for new opportunities
        # ═══════════════════════════════════════════════════════════
        if done and kiro_enabled and not args.dry_run:
            discovery_rounds += 1
            if discovery_rounds > max_discovery_rounds:
                print(f"  [agentloop] Max discovery rounds ({max_discovery_rounds}) reached — stopping")
            else:
                print(f"  [agentloop] Running auto-discovery analysis ({discovery_rounds}/{max_discovery_rounds})...")
                snapshot = collect_repo_snapshot(project_root, last_test_output)
                discovery_prompt = render_template(
                    prompts_dir / "discovery_kiro.md",
                    brief=trim(brief, 30000),
                    repo_snapshot=trim(snapshot, 24000),
                    test_output=trim(last_test_output, 12000),
                )
                write_text(plan_dir / "kiro_discovery_prompt.md", discovery_prompt)
                discovery_result = retry_call(
                    lambda: kiro_planner(config, project_root, discovery_prompt),
                    config, "discovery"
                )
                write_result(plan_dir / "kiro_discovery_output.md", discovery_result)
                cost.add("plan")

                discovery_json = extract_json_object(discovery_result.stdout or "")
                if discovery_json and discovery_json.get("new_tasks"):
                    new_tasks = discovery_json["new_tasks"]
                    print(f"  [agentloop] Discovery found {len(new_tasks)} new tasks")
                    tg.notify_discovery(new_tasks, discovery_rounds, max_discovery_rounds)
                    if discovery_json.get("should_continue", False):
                        done = False
                        kiro_plan = str(discovery_json.get("updated_plan", ""))
                        final_reason = "Auto-discovery found new tasks."
                        review_cycles = min(int(discovery_json.get("next_review_cycles", 3)), 10)
                        build_iterations = min(int(discovery_json.get("next_build_iterations", 5)), 20)
                        print(f"  [agentloop] Continuing with discovery plan (R={review_cycles}, B={build_iterations})")
                else:
                    print(f"  [agentloop] No new tasks discovered — truly done.")

        if done:
            print(f"\n[agentloop] DONE: {final_reason}")
            break

        if pc < plan_cycles:
            time.sleep(sleep_sec)

    # ═══════════════════════════════════════════════════════════════
    #  Summary
    # ═══════════════════════════════════════════════════════════════
    summary = {
        "done": done,
        "reason": final_reason,
        "plan_cycles_completed": pc,
        "total_build_iterations": total_builds,
        "cost": cost.to_dict(),
        "metrics": metrics.summary(),
        "logs": str(run_dir),
    }
    write_text(run_dir / "summary.json", json.dumps(summary, indent=2))
    write_text(run_dir / "metrics.json", json.dumps(metrics.to_dict(), indent=2))

    # Final checkpoint
    checkpoint.save({
        "plan_cycle": pc, "review_cycle": review_cycles,
        "build_iteration": 1, "total_builds": total_builds,
        "kiro_plan": kiro_plan[:5000], "kiro_feedback": "",
        "done": done, "final_reason": final_reason,
        "discovery_rounds": discovery_rounds,
        "total_cost_usd": cost.total_cost,
    })

    tg.notify_done(summary)
    print(json.dumps(summary, indent=2))

    # ═══════════════════════════════════════════════════════════════
    #  Git push when production-ready
    # ═══════════════════════════════════════════════════════════════
    git_cfg = config.get("git", {})
    if done and git_cfg.get("auto_push", False) and not args.dry_run:
        branch = str(git_cfg.get("branch", "main"))
        commit_msg = str(git_cfg.get("commit_message", f"[agentloop] Production-ready: {final_reason[:80]}"))
        print(f"\n[agentloop] Pushing to GitHub ({branch})...")
        tg.send(f"📦 *Pushing to GitHub* branch: `{branch}`")

        # git add all changes
        res = run_shell("git-add", "git add -A", cwd=project_root, timeout_sec=60)
        if not res.ok:
            print(f"[agentloop] git add failed: {res.stderr}")
        else:
            # git commit
            res = run_shell(
                "git-commit",
                f'git commit -m "{commit_msg}"',
                cwd=project_root, timeout_sec=60,
            )
            if not res.ok and "nothing to commit" not in res.stdout + res.stderr:
                print(f"[agentloop] git commit failed: {res.stderr}")
            else:
                # git push
                res = run_shell(
                    "git-push",
                    f"git push -u origin {branch}",
                    cwd=project_root, timeout_sec=120,
                )
                if res.ok:
                    print(f"[agentloop] ✅ Pushed to origin/{branch}")
                    tg.send(f"✅ *Pushed to GitHub* origin/{branch}")
                else:
                    print(f"[agentloop] git push failed: {res.stderr}")
                    tg.send(f"❌ *Push failed*: {res.stderr[:200]}")

    return 0 if done else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OrchestratorError as exc:
        print(f"[agentloop:error] {exc}", file=sys.stderr)
        raise SystemExit(1)
