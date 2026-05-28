"""Telegram bot notifier for the AI Agent Orchestrator."""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List


class TelegramNotifier:
    """Sends status updates to a Telegram chat via Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = False):
        self.bot_token = bot_token
        # Support multiple chat IDs separated by comma
        self.chat_ids = [cid.strip() for cid in str(chat_id).split(",") if cid.strip()]
        self.enabled = enabled and bool(bot_token) and len(self.chat_ids) > 0

    def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            return False
        # Clean ANSI codes
        clean = _strip_ansi(text)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        success = False
        for chat_id in self.chat_ids:
            payload = json.dumps({
                "chat_id": chat_id,
                "text": clean[:4096],
                "parse_mode": parse_mode,
            }).encode()
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        success = True
            except Exception:
                pass
        return success

    # ── Notification helpers ───────────────────────────────

    def notify_start(self, project_name: str, plan_cycles: int,
                     review_cycles: int, build_iterations: int) -> None:
        self.send(
            f"🚀 *Orchestrator ishga tushdi*\n\n"
            f"📁 Loyiha: `{_esc(project_name)}`\n"
            f"🔄 Plan: {plan_cycles} | Review: {review_cycles} | Build: {build_iterations}\n"
            f"🤖 Kiro Opus 4.7 (planner + reviewer + builder)"
        )

    def notify_plan(self, cycle: int, total: int, summary: str) -> None:
        clean = _extract_meaningful(summary)
        self.send(
            f"📋 *Plan yaratildi* ({cycle}/{total})\n\n"
            f"{_esc(clean[:800])}"
        )

    def notify_build_done(self, plan_cycle: int, review_cycle: int, builds: int) -> None:
        self.send(
            f"🔨 *Build tugadi*\n\n"
            f"Plan: {plan_cycle} | Review: {review_cycle} | Builds: {builds}"
        )

    def notify_build_progress(self, plan_cycle: int, review_cycle: int,
                              build_num: int, files_changed: List[str]) -> None:
        files_str = "\n".join(f"  • `{f}`" for f in files_changed[:5])
        self.send(
            f"✏️ *Build #{build_num}* (P{plan_cycle} R{review_cycle})\n\n"
            f"O'zgartirilgan fayllar:\n{files_str}"
        )

    def notify_review(self, plan_cycle: int, review_cycle: int,
                      total: int, snippet: str) -> None:
        verdict = _extract_verdict(snippet)
        clean = _extract_meaningful(snippet)
        emoji = "✅" if verdict == "pass" else "🔍"
        self.send(
            f"{emoji} *Review* (P{plan_cycle} R{review_cycle}/{total})\n\n"
            f"Verdict: *{verdict}*\n"
            f"{_esc(clean[:600])}"
        )

    def notify_replan(self, cycle: int, total: int, summary: str) -> None:
        clean = _extract_meaningful(summary)
        self.send(
            f"🧠 *Replan* ({cycle}/{total})\n\n"
            f"{_esc(clean[:800])}"
        )

    def notify_push(self, branch: str, commit_msg: str, success: bool) -> None:
        if success:
            self.send(
                f"📦 *GitHub push* ✅\n\n"
                f"Branch: `{branch}`\n"
                f"Commit: {_esc(commit_msg[:100])}"
            )
        else:
            self.send(f"📦 *GitHub push* ❌ Failed")

    def notify_discovery(self, tasks: List[str], round_num: int, max_rounds: int) -> None:
        tasks_str = "\n".join(f"  {i+1}. {_esc(t)}" for i, t in enumerate(tasks[:7]))
        self.send(
            f"🔎 *Auto-discovery* ({round_num}/{max_rounds})\n\n"
            f"Topilgan vazifalar ({len(tasks)} ta):\n{tasks_str}"
        )

    def notify_error(self, error: str) -> None:
        self.send(f"❌ *Xato*\n\n`{_esc(error[:1500])}`")

    def notify_done(self, summary: Dict[str, Any]) -> None:
        done = summary.get("done", False)
        emoji = "🏁" if done else "⏸️"
        status = "Tugallandi ✅" if done else "To'xtadi ⚠️"
        reason = str(summary.get("reason", ""))
        clean_reason = _extract_meaningful(reason)
        cost_info = summary.get("cost", {})
        metrics_info = summary.get("metrics", {})
        cost_str = f"  • Cost: ${cost_info.get('total_cost_usd', 0):.2f}" if cost_info else ""
        metrics_str = ""
        if metrics_info:
            metrics_str = (
                f"  • Duration: {metrics_info.get('total_duration_sec', 0):.0f}s\n"
                f"  • Review pass rate: {metrics_info.get('review_pass_rate', 0):.0%}\n"
                f"  • Files changed: {metrics_info.get('total_files_changed', 0)}"
            )
        self.send(
            f"{emoji} *{status}*\n\n"
            f"📊 Natija:\n"
            f"  • Plan cycles: {summary.get('plan_cycles_completed')}\n"
            f"  • Total builds: {summary.get('total_build_iterations')}\n"
            f"{cost_str}\n{metrics_str}\n\n"
            f"💬 {_esc(clean_reason[:400])}"
        )

    def notify_budget_warning(self, current: float, limit: float, pct: float) -> None:
        self.send(
            f"⚠️ *Budget ogohlantirish*\n\n"
            f"💰 ${current:.2f} / ${limit:.2f} ({pct:.0f}%)"
        )

    def notify_retry(self, agent: str, attempt: int, max_attempts: int, error: str) -> None:
        self.send(
            f"🔄 *Retry* {agent} ({attempt}/{max_attempts})\n\n"
            f"Xato: `{_esc(error[:200])}`"
        )


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _extract_meaningful(text: str) -> str:
    """Extract meaningful content from raw AI output, removing noise."""
    clean = _strip_ansi(text)
    # Remove common noise patterns
    noise = [
        r'Reading directory:.*?\n',
        r'Successfully read.*?\n',
        r'Batch fs_read.*?\n',
        r'↱ Operation.*?\n',
        r'Summary:.*?\n',
        r'Completed in.*?\n',
        r'All tools are now trusted.*?\n',
        r'Agents can sometimes.*?\n',
        r'WARNING:.*?\n',
        r'\s*⋮\s*\n',
        r'STDOUT:\n',
        r'STDERR:\n',
    ]
    for pattern in noise:
        clean = re.sub(pattern, '', clean)
    # Collapse multiple newlines
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    return clean if clean else "Tafsilotlar mavjud emas"


def _extract_verdict(text: str) -> str:
    """Try to extract verdict from review output."""
    clean = _strip_ansi(text).lower()
    if '"verdict"' in clean:
        if '"pass"' in clean:
            return "pass"
        elif '"needs_work"' in clean:
            return "needs\\_work"
        elif '"blocked"' in clean:
            return "blocked"
    if "pass" in clean[:200]:
        return "pass"
    return "reviewing..."


def _esc(text: str) -> str:
    """Escape Markdown special chars for Telegram."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text[:1000]
