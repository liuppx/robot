from __future__ import annotations

import json
import sqlite3
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import src.db as db_module
from src import worker as worker_module
from src.clients import feishu_client as feishu_module
from src.clients.feishu_client import (
    build_feishu_handoff_intro,
    build_feishu_thread_peer_id,
    build_feishu_thread_session_key,
    is_feishu_route_session_key,
    resolve_feishu_runtime_settings,
)
from src.clients.github_client import (
    comment_issue,
    get_installation_token,
    github_request,
)
from src.clients.openclaw_client import (
    load_openclaw_config_json,
    openclaw_issue_workspace_dir,
    save_openclaw_config_json,
)
from src.utils.helpers import (
    now_utc,
    service_actor_name,
    short_text,
    slugify,
)


ACTIVE_JOB_STATUSES = {"queued", "running"}


@dataclass
class IssueService:
    config: dict[str, Any]
    runtime: dict[str, Any]

    def feishu_get_message(self, message_id: str) -> dict[str, Any]:
        return feishu_module.feishu_get_message(self.config, self.runtime, message_id)

    def feishu_send_text_message(self, chat_id: str, text: str) -> str:
        return feishu_module.feishu_send_text_message(self.config, self.runtime, chat_id, text)

    def feishu_reply_in_thread(self, root_message_id: str, text: str) -> str:
        return feishu_module.feishu_reply_in_thread(self.config, self.runtime, root_message_id, text)

    def feishu_list_thread_messages(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        return feishu_module.feishu_list_thread_messages(self.config, self.runtime, thread_id, limit)

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return db_module.fetchone(self.config, query, params)

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return db_module.fetchall(self.config, query, params)

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        db_module.execute(self.config, query, params)

    def record_delivery_once(self, delivery_id: str, event_name: str) -> bool:
        return db_module.record_delivery_once(self.config, delivery_id, event_name)

    def upsert_issue_record(
        self,
        repo_full_name: str,
        issue_number: int,
        issue_title: str,
        issue_state: str,
        *,
        active_job_id: str | None = None,
        last_reason: str | None = None,
    ) -> None:
        db_module.upsert_issue_record(
            self.config,
            repo_full_name,
            issue_number,
            issue_title,
            issue_state,
            active_job_id=active_job_id,
            last_reason=last_reason,
        )

    def clear_issue_active_job(self, repo_full_name: str, issue_number: int, job_id: str) -> None:
        db_module.clear_issue_active_job(self.config, repo_full_name, issue_number, job_id)

    def get_existing_active_job(self, repo_full_name: str, issue_number: int) -> sqlite3.Row | None:
        return db_module.get_existing_active_job(
            self.config,
            repo_full_name,
            issue_number,
            tuple(ACTIVE_JOB_STATUSES),
        )

    def issue_has_active_job(self, repo_full_name: str, issue_number: int) -> bool:
        return self.get_existing_active_job(repo_full_name, issue_number) is not None

    def build_issue_session_key(self, repo_full_name: str, issue_number: int) -> str:
        prefix = slugify(self.config.get("openclaw_session_prefix", "gh"), limit=16)
        repo_slug = slugify(repo_full_name.replace("/", "-"), limit=48)
        if prefix:
            return f"{prefix}-{repo_slug}-issue-{issue_number}"
        return f"{repo_slug}-issue-{issue_number}"

    def normalize_issue_session_key(
        self,
        repo_full_name: str,
        issue_number: int,
        session_key: str | None,
    ) -> str:
        expected = self.build_issue_session_key(repo_full_name, issue_number)
        candidate = str(session_key or "").strip()
        if not candidate or is_feishu_route_session_key(candidate):
            return expected
        return candidate

    def build_issue_branch_name(self, issue_number: int, issue_title: str) -> str:
        prefix = slugify(self.config.get("issue_branch_prefix", "coder"), limit=16) or "coder"
        title_slug = slugify(issue_title or "task", limit=32)
        return f"{prefix}/issue-{issue_number}-{title_slug}"

    def build_issue_agent_id(self, repo_full_name: str, issue_number: int) -> str:
        return worker_module.build_issue_agent_id(self.config, repo_full_name, issue_number)

    def get_issue_session(self, repo_full_name: str, issue_number: int) -> sqlite3.Row | None:
        return db_module.get_issue_session(self.config, repo_full_name, issue_number)

    def upsert_issue_session(
        self,
        repo_full_name: str,
        issue_number: int,
        *,
        backend: str | None = None,
        session_key: str | None = None,
        session_state: str | None = None,
        last_trigger_reason: str | None = None,
        last_triggered_at: str | None = None,
        handoff_prompt: str | None = None,
        agent_session_id: str | None = None,
        branch_name: str | None = None,
        pr_url: str | None = None,
        summary: str | None = None,
        last_result_status: str | None = None,
    ) -> sqlite3.Row:
        existing = self.get_issue_session(repo_full_name, issue_number)
        created_at = str(existing["created_at"]) if existing else now_utc()
        final_session_key = self.normalize_issue_session_key(
            repo_full_name,
            issue_number,
            session_key
            if session_key is not None
            else (str(existing["session_key"]) if existing and existing["session_key"] else None),
        )
        record = {
            "backend": backend or (str(existing["backend"]) if existing else self.config["execution_backend"]),
            "session_key": final_session_key,
            "session_state": session_state
            or (str(existing["session_state"]) if existing and existing["session_state"] else "triggered"),
            "last_trigger_reason": (
                last_trigger_reason
                if last_trigger_reason is not None
                else (str(existing["last_trigger_reason"]) if existing and existing["last_trigger_reason"] else None)
            ),
            "last_triggered_at": (
                last_triggered_at
                if last_triggered_at is not None
                else (str(existing["last_triggered_at"]) if existing and existing["last_triggered_at"] else None)
            ),
            "handoff_prompt": (
                short_text(handoff_prompt, 20000)
                if handoff_prompt is not None
                else (str(existing["handoff_prompt"]) if existing and existing["handoff_prompt"] else None)
            ),
            "agent_session_id": (
                agent_session_id
                if agent_session_id is not None
                else (str(existing["agent_session_id"]) if existing and existing["agent_session_id"] else None)
            ),
            "branch_name": branch_name
            or (
                str(existing["branch_name"])
                if existing
                else self.build_issue_branch_name(issue_number, f"Issue {issue_number}")
            ),
            "pr_url": pr_url if pr_url is not None else (str(existing["pr_url"]) if existing and existing["pr_url"] else None),
            "summary": (
                short_text(summary, 12000)
                if summary is not None
                else (str(existing["summary"]) if existing and existing["summary"] else None)
            ),
            "last_result_status": (
                last_result_status
                if last_result_status is not None
                else (str(existing["last_result_status"]) if existing and existing["last_result_status"] else None)
            ),
        }
        updated_at = now_utc()
        db_module.upsert_issue_session(
            self.config,
            repo_full_name,
            issue_number,
            backend=str(record["backend"]),
            session_key=str(record["session_key"]),
            session_state=str(record["session_state"]),
            last_trigger_reason=record["last_trigger_reason"],
            last_triggered_at=record["last_triggered_at"],
            handoff_prompt=record["handoff_prompt"],
            agent_session_id=record["agent_session_id"],
            branch_name=str(record["branch_name"]),
            pr_url=record["pr_url"],
            summary=record["summary"],
            last_result_status=record["last_result_status"],
            created_at=created_at,
            updated_at=updated_at,
        )
        session = self.get_issue_session(repo_full_name, issue_number)
        if session is None:
            raise RuntimeError(f"failed to load issue session for {repo_full_name}#{issue_number}")
        return session

    def ensure_issue_session(
        self,
        repo_full_name: str,
        issue_number: int,
        issue_title: str,
    ) -> sqlite3.Row:
        existing = self.get_issue_session(repo_full_name, issue_number)
        branch_name = str(existing["branch_name"]) if existing and existing["branch_name"] else self.build_issue_branch_name(
            issue_number,
            issue_title,
        )
        session_key = self.normalize_issue_session_key(
            repo_full_name,
            issue_number,
            str(existing["session_key"]) if existing and existing["session_key"] else None,
        )
        return self.upsert_issue_session(
            repo_full_name,
            issue_number,
            backend=self.config["execution_backend"],
            session_key=session_key,
            session_state=(
                str(existing["session_state"])
                if existing and existing["session_state"]
                else "triggered"
            ),
            branch_name=branch_name,
        )

    def get_feishu_binding(self, chat_id: str, thread_id: str) -> sqlite3.Row | None:
        return db_module.get_feishu_binding(self.config, chat_id, thread_id)

    def list_issue_bindings(self, repo_full_name: str, issue_number: int) -> list[sqlite3.Row]:
        return db_module.list_issue_bindings(self.config, repo_full_name, issue_number)

    def upsert_feishu_binding(
        self,
        *,
        chat_id: str,
        thread_id: str,
        repo_full_name: str,
        issue_number: int,
        session_key: str | None = None,
        note: str | None = None,
        binding_state: str = "bound",
        root_message_id: str | None = None,
        prompt_message_id: str | None = None,
        last_seen_message_id: str | None = None,
        last_seen_message_time: str | None = None,
        confirm_message_id: str | None = None,
        confirm_message_time: str | None = None,
    ) -> sqlite3.Row:
        if self.get_issue_session(repo_full_name, issue_number) is None:
            self.ensure_issue_session(repo_full_name, issue_number, f"Issue {issue_number}")
        existing = self.get_feishu_binding(chat_id, thread_id)
        now = now_utc()
        final_session_key = (
            session_key
            or (str(existing["session_key"]) if existing and existing["session_key"] else "")
            or build_feishu_thread_session_key(self.config, repo_full_name, issue_number, chat_id, thread_id)
        )
        payload = {
            "note": note if note is not None else (str(existing["note"]) if existing and existing["note"] else None),
            "root_message_id": (
                root_message_id
                if root_message_id is not None
                else (str(existing["root_message_id"]) if existing and existing["root_message_id"] else None)
            ),
            "prompt_message_id": (
                prompt_message_id
                if prompt_message_id is not None
                else (str(existing["prompt_message_id"]) if existing and existing["prompt_message_id"] else None)
            ),
            "last_seen_message_id": (
                last_seen_message_id
                if last_seen_message_id is not None
                else (str(existing["last_seen_message_id"]) if existing and existing["last_seen_message_id"] else None)
            ),
            "last_seen_message_time": (
                last_seen_message_time
                if last_seen_message_time is not None
                else (str(existing["last_seen_message_time"]) if existing and existing["last_seen_message_time"] else None)
            ),
            "confirm_message_id": (
                confirm_message_id
                if confirm_message_id is not None
                else (str(existing["confirm_message_id"]) if existing and existing["confirm_message_id"] else None)
            ),
            "confirm_message_time": (
                confirm_message_time
                if confirm_message_time is not None
                else (str(existing["confirm_message_time"]) if existing and existing["confirm_message_time"] else None)
            ),
        }
        db_module.upsert_feishu_binding(
            self.config,
            chat_id=chat_id,
            thread_id=thread_id,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            session_key=final_session_key,
            binding_state=binding_state,
            note=payload["note"],
            root_message_id=payload["root_message_id"],
            prompt_message_id=payload["prompt_message_id"],
            last_seen_message_id=payload["last_seen_message_id"],
            last_seen_message_time=payload["last_seen_message_time"],
            confirm_message_id=payload["confirm_message_id"],
            confirm_message_time=payload["confirm_message_time"],
            created_at=now,
            updated_at=now,
        )
        binding = self.get_feishu_binding(chat_id, thread_id)
        if binding is None:
            raise RuntimeError(f"failed to load Feishu binding for {chat_id}:{thread_id}")
        return binding

    def delete_feishu_binding(self, chat_id: str, thread_id: str) -> bool:
        return db_module.delete_feishu_binding(self.config, chat_id, thread_id)

    def preferred_issue_binding(
        self,
        repo_full_name: str,
        issue_number: int,
        *,
        chat_id: str | None = None,
    ) -> sqlite3.Row | None:
        bindings = self.list_issue_bindings(repo_full_name, issue_number)
        if not bindings:
            return None
        if chat_id:
            for binding in bindings:
                if str(binding["chat_id"]) == chat_id:
                    return binding
        return bindings[0]

    def remove_openclaw_feishu_route_bindings(
        self,
        repo_full_name: str,
        issue_number: int,
        bindings: list[sqlite3.Row] | None = None,
    ) -> None:
        issue_bindings = bindings or self.list_issue_bindings(repo_full_name, issue_number)
        if not issue_bindings:
            return

        peer_ids = {
            build_feishu_thread_peer_id(str(row["chat_id"]), str(row["thread_id"]))
            for row in issue_bindings
        }
        agent_id = self.build_issue_agent_id(repo_full_name, issue_number)
        payload = load_openclaw_config_json(self.config)
        existing = payload.get("bindings")
        if not isinstance(existing, list):
            return

        next_bindings: list[dict[str, Any]] = []
        changed = False
        for raw_binding in existing:
            if not isinstance(raw_binding, dict):
                next_bindings.append(raw_binding)
                continue
            match = raw_binding.get("match") or {}
            peer = match.get("peer") or {}
            should_remove = (
                raw_binding.get("type", "route") == "route"
                and str(raw_binding.get("agentId") or "") == agent_id
                and str(match.get("channel") or "") == "feishu"
                and str(peer.get("kind") or "") == "group"
                and str(peer.get("id") or "").lower() in peer_ids
            )
            if should_remove:
                changed = True
                continue
            next_bindings.append(raw_binding)

        if changed:
            payload["bindings"] = next_bindings or None
            save_openclaw_config_json(self.config, payload)

    def build_handoff_prompt(
        self,
        repo_full_name: str,
        issue: dict[str, Any],
        session_key: str | None = None,
    ) -> str:
        issue_body = short_text(issue.get("body") or "(no issue body)", 6000)
        issue_title = issue.get("title") or f"Issue #{issue['number']}"
        lines = [
            f"你正在继续处理 GitHub Issue #{issue['number']}。",
            f"仓库：{repo_full_name}",
            f"标题：{issue_title}",
        ]
        if session_key:
            lines.append(f"会话标识：{session_key}")
        lines.extend(
            [
                "Issue 正文：",
                issue_body,
            ]
        )
        return "\n".join(lines).strip()

    def build_feishu_discussion_prompt(
        self,
        repo_full_name: str,
        issue_number: int,
        issue_title: str,
        handoff_prompt: str,
        recent_messages: list[dict[str, Any]],
    ) -> str:
        transcript_lines: list[str] = []
        for message in recent_messages[-8:]:
            content = short_text(str(message.get("content") or "").strip(), 1000)
            if not content:
                continue
            sender_type = str(message.get("sender_type") or "").strip().lower()
            sender = "用户" if sender_type == "user" else "助手"
            transcript_lines.append(f"{sender}: {content}")

        lines = [
            f"你正在继续处理 GitHub Issue #{issue_number} 的飞书讨论阶段。",
            f"仓库：{repo_full_name}",
            f"标题：{issue_title}",
            "",
            "当前要求：",
            "- 现在只讨论方案、边界、改动计划和风险。",
            "- 不要开始 coding，不要假装已经执行。",
            "- 不要输出 `result:` / `summary:` / `tests:` / `risks:` 模板。",
            "- 回复直接发给飞书用户，保持简洁明确。",
            "- 如果用户还没有明确发送 `/run`，不要把讨论当成执行确认。",
            "",
            "交接背景：",
            short_text(handoff_prompt, 5000),
            "",
            "线程最近消息：",
            "\n".join(transcript_lines) if transcript_lines else "(无)",
            "",
            "请只输出你要发回飞书线程的正文。",
        ]
        return "\n".join(lines).strip()

    def ensure_issue_handoff_binding(
        self,
        repo_full_name: str,
        issue_number: int,
        issue: dict[str, Any],
        handoff_prompt: str,
    ) -> tuple[sqlite3.Row, bool]:
        settings = resolve_feishu_runtime_settings(self.config)
        chat_id = settings["chat_id"]
        existing_binding = self.preferred_issue_binding(repo_full_name, issue_number, chat_id=chat_id)
        created_new = existing_binding is None

        if existing_binding is None:
            root_message_id = self.feishu_send_text_message(
                chat_id,
                build_feishu_handoff_intro(repo_full_name, issue),
            )
            prompt_message_id = self.feishu_reply_in_thread(root_message_id, handoff_prompt)
            prompt_message = self.feishu_get_message(prompt_message_id)
            thread_id = str(prompt_message.get("thread_id") or "").strip()
            if not thread_id:
                root_message = self.feishu_get_message(root_message_id)
                thread_id = str(root_message.get("thread_id") or "").strip()
            if not thread_id:
                raise RuntimeError("Feishu thread_id is missing after creating handoff thread")
            last_seen_message_id = prompt_message_id
            last_seen_message_time = str(prompt_message.get("create_time") or "")
        else:
            root_message_id = (
                str(existing_binding["root_message_id"] or "").strip()
                or str(existing_binding["prompt_message_id"] or "").strip()
            )
            if not root_message_id:
                raise RuntimeError("Feishu binding exists but root_message_id is missing")
            thread_id = str(existing_binding["thread_id"] or "").strip()
            if not thread_id:
                raise RuntimeError("Feishu binding exists but thread_id is missing")
            prompt_message_id = self.feishu_reply_in_thread(root_message_id, handoff_prompt)
            prompt_message = self.feishu_get_message(prompt_message_id)
            refreshed_thread_id = str(prompt_message.get("thread_id") or "").strip()
            if refreshed_thread_id:
                thread_id = refreshed_thread_id
            last_seen_message_id = prompt_message_id
            last_seen_message_time = str(prompt_message.get("create_time") or "")

        route_session_key = build_feishu_thread_session_key(
            self.config,
            repo_full_name,
            issue_number,
            chat_id,
            thread_id,
        )
        binding = self.upsert_feishu_binding(
            chat_id=chat_id,
            thread_id=thread_id,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            session_key=route_session_key,
            note="auto handoff thread",
            binding_state="waiting_confirm",
            root_message_id=root_message_id,
            prompt_message_id=prompt_message_id,
            last_seen_message_id=last_seen_message_id,
            last_seen_message_time=last_seen_message_time,
            confirm_message_id="",
            confirm_message_time="",
        )
        return binding, created_new

    def build_issue_payload_from_github(self, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        owner, repo = repo_full_name.split("/", 1)
        token = get_installation_token(self.config)
        issue = github_request(
            self.config,
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            token=token,
        ).json()
        return {
            "action": "feishu_confirmed",
            "repository": {"full_name": repo_full_name},
            "issue": issue,
        }

    def record_issue_trigger(self, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        repo_full_name = payload["repository"]["full_name"]
        issue = payload["issue"]
        issue_number = int(issue["number"])
        issue_title = issue.get("title") or f"Issue #{issue_number}"
        issue_state = issue.get("state") or "open"
        owner, repo = repo_full_name.split("/", 1)

        self.upsert_issue_record(
            repo_full_name,
            issue_number,
            issue_title,
            issue_state,
            last_reason=reason,
        )
        worker_module.ensure_openclaw_issue_agent(
            self.config,
            repo_full_name,
            issue_number,
            openclaw_issue_workspace_dir(self.config, repo_full_name, issue_number),
        )
        session_row = self.ensure_issue_session(repo_full_name, issue_number, issue_title)
        local_session_key = str(session_row["session_key"])
        handoff_prompt = self.build_handoff_prompt(repo_full_name, issue, local_session_key)
        binding, created_new = self.ensure_issue_handoff_binding(
            repo_full_name,
            issue_number,
            issue,
            handoff_prompt,
        )
        session_row = self.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="waiting_confirm",
            last_trigger_reason=reason,
            last_triggered_at=now_utc(),
            handoff_prompt=handoff_prompt,
            last_result_status="waiting_confirm",
        )

        token = get_installation_token(self.config)
        comment_issue(
            self.config,
            token,
            owner,
            repo,
            issue_number,
            textwrap.dedent(
                f"""
                {service_actor_name()} 已登记此 Issue，并已{"创建" if created_new else "复用"}飞书讨论线程。

                - Trigger: `{reason}`
                - Session: `{session_row['session_key']}`
                - State: `{session_row['session_state']}`
                - Feishu Chat: `{binding['chat_id']}`
                - Feishu Thread: `{binding['thread_id']}`
                - Confirm Keywords: `{", ".join(self.config['feishu_confirm_keywords'])}`

                请先在线程里讨论方案，确认后再在线程中发送 `/run`。
                """
            ).strip(),
        )
        return {
            "repo_full_name": repo_full_name,
            "issue_number": issue_number,
            "session_key": str(session_row["session_key"]),
            "session_state": str(session_row["session_state"]),
            "chat_id": str(binding["chat_id"]),
            "thread_id": str(binding["thread_id"]),
        }

    def create_job(self, payload: dict[str, Any], reason: str) -> tuple[str, Path, bool]:
        repo_full_name = payload["repository"]["full_name"]
        issue = payload["issue"]
        issue_number = int(issue["number"])
        issue_title = issue.get("title") or f"Issue #{issue_number}"
        issue_state = issue.get("state") or "open"

        existing = self.get_existing_active_job(repo_full_name, issue_number)
        if existing:
            return str(existing["job_id"]), Path(str(existing["job_dir"])), False

        job_id = f"issue-{issue_number}-{int(time.time() * 1000)}"
        job_path = Path(self.config["job_root"]) / job_id
        job_path.mkdir(parents=True, exist_ok=True)
        job_data = {
            "job_id": job_id,
            "queued_at": now_utc(),
            "reason": reason,
            "payload": payload,
        }
        (job_path / "job.json").write_text(
            json.dumps(job_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        payload_json = json.dumps(payload, ensure_ascii=False)
        created_at = now_utc()
        db_module.insert_job(
            self.config,
            job_id=job_id,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            reason=reason,
            payload_json=payload_json,
            status="queued",
            created_at=created_at,
            job_dir=str(job_path),
        )
        db_module.upsert_issue_record(
            self.config,
            repo_full_name,
            issue_number,
            issue_title,
            issue_state,
            active_job_id=job_id,
            last_reason=reason,
            updated_at=created_at,
            closed_at=created_at if issue_state == "closed" else None,
        )
        return job_id, job_path, True

    def fetch_waiting_feishu_bindings(self) -> list[sqlite3.Row]:
        return db_module.fetch_waiting_feishu_bindings(self.config)

    def issue_payload_for_execution(self, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        return self.build_issue_payload_from_github(repo_full_name, issue_number)

    def confirm_feishu_binding_and_queue(
        self,
        binding: sqlite3.Row,
        confirm_message: dict[str, Any],
    ) -> tuple[str, bool]:
        repo_full_name = str(binding["repo_full_name"])
        issue_number = int(binding["issue_number"])
        issue_payload = self.issue_payload_for_execution(repo_full_name, issue_number)
        job_id, _, created = self.create_job(
            issue_payload,
            f"feishu.confirm:{binding['chat_id']}:{binding['thread_id']}",
        )
        issue = issue_payload["issue"]
        issue_title = issue.get("title") or f"Issue #{issue_number}"
        session_row = self.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="queued",
            last_trigger_reason=f"feishu.confirm:{binding['chat_id']}:{binding['thread_id']}",
            last_triggered_at=now_utc(),
            summary=str(confirm_message.get("content") or "").strip() or None,
            last_result_status="queued",
        )
        self.upsert_feishu_binding(
            chat_id=str(binding["chat_id"]),
            thread_id=str(binding["thread_id"]),
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            session_key=str(binding["session_key"]),
            note=str(binding["note"] or "") or None,
            binding_state="confirmed",
            root_message_id=str(binding["root_message_id"] or "") or None,
            prompt_message_id=str(binding["prompt_message_id"] or "") or None,
            last_seen_message_id=str(confirm_message["message_id"]),
            last_seen_message_time=str(confirm_message["create_time"]),
            confirm_message_id=str(confirm_message["message_id"]),
            confirm_message_time=str(confirm_message["create_time"]),
        )
        token = get_installation_token(self.config)
        owner, repo = repo_full_name.split("/", 1)
        comment_issue(
            self.config,
            token,
            owner,
            repo,
            issue_number,
            textwrap.dedent(
                f"""
                {service_actor_name()} 已收到飞书线程确认，开始排队执行。

                - Issue: `{repo_full_name}#{issue_number}`
                - Session: `{session_row['session_key']}`
                - Job: `{job_id}`
                - Title: `{issue_title}`
                """
            ).strip(),
        )
        if str(binding["root_message_id"] or "").strip():
            try:
                self.feishu_reply_in_thread(
                    str(binding["root_message_id"]),
                    textwrap.dedent(
                        f"""
                        已收到确认，开始执行。

                        - Issue: {repo_full_name}#{issue_number}
                        - Job: {job_id}
                        """
                    ).strip(),
                )
            except Exception as exc:
                print(f"warning: failed to reply in Feishu thread for {repo_full_name}#{issue_number}: {exc}")
        return job_id, created

    def reply_issue_execution_result_to_feishu(
        self,
        repo_full_name: str,
        issue_number: int,
        *,
        job_id: str,
        status: str,
        pr_url: str | None = None,
        result_summary: str | None = None,
        error_text: str | None = None,
    ) -> None:
        try:
            settings = resolve_feishu_runtime_settings(self.config)
            binding = self.preferred_issue_binding(repo_full_name, issue_number, chat_id=settings["chat_id"])
            if binding is None:
                print(f"warning: no Feishu binding found for {repo_full_name}#{issue_number}; skip result reply")
                return

            root_message_id = str(binding["root_message_id"] or "").strip() or str(binding["prompt_message_id"] or "").strip()
            if not root_message_id:
                print(
                    f"warning: Feishu binding missing root_message_id for "
                    f"{repo_full_name}#{issue_number}; skip result reply"
                )
                return

            if status == "succeeded":
                lines = [
                    f"{service_actor_name()} 已完成执行。",
                    f"- Issue: `{repo_full_name}#{issue_number}`",
                    f"- Job: `{job_id}`",
                ]
                if pr_url:
                    lines.append(f"- PR: `{pr_url}`")
                if result_summary:
                    lines.extend(["", short_text(result_summary, 3000)])
            elif status == "no_change":
                lines = [
                    f"{service_actor_name()} 已完成（无改动）。",
                    f"- Issue: `{repo_full_name}#{issue_number}`",
                    f"- Job: `{job_id}`",
                ]
                if result_summary:
                    lines.extend(["", short_text(result_summary, 3000)])
            else:
                summary = short_text(error_text or result_summary or "(no error text)", 3000)
                lines = [
                    f"{service_actor_name()} 执行失败。",
                    f"- Issue: `{repo_full_name}#{issue_number}`",
                    f"- Job: `{job_id}`",
                    "",
                    "错误摘要：",
                    summary,
                ]

            self.feishu_reply_in_thread(root_message_id, "\n".join(lines).strip())
        except Exception as exc:
            print(f"warning: failed to post Feishu result reply for {repo_full_name}#{issue_number}: {exc}")

    def reply_issue_discussion_to_feishu(
        self,
        repo_full_name: str,
        issue_number: int,
        *,
        binding: sqlite3.Row,
        recent_messages: list[dict[str, Any]],
    ) -> str | None:
        issue_row = self.fetchone(
            """
            SELECT issue_title
            FROM issues
            WHERE repo_full_name = ? AND issue_number = ?
            """,
            (repo_full_name, issue_number),
        )
        issue_title = str(issue_row["issue_title"]) if issue_row and issue_row["issue_title"] else f"Issue #{issue_number}"
        session_row = self.ensure_issue_session(repo_full_name, issue_number, issue_title)
        handoff_prompt = str(session_row["handoff_prompt"] or "").strip()
        if not handoff_prompt:
            return None

        prompt = self.build_feishu_discussion_prompt(
            repo_full_name,
            issue_number,
            issue_title,
            handoff_prompt,
            recent_messages,
        )
        turn = worker_module.run_openclaw_chat_turn(
            self.config,
            repo_full_name,
            issue_number,
            prompt,
            str(session_row["session_key"]),
        )
        response_text = str(turn["text"])
        agent_session_id = str(turn["agent_session_id"] or "").strip() or None

        root_message_id = str(binding["root_message_id"] or "").strip() or str(binding["prompt_message_id"] or "").strip()
        if not root_message_id:
            raise RuntimeError(f"Feishu binding missing root_message_id for {repo_full_name}#{issue_number}")
        self.feishu_reply_in_thread(root_message_id, response_text)
        self.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="bound",
            agent_session_id=agent_session_id,
            summary=response_text,
            last_result_status="waiting_confirm",
        )
        return agent_session_id

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return db_module.get_job(self.config, job_id)

    def mark_job_running(self, job_id: str, pid: int) -> None:
        db_module.mark_job_running(self.config, job_id, pid)

    def mark_job_finished(
        self,
        job_id: str,
        status: str,
        *,
        error_text: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        db_module.mark_job_finished(
            self.config,
            job_id,
            status,
            error_text=error_text,
            result_summary=result_summary,
        )

    def requeue_job(self, job_id: str, error_text: str | None = None) -> None:
        db_module.requeue_job(self.config, job_id, error_text)

    def cleanup_closed_issue_if_finished(self, repo_full_name: str, issue_number: int) -> None:
        issue_row = self.fetchone(
            "SELECT issue_state FROM issues WHERE repo_full_name = ? AND issue_number = ?",
            (repo_full_name, issue_number),
        )
        if not issue_row or issue_row["issue_state"] != "closed":
            return
        active = self.fetchone(
            "SELECT job_id FROM jobs WHERE repo_full_name = ? AND issue_number = ? AND status IN ('queued', 'running') LIMIT 1",
            (repo_full_name, issue_number),
        )
        if active:
            return
        self.execute(
            "DELETE FROM jobs WHERE repo_full_name = ? AND issue_number = ?",
            (repo_full_name, issue_number),
        )
        self.execute(
            "DELETE FROM issues WHERE repo_full_name = ? AND issue_number = ?",
            (repo_full_name, issue_number),
        )
        from src import scheduler as scheduler_module

        scheduler_module.remove_issue_from_state(self.config, repo_full_name, issue_number)
        issue_root = worker_module.repo_issue_root(self.config, repo_full_name, issue_number)
        if issue_root.exists():
            import shutil

            shutil.rmtree(issue_root, ignore_errors=True)
        bindings = self.list_issue_bindings(repo_full_name, issue_number)
        if self.config.get("execution_backend") == "openclaw":
            try:
                self.remove_openclaw_feishu_route_bindings(repo_full_name, issue_number, bindings=bindings)
                worker_module.delete_openclaw_issue_agent(self.config, repo_full_name, issue_number)
            except Exception as exc:
                print(
                    f"warning: failed to delete OpenClaw issue agent for "
                    f"{repo_full_name}#{issue_number}: {exc}"
                )
        self.execute(
            "DELETE FROM feishu_bindings WHERE repo_full_name = ? AND issue_number = ?",
            (repo_full_name, issue_number),
        )

    def handle_issue_closed(self, repo_full_name: str, issue: dict[str, Any]) -> None:
        issue_number = int(issue["number"])
        self.upsert_issue_record(
            repo_full_name,
            issue_number,
            issue.get("title") or f"Issue #{issue_number}",
            "closed",
        )
        self.upsert_issue_session(
            repo_full_name,
            issue_number,
            session_state="closed",
            last_result_status="closed",
        )
        self.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', finished_at = ?, error_text = 'issue closed before execution'
            WHERE repo_full_name = ? AND issue_number = ? AND status = 'queued'
            """,
            (now_utc(), repo_full_name, issue_number),
        )
        self.cleanup_closed_issue_if_finished(repo_full_name, issue_number)

    def issue_session_payload(self, repo_full_name: str, issue_number: int) -> dict[str, Any]:
        session_row = self.get_issue_session(repo_full_name, issue_number)
        issue_row = self.fetchone(
            """
            SELECT issue_title, issue_state, active_job_id, last_reason, updated_at, closed_at
            FROM issues
            WHERE repo_full_name = ? AND issue_number = ?
            """,
            (repo_full_name, issue_number),
        )
        bindings = self.list_issue_bindings(repo_full_name, issue_number)
        return {
            "repo_full_name": repo_full_name,
            "issue_number": issue_number,
            "issue": dict(issue_row) if issue_row else None,
            "session": dict(session_row) if session_row else None,
            "openclaw": {
                "agent_id": self.build_issue_agent_id(repo_full_name, issue_number),
                "workspace": str(openclaw_issue_workspace_dir(self.config, repo_full_name, issue_number)),
                "repo_path": str(worker_module.repo_checkout_dir(self.config, repo_full_name, issue_number)),
            },
            "bindings": [dict(row) for row in bindings],
            "binding_count": len(bindings),
        }


__all__ = ["IssueService"]
