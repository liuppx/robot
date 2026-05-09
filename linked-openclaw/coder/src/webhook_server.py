from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import Flask, jsonify, request

from src import worker as worker_module
from src.clients.openclaw_client import openclaw_issue_workspace_dir
from src.issue_service import IssueService
from src.utils.helpers import backend_label, backend_model_label, now_utc


@dataclass
class WebhookContext:
    config: dict[str, Any]
    runtime: dict[str, Any]
    issue_service: IssueService
    validate_signature: Callable[[str, bytes, str | None], bool]
    repo_allowed: Callable[[str], bool]
    webhook_decision: Callable[[dict[str, Any], str], tuple[bool, str]]
    queue_payload: Callable[[dict[str, Any], str], str]
    queue_stats: Callable[[], dict[str, int]]


def create_app(context_provider: Callable[[], WebhookContext]) -> Flask:
    app = Flask(__name__)

    def current_context() -> WebhookContext:
        return context_provider()

    @app.get("/")
    def root() -> Any:
        context = current_context()
        return jsonify(
            {
                "service": "coder-issue-bot",
                "status": "ok",
                "time": now_utc(),
                "endpoint": "/github/webhook",
                "poll_enabled": context.config.get("poll_enabled"),
            }
        )

    @app.get("/health")
    def health() -> Any:
        context = current_context()
        return jsonify(
            {
                "status": "ok",
                "time": now_utc(),
                "app_home": context.config.get("app_home"),
                "data_dir": context.config.get("data_dir"),
                "openclaw_config_path": context.config.get("openclaw_config_path"),
                "openclaw_runtime_config_path": context.config.get("openclaw_runtime_config_path"),
                "webhook_enabled": context.config.get("webhook_enabled"),
                "allowed_repos": context.config.get("allowed_repos"),
                "execution_backend": context.config.get("execution_backend"),
                "backend_label": backend_label(context.config),
                "backend_model": backend_model_label(context.config),
                "run_on_issue_opened": context.config.get("run_on_issue_opened"),
                "trigger_label": context.config.get("trigger_label"),
                "poll_enabled": context.config.get("poll_enabled"),
                "poll_interval_seconds": context.config.get("poll_interval_seconds"),
                "dispatch_interval_seconds": context.config.get("dispatch_interval_seconds"),
                "submit_comment_after_pr": context.config.get("submit_comment_after_pr"),
                "submit_comment_body": context.config.get("submit_comment_body"),
                "last_poll_started_at": context.runtime.get("last_poll_started_at"),
                "last_poll_completed_at": context.runtime.get("last_poll_completed_at"),
                "last_poll_error": context.runtime.get("last_poll_error"),
                "last_queued_job_id": context.runtime.get("last_queued_job_id"),
                "last_dispatched_job_id": context.runtime.get("last_dispatched_job_id"),
                "queue": context.queue_stats(),
            }
        )

    @app.get("/issues/<owner>/<repo>/<int:issue_number>/session")
    def issue_session_view(owner: str, repo: str, issue_number: int) -> Any:
        context = current_context()
        repo_full_name = f"{owner}/{repo}"
        return jsonify(context.issue_service.issue_session_payload(repo_full_name, issue_number))

    @app.post("/feishu/bind")
    def feishu_bind() -> Any:
        context = current_context()
        payload = request.get_json(silent=True) or {}
        repo_full_name = str(payload.get("repo_full_name") or "").strip()
        if not repo_full_name:
            owner = str(payload.get("owner") or "").strip()
            repo = str(payload.get("repo") or "").strip()
            if owner and repo:
                repo_full_name = f"{owner}/{repo}"
        issue_number_raw = payload.get("issue_number")
        chat_id = str(payload.get("chat_id") or "").strip()
        thread_id = str(payload.get("thread_id") or "").strip()
        if not repo_full_name or issue_number_raw is None or not chat_id or not thread_id:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "repo_full_name/issue_number/chat_id/thread_id are required",
                    }
                ),
                400,
            )
        if not context.repo_allowed(repo_full_name):
            return jsonify({"ok": False, "error": "repo not allowed", "repo": repo_full_name}), 403

        try:
            issue_number = int(issue_number_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "issue_number must be an integer"}), 400

        worker_module.ensure_openclaw_issue_agent(
            context.config,
            repo_full_name,
            issue_number,
            openclaw_issue_workspace_dir(context.config, repo_full_name, issue_number),
        )
        context.issue_service.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="waiting_confirm",
            last_result_status="waiting_confirm",
        )
        binding = context.issue_service.upsert_feishu_binding(
            chat_id=chat_id,
            thread_id=thread_id,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            note=str(payload.get("note") or "").strip() or None,
            binding_state=str(payload.get("binding_state") or "waiting_confirm").strip() or "waiting_confirm",
        )
        return jsonify(
            {
                "ok": True,
                "binding": dict(binding),
                "session": context.issue_service.issue_session_payload(repo_full_name, issue_number),
            }
        )

    @app.get("/feishu/bindings/<chat_id>/<thread_id>")
    def feishu_binding_view(chat_id: str, thread_id: str) -> Any:
        context = current_context()
        binding = context.issue_service.get_feishu_binding(chat_id, thread_id)
        if binding is None:
            return jsonify({"ok": False, "error": "binding not found"}), 404
        repo_full_name = str(binding["repo_full_name"])
        issue_number = int(binding["issue_number"])
        return jsonify(
            {
                "ok": True,
                "binding": dict(binding),
                "session": context.issue_service.issue_session_payload(repo_full_name, issue_number),
            }
        )

    @app.delete("/feishu/bindings/<chat_id>/<thread_id>")
    def feishu_binding_delete(chat_id: str, thread_id: str) -> Any:
        context = current_context()
        binding = context.issue_service.get_feishu_binding(chat_id, thread_id)
        if binding is None:
            return jsonify({"ok": False, "error": "binding not found"}), 404
        repo_full_name = str(binding["repo_full_name"])
        issue_number = int(binding["issue_number"])
        if context.config.get("execution_backend") == "openclaw":
            try:
                context.issue_service.remove_openclaw_feishu_route_bindings(
                    repo_full_name,
                    issue_number,
                    bindings=[binding],
                )
            except Exception as exc:
                return jsonify({"ok": False, "error": f"failed to remove OpenClaw binding: {exc}"}), 500
        deleted = context.issue_service.delete_feishu_binding(chat_id, thread_id)
        remaining = context.issue_service.list_issue_bindings(repo_full_name, issue_number)
        session_row = context.issue_service.get_issue_session(repo_full_name, issue_number)
        if session_row and not remaining and str(session_row["session_state"]) in {"bound", "waiting_confirm"}:
            context.issue_service.upsert_issue_session(
                repo_full_name,
                issue_number,
                session_state="triggered",
                last_result_status="triggered",
            )
        return jsonify(
            {
                "ok": deleted,
                "repo_full_name": repo_full_name,
                "issue_number": issue_number,
                "binding_removed": {"chat_id": chat_id, "thread_id": thread_id},
                "session": context.issue_service.issue_session_payload(repo_full_name, issue_number),
            }
        )

    @app.post("/issues/<owner>/<repo>/<int:issue_number>/session/state")
    def issue_session_state_update(owner: str, repo: str, issue_number: int) -> Any:
        context = current_context()
        payload = request.get_json(silent=True) or {}
        repo_full_name = f"{owner}/{repo}"
        if not context.repo_allowed(repo_full_name):
            return jsonify({"ok": False, "error": "repo not allowed", "repo": repo_full_name}), 403
        session_row = context.issue_service.get_issue_session(repo_full_name, issue_number)
        if session_row is None:
            context.issue_service.ensure_issue_session(repo_full_name, issue_number, f"Issue {issue_number}")
        handoff_prompt = payload.get("handoff_prompt")
        summary = payload.get("summary")
        context.issue_service.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state=str(payload.get("session_state") or "").strip() or None,
            handoff_prompt=None if handoff_prompt is None else str(handoff_prompt),
            agent_session_id=str(payload.get("agent_session_id") or "").strip() or None,
            pr_url=str(payload.get("pr_url") or "").strip() or None,
            summary=None if summary is None else str(summary),
            last_result_status=str(payload.get("last_result_status") or "").strip() or None,
        )
        return jsonify({"ok": True, "session": context.issue_service.issue_session_payload(repo_full_name, issue_number)})

    @app.post("/github/webhook")
    def github_webhook() -> Any:
        context = current_context()
        if not context.config.get("webhook_enabled", True):
            return jsonify({"ok": False, "error": "webhook disabled"}), 404

        raw_body = request.get_data()
        signature = request.headers.get("X-Hub-Signature-256")
        if not context.validate_signature(context.config["webhook_secret"], raw_body, signature):
            return jsonify({"ok": False, "error": "invalid signature"}), 401

        event_name = request.headers.get("X-GitHub-Event", "")
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if delivery_id and not context.issue_service.record_delivery_once(delivery_id, event_name):
            return jsonify({"ok": True, "ignored": "duplicate delivery", "delivery_id": delivery_id}), 200

        payload = request.get_json(silent=True) or {}
        if event_name == "ping":
            return jsonify({"ok": True, "event": "ping"}), 200

        repo_full_name = payload.get("repository", {}).get("full_name", "")
        issue = payload.get("issue") or {}
        issue_number = issue.get("number")
        if not repo_full_name or issue_number is None:
            return jsonify({"ok": True, "ignored": "missing repository or issue"}), 200
        if not context.repo_allowed(repo_full_name):
            return jsonify({"ok": True, "ignored": "repo not allowed", "repo": repo_full_name}), 200

        if event_name == "issues" and payload.get("action") == "closed":
            context.issue_service.handle_issue_closed(repo_full_name, issue)
            return jsonify({"ok": True, "handled": "issue closed"}), 200

        if event_name == "issues" and payload.get("action") == "reopened":
            context.issue_service.upsert_issue_record(
                repo_full_name,
                int(issue_number),
                issue.get("title") or f"Issue #{issue_number}",
                "open",
            )

        should_run, reason = context.webhook_decision(payload, event_name)
        if not should_run:
            return jsonify({"ok": True, "ignored": reason}), 200

        trigger_id = context.queue_payload(payload, reason)
        return jsonify({"ok": True, "triggered": True, "trigger_id": trigger_id, "reason": reason}), 202

    return app


__all__ = ["WebhookContext", "create_app"]
