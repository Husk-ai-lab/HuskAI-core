"""Local-only actions. Email and ERP updates are simulated in this phase."""

from __future__ import annotations

import json
import platform
import string
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.automations.pdf import write_pdf
from app.data.models.automation import ActionNode, ActionType
from app.helpers.logging import get_logger
from app.helpers.text import slug
from app.i18n import t

log = get_logger("actions")


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str | None, state: dict[str, Any], fallback: str) -> str:
    if not template:
        return fallback
    flat = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in state.items()}
    try:
        return string.Formatter().vformat(template, (), _SafeDict(flat))
    except (ValueError, IndexError):
        return template


def notify_desktop(title: str, message: str) -> bool:
    try:
        system = platform.system()
        if system == "Darwin":
            script = f'display notification {json.dumps(message[:200])} with title {json.dumps(title[:60])}'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            return True
        if system == "Linux":
            subprocess.run(["notify-send", title[:60], message[:200]], check=False, timeout=5)
            return True
    except Exception:
        return False
    return False


def run_action(node: ActionNode, state: dict[str, Any], out_dir: Path, automation_title: str,
               test_mode: bool, lang: str = "en") -> tuple[str, Any, str]:
    """Returns (status, output, message). status: ok | simulated."""
    summary = state.get("_summary") or "\n".join(
        f"{k}: {v}" for k, v in state.items() if not k.startswith("_") and k != "trigger"
    )
    body = render_template(node.template, state, summary)
    stamp = state.get("_stamp", "run")
    name = slug(node.label, 40)
    if node.action.simulated:
        key = "run.simulated_email" if node.action == ActionType.email else "run.simulated_erp"
        return "simulated", {"target": node.target, "body": body[:2000]}, t(
            key, lang, target=node.target or t("run.not_configured", lang))
    if node.action == ActionType.log:
        log.info("automation log node=%s", node.id)
        return "ok", body[:2000], t("run.logged", lang)
    if node.action == ActionType.notify:
        if test_mode:
            return "simulated", body[:500], t("run.notify_test", lang, target=node.target or t("run.the_manager", lang))
        shown = notify_desktop(automation_title, body)
        return "ok", body[:500], t("run.notify_desktop" if shown else "run.notify_logged", lang)
    folder = Path(node.target).expanduser() if (node.target and not test_mode and Path(node.target).expanduser().is_absolute()) else out_dir
    folder.mkdir(parents=True, exist_ok=True)
    if node.action == ActionType.write_file:
        p = folder / f"{name}-{stamp}.md"
        p.write_text(f"# {automation_title}\n\n{body}\n", "utf-8")
        return "ok", str(p), t("run.file_created", lang, name=p.name)
    if node.action == ActionType.pdf_report:
        p = write_pdf(folder / f"{name}-{stamp}.pdf", automation_title, body)
        return "ok", str(p), t("run.pdf_created", lang, name=p.name)
    if node.action == ActionType.write_xlsx:
        rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}
                for k, v in state.items() if not k.startswith("_") and k != "trigger"]
        p = folder / f"{name}-{stamp}.xlsx"
        pd.DataFrame(rows or [{"key": "result", "value": body}]).to_excel(p, index=False)
        return "ok", str(p), t("run.excel_created", lang, name=p.name)
    return "ok", None, t("run.no_action", lang)
