from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import src.db as db_module
from src import worker as worker_module
from src.clients import feishu_client as feishu_module
from src.clients.feishu_client import (
    append_note_marker,
    feishu_group_message_scope_missing,
    feishu_message_marker_is_newer,
    message_matches_confirm_keywords,
)
from src.clients.github_client import get_installation_token, list_open_issues
from src.utils.helpers import ensure_dir, newer_utc_timestamp, now_utc, shift_utc_timestamp, short_text


ACTIVE_JOB_STATUSES = ("queued", "running")
STATE_LOCK = threading.Lock()
DISPATCH_THREAD: threading.Thread | None = None
POLLING_THREAD: threading.Thread | None = None


@dataclass
class SchedulerContext:
    config: dict[str, Any]
    runtime: dict[str, Any]
    record_issue_trigger: Callable[[dict[str, Any], str], dict[str, Any]]
    get_issue_session: Callable[[str, int], sqlite3.Row | None]
    issue_has_active_job: Callable[[str, int], bool]
    upsert_feishu_binding: Callable[..., sqlite3.Row]
    upsert_issue_session: Callable[..., sqlite3.Row]
    reply_issue_discussion_to_feishu: Callable[..., str | None]
    confirm_feishu_binding_and_queue: Callable[[dict[str, Any], sqlite3.Row, dict[str, Any]], tuple[str, bool]]


def session_allows_feishu_followup(session_state: str | None) -> bool:
    normalized = str(session_state or "").strip().lower()
    # failed keeps the same issue/thread context, so a later `/run`
    # in the same thread should be allowed to queue a retry.
    return normalized in {"waiting_confirm", "bound", "failed"}


def default_state() -> dict[str, Any]:
    return {
        "processed_triggers": {},
        "poll_cache": {
            "repos": {},
            "issues": {},
        },
    }


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    processed = state.get("processed_triggers")
    if not isinstance(processed, dict):
        state["processed_triggers"] = {}

    poll_cache = state.get("poll_cache")
    if not isinstance(poll_cache, dict):
        state["poll_cache"] = {}
        poll_cache = state["poll_cache"]

    repos = poll_cache.get("repos")
    if not isinstance(repos, dict):
        poll_cache["repos"] = {}

    issues = poll_cache.get("issues")
    if not isinstance(issues, dict):
        poll_cache["issues"] = {}

    return state


def poll_cache_repo_state(state: dict[str, Any], repo_full_name: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    poll_cache = normalized["poll_cache"]
    repos = poll_cache["repos"]
    repo_state = repos.get(repo_full_name)
    if not isinstance(repo_state, dict):
        repo_state = {}
        repos[repo_full_name] = repo_state
    return repo_state


def poll_cache_issue_key(repo_full_name: str, issue_number: int) -> str:
    return f"{repo_full_name}#{issue_number}"


def state_set(target: dict[str, Any], key: str, value: Any) -> bool:
    current = target.get(key)
    if current == value:
        return False
    if value is None:
        if key not in target:
            return False
        target.pop(key, None)
        return True
    target[key] = value
    return True


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    path = Path(config["state_file"])
    ensure_dir(path.parent)
    with STATE_LOCK:
        if not path.exists():
            return default_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default_state()
    if not isinstance(data, dict):
        return default_state()
    return normalize_state(data)


def save_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    path = Path(config["state_file"])
    ensure_dir(path.parent)
    normalized = normalize_state(state)
    processed = normalized["processed_triggers"]
    if len(processed) > 2000:
        trimmed = sorted(processed.items(), key=lambda item: item[1])[-1000:]
        normalized["processed_triggers"] = dict(trimmed)
    tmp_path = path.with_suffix(".tmp")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with STATE_LOCK:
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)


def remove_issue_from_state(config: dict[str, Any], repo_full_name: str, issue_number: int) -> None:
    prefix = f"{repo_full_name}#{issue_number}:"
    state = load_state(config)
    processed = state.setdefault("processed_triggers", {})
    keys = [key for key in processed if key.startswith(prefix)]
    issue_cache_key = poll_cache_issue_key(repo_full_name, issue_number)
    issue_cache = state.setdefault("poll_cache", {}).setdefault("issues", {})
    removed = False
    if not keys:
        if issue_cache.pop(issue_cache_key, None) is None:
            return
        save_state(config, state)
        return
    for key in keys:
        processed.pop(key, None)
        removed = True
    if issue_cache.pop(issue_cache_key, None) is not None:
        removed = True
    if not removed:
        return
    save_state(config, state)


def trigger_key(repo_full_name: str, issue_number: int, kind: str, value: str) -> str:
    return f"{repo_full_name}#{issue_number}:{kind}:{value}"


def build_payload(
    repo_full_name: str,
    issue: dict[str, Any],
    *,
    action: str,
    label_name: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "repository": {"full_name": repo_full_name},
        "issue": issue,
    }
    if label_name is not None:
        payload["label"] = {"name": label_name}
    return payload


def repo_has_running_job(config: dict[str, Any], repo_full_name: str) -> bool:
    row = db_module.fetchone(
        config,
        "SELECT job_id FROM jobs WHERE repo_full_name = ? AND status = 'running' LIMIT 1",
        (repo_full_name,),
    )
    return row is not None


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def queue_payload(context: SchedulerContext, payload: dict[str, Any], reason: str) -> str:
    trigger_info = context.record_issue_trigger(payload, reason)
    action = "created" if trigger_info["session_state"] == "waiting_confirm" else "updated"
    print(
        f"{action} handoff for "
        f"{trigger_info['repo_full_name']}#{trigger_info['issue_number']} "
        f"(session={trigger_info['session_key']} state={trigger_info['session_state']})"
    )
    return str(trigger_info["session_key"])


def recover_inflight_jobs(context: SchedulerContext, *, source: str = "service startup") -> None:
    running_jobs = db_module.fetchall(
        context.config,
        "SELECT job_id, worker_pid, repo_full_name, issue_number FROM jobs WHERE status = 'running'",
    )
    for row in running_jobs:
        if pid_is_alive(row["worker_pid"]):
            continue
        repo_full_name = str(row["repo_full_name"])
        issue_number = int(row["issue_number"])
        worker_module.release_active_lock(context.config, repo_full_name, issue_number)
        worker_module.release_repo_lock(context.config, repo_full_name)
        db_module.requeue_job(
            context.config,
            str(row["job_id"]),
            f"worker process missing; re-queued on {source}",
        )


def scan_waiting_feishu_confirmations(context: SchedulerContext) -> None:
    for binding in db_module.fetch_waiting_feishu_bindings(context.config):
        repo_full_name = str(binding["repo_full_name"])
        issue_number = int(binding["issue_number"])
        session_row = context.get_issue_session(repo_full_name, issue_number)
        if session_row is None:
            continue
        if not session_allows_feishu_followup(str(session_row["session_state"] or "")):
            continue
        if context.issue_has_active_job(repo_full_name, issue_number):
            continue

        thread_id = str(binding["thread_id"] or "").strip()
        if not thread_id:
            continue
        try:
            messages = feishu_module.feishu_list_thread_messages(
                context.config,
                context.runtime,
                thread_id,
                context.config["feishu_thread_scan_limit"],
            )
        except Exception as exc:
            error_summary = short_text(str(exc), 1500)
            print(
                f"warning: failed to scan Feishu thread for "
                f"{repo_full_name}#{issue_number}: {error_summary}"
            )
            if feishu_group_message_scope_missing(exc):
                warning_marker = "warning:missing-im-message-group-msg-scope"
                note = str(binding["note"] or "")
                warning_text = (
                    "当前飞书应用缺少 `im:message.group_msg` 权限，"
                    "暂时无法读取群线程消息，因此不会响应讨论消息或 `/run`。\n\n"
                    "请在飞书开放平台为该应用开通这个权限后，再回到这个线程重试。"
                )
                root_message_id = (
                    str(binding["root_message_id"] or "").strip()
                    or str(binding["prompt_message_id"] or "").strip()
                )
                if warning_marker not in note and root_message_id:
                    try:
                        feishu_module.feishu_reply_in_thread(
                            context.config,
                            context.runtime,
                            root_message_id,
                            warning_text,
                        )
                    except Exception as reply_exc:
                        print(
                            f"warning: failed to post Feishu permission warning for "
                            f"{repo_full_name}#{issue_number}: {short_text(str(reply_exc), 800)}"
                        )
                if warning_marker not in note:
                    context.upsert_feishu_binding(
                        context.config,
                        chat_id=str(binding["chat_id"] or "").strip(),
                        thread_id=thread_id,
                        repo_full_name=repo_full_name,
                        issue_number=issue_number,
                        session_key=str(binding["session_key"] or "").strip(),
                        binding_state=str(binding["binding_state"] or "waiting_confirm"),
                        note=append_note_marker(note, warning_marker),
                        root_message_id=str(binding["root_message_id"] or "").strip() or None,
                        prompt_message_id=str(binding["prompt_message_id"] or "").strip() or None,
                        last_seen_message_id=str(binding["last_seen_message_id"] or "").strip() or None,
                        last_seen_message_time=str(binding["last_seen_message_time"] or "").strip() or None,
                        confirm_message_id=str(binding["confirm_message_id"] or "").strip() or None,
                        confirm_message_time=str(binding["confirm_message_time"] or "").strip() or None,
                    )
                    context.upsert_issue_session(
                        context.config,
                        repo_full_name,
                        issue_number,
                        summary=warning_text,
                        last_result_status="waiting_confirm",
                    )
            continue

        newest_seen_id = str(binding["last_seen_message_id"] or "").strip()
        newest_seen_time = str(binding["last_seen_message_time"] or "").strip()
        confirm_message: dict[str, Any] | None = None
        discussion_messages: list[dict[str, Any]] = []

        for message in messages:
            if not feishu_message_marker_is_newer(
                message,
                str(binding["last_seen_message_time"] or ""),
                str(binding["last_seen_message_id"] or ""),
            ):
                continue
            newest_seen_id = str(message["message_id"])
            newest_seen_time = str(message["create_time"])
            if str(message.get("sender_type") or "").strip().lower() != "user":
                continue
            if message_matches_confirm_keywords(
                str(message.get("content") or ""),
                context.config["feishu_confirm_keywords"],
            ):
                confirm_message = message
                break
            discussion_messages.append(message)

        if discussion_messages:
            for message in discussion_messages:
                try:
                    visible_messages = []
                    for item in messages:
                        visible_messages.append(item)
                        if str(item.get("message_id") or "") == str(message.get("message_id") or ""):
                            break
                    context.reply_issue_discussion_to_feishu(
                        context.config,
                        repo_full_name,
                        issue_number,
                        binding=binding,
                        recent_messages=visible_messages,
                    )
                except Exception as exc:
                    error_summary = short_text(str(exc), 1500)
                    print(
                        f"warning: failed to proxy Feishu discussion for "
                        f"{repo_full_name}#{issue_number}: {error_summary}"
                    )
                    root_message_id = (
                        str(binding["root_message_id"] or "").strip()
                        or str(binding["prompt_message_id"] or "").strip()
                    )
                    if root_message_id:
                        try:
                            feishu_module.feishu_reply_in_thread(
                                context.config,
                                context.runtime,
                                root_message_id,
                                f"讨论阶段回复失败，请稍后重试。\n\n错误摘要：{error_summary}",
                            )
                        except Exception as reply_exc:
                            print(
                                f"warning: failed to post discussion error reply for "
                                f"{repo_full_name}#{issue_number}: {reply_exc}"
                            )

        if confirm_message is None:
            if newest_seen_id != str(binding["last_seen_message_id"] or "").strip() or newest_seen_time != str(
                binding["last_seen_message_time"] or ""
            ).strip():
                context.upsert_feishu_binding(
                    context.config,
                    chat_id=str(binding["chat_id"]),
                    thread_id=thread_id,
                    repo_full_name=repo_full_name,
                    issue_number=issue_number,
                    session_key=str(binding["session_key"]),
                    note=str(binding["note"] or "") or None,
                    binding_state=str(binding["binding_state"] or "waiting_confirm"),
                    root_message_id=str(binding["root_message_id"] or "") or None,
                    prompt_message_id=str(binding["prompt_message_id"] or "") or None,
                    last_seen_message_id=newest_seen_id or None,
                    last_seen_message_time=newest_seen_time or None,
                    confirm_message_id=str(binding["confirm_message_id"] or "") or None,
                    confirm_message_time=str(binding["confirm_message_time"] or "") or None,
                )
            continue

        job_id, created = context.confirm_feishu_binding_and_queue(context.config, binding, confirm_message)
        context.runtime["last_queued_job_id"] = job_id
        action = "queued" if created else "reused"
        print(
            f"{action} job {job_id} for "
            f"{repo_full_name}#{issue_number} "
            f"(session={session_row['session_key']} state=queued)"
        )


def dispatch_queued_jobs(context: SchedulerContext) -> None:
    queued_jobs = db_module.fetchall(
        context.config,
        "SELECT job_id, repo_full_name, job_dir FROM jobs WHERE status = 'queued' ORDER BY created_at ASC",
    )
    repo_started: set[str] = set()
    for row in queued_jobs:
        repo_full_name = str(row["repo_full_name"])
        if repo_full_name in repo_started:
            continue
        if repo_has_running_job(context.config, repo_full_name):
            continue
        if worker_module.repo_lock_path(context.config, repo_full_name).exists():
            continue
        job_id = str(row["job_id"])
        job_dir = Path(str(row["job_dir"]))
        pid = worker_module.spawn_worker(context.config, job_id, job_dir)
        db_module.mark_job_running(context.config, job_id, pid)
        context.runtime["last_dispatched_job_id"] = job_id
        repo_started.add(repo_full_name)


def dispatch_loop(context: SchedulerContext) -> None:
    interval = max(2, context.config["dispatch_interval_seconds"])
    while True:
        try:
            recover_inflight_jobs(context, source="dispatch loop")
            scan_waiting_feishu_confirmations(context)
            dispatch_queued_jobs(context)
        except Exception as exc:
            print(f"dispatch error: {exc}")
        time.sleep(interval)


def start_dispatch_thread(context: SchedulerContext) -> None:
    global DISPATCH_THREAD
    if DISPATCH_THREAD and DISPATCH_THREAD.is_alive():
        return
    DISPATCH_THREAD = threading.Thread(
        target=dispatch_loop,
        args=(context,),
        name="job-dispatcher",
        daemon=True,
    )
    DISPATCH_THREAD.start()


def detect_poll_trigger(
    context: SchedulerContext,
    repo_full_name: str,
    issue: dict[str, Any],
    state: dict[str, Any],
) -> tuple[tuple[dict[str, Any], str, str] | None, bool, bool]:
    issue_number = int(issue["number"])
    processed = state.setdefault("processed_triggers", {})
    if worker_module.active_lock_path(context.config, repo_full_name, issue_number).exists():
        return None, False, True
    if context.issue_has_active_job(repo_full_name, issue_number):
        return None, False, True

    label_names = {label.get("name", "") for label in issue.get("labels", [])}
    if context.config["trigger_label"] and context.config["trigger_label"] in label_names:
        key = trigger_key(repo_full_name, issue_number, "label", context.config["trigger_label"])
        if key not in processed:
            payload = build_payload(
                repo_full_name,
                issue,
                action="labeled",
                label_name=context.config["trigger_label"],
            )
            return (payload, f"poll.issues_labeled:{context.config['trigger_label']}", key), False, False

    if context.config["run_on_issue_opened"]:
        key = trigger_key(repo_full_name, issue_number, "opened", "issue")
        if key not in processed:
            payload = build_payload(repo_full_name, issue, action="opened")
            return (payload, "poll.issues_opened", key), False, False

    return None, False, False


def poll_once(context: SchedulerContext) -> None:
    if not context.config["poll_enabled"] or not context.config["allowed_repos"]:
        return

    context.runtime["last_poll_started_at"] = now_utc()
    context.runtime["last_poll_error"] = None
    state = load_state(context.config)
    state_changed = False
    token = get_installation_token(context.config)

    for repo_full_name in context.config["allowed_repos"]:
        owner, repo = repo_full_name.split("/", 1)
        repo_state = poll_cache_repo_state(state, repo_full_name)

        use_incremental = not bool(repo_state.get("force_full_scan"))
        issues_since = None
        issues_etag = None
        issues_etag_key = ""
        if use_incremental:
            issues_since = shift_utc_timestamp(
                str(repo_state.get("last_issue_updated_at") or "") or None,
                seconds=-1,
            )
            issues_etag_key = issues_since or ""
            if str(repo_state.get("issues_etag_key") or "") == issues_etag_key:
                issues_etag = str(repo_state.get("issues_etag") or "") or None

        issues, latest_etag, not_modified = list_open_issues(
            context.config,
            token,
            owner,
            repo,
            since=issues_since,
            etag=issues_etag,
        )
        if not_modified:
            continue

        if use_incremental:
            state_changed |= state_set(repo_state, "issues_etag_key", issues_etag_key)
            state_changed |= state_set(repo_state, "issues_etag", latest_etag)

        repo_latest_updated_at = str(repo_state.get("last_issue_updated_at") or "")
        repo_needs_full_scan = False
        for issue in issues:
            if issue.get("pull_request"):
                continue
            decision, issue_state_changed, issue_requests_rescan = detect_poll_trigger(
                context,
                repo_full_name,
                issue,
                state,
            )
            state_changed |= issue_state_changed
            if issue_requests_rescan:
                repo_needs_full_scan = True
            else:
                repo_latest_updated_at = newer_utc_timestamp(
                    repo_latest_updated_at,
                    str(issue.get("updated_at") or ""),
                ) or repo_latest_updated_at
            if not decision:
                continue
            payload, reason, key = decision
            queue_payload(context, payload, reason)
            state.setdefault("processed_triggers", {})[key] = now_utc()
            state_changed = True

        if repo_needs_full_scan:
            state_changed |= state_set(repo_state, "force_full_scan", True)
            continue

        state_changed |= state_set(repo_state, "force_full_scan", False)
        if repo_latest_updated_at:
            state_changed |= state_set(repo_state, "last_issue_updated_at", repo_latest_updated_at)
        if not use_incremental:
            state_changed |= state_set(repo_state, "issues_etag_key", None)
            state_changed |= state_set(repo_state, "issues_etag", None)

    if state_changed:
        save_state(context.config, state)
    context.runtime["last_poll_completed_at"] = now_utc()


def poll_loop(context: SchedulerContext) -> None:
    interval = max(15, context.config["poll_interval_seconds"])
    print(f"polling enabled: every {interval}s for {context.config['allowed_repos']}")
    while True:
        try:
            poll_once(context)
        except Exception as exc:
            context.runtime["last_poll_error"] = short_text(str(exc), 1200)
            print(f"polling error: {exc}")
        time.sleep(interval)


def start_polling_thread(context: SchedulerContext) -> None:
    global POLLING_THREAD
    if not context.config["poll_enabled"]:
        print("polling disabled")
        return
    if POLLING_THREAD and POLLING_THREAD.is_alive():
        return
    POLLING_THREAD = threading.Thread(
        target=poll_loop,
        args=(context,),
        name="github-poller",
        daemon=True,
    )
    POLLING_THREAD.start()


def queue_stats(config: dict[str, Any]) -> dict[str, int]:
    rows = db_module.fetchall(config, "SELECT status, COUNT(*) AS total FROM jobs GROUP BY status")
    stats = {str(row["status"]): int(row["total"]) for row in rows}
    return {
        "queued": stats.get("queued", 0),
        "running": stats.get("running", 0),
        "succeeded": stats.get("succeeded", 0),
        "failed": stats.get("failed", 0),
        "no_change": stats.get("no_change", 0),
        "needs_human": stats.get("needs_human", 0),
        "cancelled": stats.get("cancelled", 0),
    }


__all__ = [
    "SchedulerContext",
    "default_state",
    "dispatch_loop",
    "dispatch_queued_jobs",
    "load_state",
    "poll_loop",
    "poll_once",
    "queue_stats",
    "recover_inflight_jobs",
    "remove_issue_from_state",
    "scan_waiting_feishu_confirmations",
    "save_state",
    "session_allows_feishu_followup",
    "start_dispatch_thread",
    "start_polling_thread",
]
