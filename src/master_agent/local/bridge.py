"""Durable Telegram -> local n8n webhook -> Telegram bridge. No public tunnel required."""

import asyncio
import re

from pydantic import Field

from master_agent.ir import ClosedModel
from master_agent.repository import StateConflict, now
from master_agent.service import verify_remote


class BindingInput(ClosedModel):
    build_id: str
    allowed_chat_ids: list[int] = Field(default_factory=list, max_length=200)
    reply_path: str | None = Field(
        default="message.content", pattern=r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){0,7}$"
    )


class Bridges:
    def __init__(self, store, connectors, n8n, settings, client):
        self.store, self.connectors, self.n8n, self.settings, self.http = (
            store,
            connectors,
            n8n,
            settings,
            client,
        )
        self.tasks = {}
        self.locks = {}
        self.starting = set()

    def bind(self, alias: str, data: BindingInput):
        connection = self.store.get("connections", alias)
        if connection["kind"] not in {"telegram_bot", "telegram_user"}:
            raise StateConflict("Only Telegram connections can receive this binding")
        if connection["kind"] == "telegram_user" and not data.allowed_chat_ids:
            raise StateConflict("Choose the personal-account chats to automate")
        if alias in self.tasks or alias in self.starting:
            raise StateConflict("Stop the listener before changing its binding")
        if data.reply_path and any(
            part in {"constructor", "prototype", "__proto__"} for part in data.reply_path.split(".")
        ):
            raise StateConflict("Unsafe response path")
        self.webhook(data.build_id)
        record = {
            "id": alias,
            **data.model_dump(),
            "enabled": False,
            "status": "stopped",
            "cursor": {},
            "created_at": now(),
        }
        self.store.put("bindings", alias, record)
        return record

    def webhook(self, build_id):
        record = self.store.repo.get(build_id)
        hooks = [
            node for node in record["workflow"]["nodes"] if node["type"] == "n8n-nodes-base.webhook"
        ]
        if len(hooks) != 1 or hooks[0]["parameters"].get("httpMethod", "GET") != "POST":
            raise StateConflict("Bridge requires exactly one POST webhook")
        path = hooks[0]["parameters"].get("path", "")
        if not re.fullmatch(r"[A-Za-z0-9_/-]{1,160}", path) or ".." in path:
            raise StateConflict("Unsafe local webhook path")
        return record, self.settings.n8n_base_url + "/webhook/" + path

    async def check_active(self, binding):
        record, url = self.webhook(binding["build_id"])
        if record["status"] != "active" or not record["n8n_workflow_id"]:
            raise StateConflict("Owner must deploy, review and activate the workflow first")
        remote = await self.n8n.get_workflow(record["n8n_workflow_id"])
        verify_remote(record, remote)
        if remote.get("active") is not True or (
            "activeVersionId" in remote and remote["activeVersionId"] != remote.get("versionId")
        ):
            raise StateConflict("Reviewed workflow is not the published active version")
        return url

    async def start(self, alias):
        async with self.locks.setdefault(alias, asyncio.Lock()):
            self.starting.add(alias)
            try:
                return await self._start(alias)
            finally:
                self.starting.discard(alias)

    async def _start(self, alias):
        if alias in self.tasks:
            return self.store.get("bindings", alias)
        binding = self.store.get("bindings", alias)
        await self.check_active(binding)
        connection = self.store.get("connections", alias)
        if connection["kind"] == "telegram_bot":
            info = await self.connectors.bot(alias, "getWebhookInfo")
            if info.get("url"):
                raise StateConflict(
                    "Bot already has a webhook; remove it explicitly before enabling polling"
                )
        else:
            await self.connectors.user(alias)
        binding.update(enabled=True, status="running", last_error=None)
        self.store.put("bindings", alias, binding)
        task = asyncio.create_task(self.loop(alias))
        self.tasks[alias] = task
        task.add_done_callback(lambda done: self.tasks.pop(alias, None))
        self.store.audit("bridge.start", "complete", connection=alias, build_id=binding["build_id"])
        return binding

    async def stop(self, alias):
        async with self.locks.setdefault(alias, asyncio.Lock()):
            return await self._stop(alias)

    async def _stop(self, alias):
        task = self.tasks.pop(alias, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        binding = self.store.get("bindings", alias)
        binding.update(enabled=False, status="stopped")
        self.store.put("bindings", alias, binding)
        self.store.audit("bridge.stop", "complete", connection=alias)
        return binding

    async def poll(self, alias, binding):
        connection = self.store.get("connections", alias)
        cursor = dict(binding["cursor"])
        events = []
        if connection["kind"] == "telegram_bot":
            updates = await self.connectors.bot(
                alias,
                "getUpdates",
                {"offset": cursor.get("offset", 0), "timeout": 20, "allowed_updates": ["message"]},
                request_timeout=30,
            )
            for update in sorted(updates, key=lambda item: item["update_id"]):
                message = update.get("message", {})
                events.append(
                    (
                        str(update["update_id"]),
                        message.get("chat", {}).get("id"),
                        message.get("text"),
                        message.get("from", {}).get("id"),
                    )
                )
                cursor["offset"] = max(cursor.get("offset", 0), update["update_id"] + 1)
        else:
            client = await self.connectors.user(alias)
            await client.get_dialogs(limit=150)
            for chat_id in binding["allowed_chat_ids"]:
                key = str(chat_id)
                if key not in cursor:
                    recent = await client.get_messages(chat_id, limit=1)
                    cursor[key] = recent[0].id if recent else 0
                    continue
                async for message in client.iter_messages(
                    chat_id, min_id=cursor[key], reverse=True, limit=50
                ):
                    cursor[key] = max(cursor[key], message.id)
                    if not message.out and message.raw_text:
                        events.append(
                            (
                                f"{chat_id}:{message.id}",
                                chat_id,
                                message.raw_text,
                                message.sender_id,
                            )
                        )
        # Capture before advancing the remote offset, including ignored updates.
        captured = []
        for event_key, chat_id, text, sender_id in events:
            if (
                not chat_id
                or not text
                or binding["allowed_chat_ids"]
                and chat_id not in binding["allowed_chat_ids"]
            ):
                continue
            record = self.store.capture(
                alias,
                event_key,
                {
                    "message": text,
                    "chat_id": chat_id,
                    "sender_id": sender_id,
                    "connection": alias,
                    "event_id": event_key,
                },
                build_id=binding["build_id"],
            )
            if record:
                captured.append(record)
        binding["cursor"] = cursor
        self.store.put("bindings", alias, binding)
        return captured

    async def deliver(self, record, allow_uncertain=False):
        record = self.store.claim_delivery(record["id"], allow_uncertain=allow_uncertain)
        if not record:
            return
        binding = self.store.get("bindings", record["connection"])
        payload = self.store.unseal(record["sealed"])
        phase = "preflight"
        try:
            if record.get("build_id") and record["build_id"] != binding["build_id"]:
                raise StateConflict("Delivery belongs to a different workflow binding")
            url = await self.check_active(binding)
            cached = record.get("sealed_reply")
            if not cached:
                phase = "workflow"
                record = self.store.update_delivery(record, "delivering", phase=phase)
                response = await self.http.post(
                    url, json=payload, timeout=self.settings.ollama_timeout_seconds + 30
                )
                response.raise_for_status()
            if binding["reply_path"]:
                if cached:
                    result = self.store.unseal(cached)["text"]
                else:
                    result = response.json()
                    for key in binding["reply_path"].split("."):
                        result = result[key]
                if not isinstance(result, str) or not result:
                    raise ValueError("Workflow did not return the configured reply text")
                phase = "send"
                record = self.store.update_delivery(
                    record,
                    "delivering",
                    phase=phase,
                    sealed_reply=self.store.seal({"text": result[:4096]}),
                )
                # Destination comes from the captured event, never from model output.
                await self.connectors.send(record["connection"], payload["chat_id"], result[:4096])
            self.store.update_delivery(record, "complete")
        except asyncio.CancelledError:
            self.store.update_delivery(
                record, "pending" if phase == "preflight" else "uncertain", phase=phase
            )
            raise
        except Exception:
            self.store.update_delivery(
                record, "pending" if phase == "preflight" else "uncertain", phase=phase
            )
            self.store.audit(
                "bridge.deliver",
                "needs_attention",
                connection=record["connection"],
                event=record["event_key"],
                phase=phase,
            )
            # Side effects may already have happened. Never retry a webhook/send blindly.

    async def loop(self, alias):
        while True:
            try:
                binding = self.store.get("bindings", alias)
                await self.poll(alias, binding)
                for record in self.store.pending(alias):
                    await self.deliver(record)
                binding = self.store.get("bindings", alias)
                binding.update(status="running", last_error=None, last_poll_at=now())
                self.store.put("bindings", alias, binding)
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                binding = self.store.get("bindings", alias)
                binding.update(status="waiting", last_error="connection_or_workflow_unavailable")
                self.store.put("bindings", alias, binding)
                await asyncio.sleep(5)

    async def resume(self):
        for binding in self.store.list("bindings"):
            if binding.get("enabled"):
                try:
                    await self.start(binding["id"])
                except Exception:
                    binding.update(status="needs_attention", last_error="restart_preflight_failed")
                    self.store.put("bindings", binding["id"], binding)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
