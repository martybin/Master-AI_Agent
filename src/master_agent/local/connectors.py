"""Telegram Bot API, personal-account sessions and authenticated HTTP connections."""

import asyncio
import time
from typing import Literal

import httpx
from pydantic import Field, SecretStr
from telethon import TelegramClient, errors, utils
from telethon.sessions import StringSession

from master_agent.clients import IntegrationError
from master_agent.ir import ClosedModel, Identifier
from master_agent.local.store import LocalStore
from master_agent.repository import StateConflict, now


class ConnectionInput(ClosedModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["telegram_bot", "telegram_user", "http"]
    token: SecretStr = SecretStr("")
    api_id: int | None = Field(default=None, gt=0)
    api_hash: SecretStr = SecretStr("")
    base_url: str = ""
    header_name: str = Field(default="Authorization", pattern=r"^[A-Za-z0-9-]{1,60}$")


class Connectors:
    def __init__(self, store: LocalStore, client: httpx.AsyncClient, factory=TelegramClient):
        self.store, self.http, self.factory = store, client, factory
        self.clients: dict = {}
        self.pending: dict = {}
        self.locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def public(record: dict):
        return {k: v for k, v in record.items() if k != "sealed"}

    def list(self):
        return [self.public(record) for record in self.store.list("connections")]

    def create(self, data: ConnectionInput):
        if any(record["id"] == data.id for record in self.list()):
            raise StateConflict("Connection id already exists; use a different id")
        if data.kind == "telegram_bot" and not data.token.get_secret_value():
            raise StateConflict("A BotFather token is required")
        if data.kind == "telegram_user" and (
            not data.api_id or not data.api_hash.get_secret_value()
        ):
            raise StateConflict("Telegram api_id and api_hash are required")
        if data.kind == "http":
            try:
                url = httpx.URL(data.base_url)
            except httpx.InvalidURL:
                raise StateConflict("Invalid HTTP base URL") from None
            if (
                url.scheme not in {"http", "https"}
                or not url.host
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise StateConflict("HTTP connection needs a base URL without credentials or query")
        record = {
            "id": data.id,
            "label": data.label,
            "kind": data.kind,
            "status": "configured",
            "base_url": data.base_url.rstrip("/"),
            "header_name": data.header_name,
            "created_at": now(),
            "sealed": self.store.seal(
                {
                    "token": data.token.get_secret_value(),
                    "api_id": data.api_id,
                    "api_hash": data.api_hash.get_secret_value(),
                }
            ),
        }
        self.store.put("connections", data.id, record)
        self.store.audit("connection.create", "complete", connection=data.id, kind=data.kind)
        return self.public(record)

    async def bot(self, alias: str, method: str, data: dict | None = None, request_timeout=20):
        record = self.store.get("connections", alias)
        if record["kind"] != "telegram_bot":
            raise StateConflict("This connection is not a Telegram bot")
        token = self.store.unseal(record["sealed"])["token"]
        try:
            response = await self.http.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=data or {},
                timeout=request_timeout,
            )
            result = response.json()
            if not response.is_success or result.get("ok") is not True:
                raise ValueError("Bot API refused request")
            return result["result"]
        except (httpx.HTTPError, ValueError, KeyError):
            raise IntegrationError(
                "telegram",
                "bot_request_failed",
                "Telegram bot request failed; check token/network or delivery status",
            ) from None

    async def verify(self, alias: str):
        record = self.store.get("connections", alias)
        if record["kind"] == "telegram_bot":
            identity = await self.bot(alias, "getMe")
            record["identity"] = {"id": identity["id"], "username": identity.get("username")}
        elif record["kind"] == "telegram_user":
            client = await self.user(alias)
            identity = await client.get_me()
            record["identity"] = {"id": identity.id, "username": identity.username}
        else:
            response = await self.http_request(alias, "GET", "/")
            record["identity"] = {"http_status": response["status"]}
        # A login may have refreshed the encrypted session while the check awaited I/O.
        record = {**self.store.get("connections", alias), "identity": record["identity"]}
        record["status"] = "connected"
        self.store.put("connections", alias, record)
        return self.public(record)

    async def user(self, alias: str):
        async with self.locks.setdefault(alias, asyncio.Lock()):
            return await self._user(alias)

    async def _user(self, alias: str):
        record = self.store.get("connections", alias)
        if record["kind"] != "telegram_user":
            raise StateConflict("This connection is not a personal Telegram account")
        if alias not in self.clients:
            secret = self.store.unseal(record["sealed"])
            client = self.factory(
                StringSession(secret.get("session", "")),
                secret["api_id"],
                secret["api_hash"],
                request_retries=0,
                flood_sleep_threshold=0,
            )
            try:
                await client.connect()
            except BaseException:
                await client.disconnect()
                raise
            self.clients[alias] = client
        client = self.clients[alias]
        if not await client.is_user_authorized():
            raise StateConflict("Complete personal-account sign-in in Connections first")
        return client

    async def begin_login(self, alias: str, phone: str):
        await self.expire_logins()
        async with self.locks.setdefault(alias, asyncio.Lock()):
            return await self._begin_login(alias, phone)

    async def _begin_login(self, alias: str, phone: str):
        record = self.store.get("connections", alias)
        if record["kind"] != "telegram_user":
            raise StateConflict("Only personal accounts use this login flow")
        if alias in self.pending:
            raise StateConflict("A sign-in is already pending; finish it or cancel it first")
        secret = self.store.unseal(record["sealed"])
        client = self.factory(
            StringSession(secret.get("session", "")),
            secret["api_id"],
            secret["api_hash"],
            request_retries=0,
            flood_sleep_threshold=0,
        )
        try:
            await client.connect()
            if await client.is_user_authorized():
                await self._finish_login(alias, client)
                return {"state": "authorized"}
            sent = await client.send_code_request(phone)
            self.pending[alias] = {
                "client": client,
                "phone": phone,
                "hash": sent.phone_code_hash,
                "expires": time.monotonic() + 600,
                "state": "code_required",
            }
            return {"state": "code_required", "expires_in": 600}
        except asyncio.CancelledError:
            await client.disconnect()
            raise
        except Exception:
            await client.disconnect()
            raise IntegrationError(
                "telegram",
                "login_failed",
                "Cannot start Telegram sign-in; check API credentials and connectivity",
            ) from None

    async def complete_login(self, alias: str, code: str = "", password: str = ""):
        await self.expire_logins()
        async with self.locks.setdefault(alias, asyncio.Lock()):
            pending = self.pending.get(alias)
            if not pending:
                raise StateConflict("No pending login or the code has expired")
            try:
                if pending["state"] == "password_required":
                    if not password:
                        return {"state": "password_required"}
                    await pending["client"].sign_in(password=password)
                else:
                    await pending["client"].sign_in(
                        phone=pending["phone"], code=code, phone_code_hash=pending["hash"]
                    )
                if not await pending["client"].is_user_authorized():
                    raise StateConflict("Telegram did not authorize this session")
                await self._finish_login(alias, pending["client"])
                self.pending.pop(alias, None)
                return {"state": "authorized"}
            except errors.SessionPasswordNeededError:
                pending["state"] = "password_required"
                return {"state": "password_required"}
            except StateConflict:
                raise
            except Exception:
                raise IntegrationError(
                    "telegram", "login_failed", "Telegram refused the login code or password"
                ) from None

    async def _finish_login(self, alias: str, client):
        record = self.store.get("connections", alias)
        secret = self.store.unseal(record["sealed"])
        secret["session"] = client.session.save()
        record.update(sealed=self.store.seal(secret), status="connected")
        self.store.put("connections", alias, record)
        if alias in self.clients:
            await self.clients[alias].disconnect()
        self.clients[alias] = client
        self.store.audit("telegram.login", "complete", connection=alias)

    async def expire_logins(self):
        for alias in list(self.pending):
            async with self.locks.setdefault(alias, asyncio.Lock()):
                pending = self.pending.get(alias)
                if pending and pending["expires"] < time.monotonic():
                    self.pending.pop(alias)
                    await pending["client"].disconnect()

    async def cancel_login(self, alias: str):
        async with self.locks.setdefault(alias, asyncio.Lock()):
            pending = self.pending.pop(alias, None)
            if pending:
                await pending["client"].disconnect()
        return {"state": "cancelled"}

    async def dialogs(self, alias: str):
        client = await self.user(alias)
        return [
            {"id": utils.get_peer_id(dialog.entity), "title": dialog.name}
            for dialog in await client.get_dialogs(limit=150)
        ]

    async def send(self, alias: str, chat_id: int, text: str):
        record = self.store.get("connections", alias)
        if not text or len(text) > 4096:
            raise StateConflict("Telegram reply must contain 1-4096 characters")
        if record["kind"] == "telegram_bot":
            result = await self.bot(alias, "sendMessage", {"chat_id": chat_id, "text": text})
            return {"message_id": result["message_id"]}
        client = await self.user(alias)
        await client.get_dialogs(limit=150)
        message = await client.send_message(chat_id, text, parse_mode=None)
        return {"message_id": message.id}

    async def http_request(self, alias: str, method: str, path: str, body=None):
        record = self.store.get("connections", alias)
        if record["kind"] != "http":
            raise StateConflict("Expected an HTTP connection")
        url = httpx.URL(record["base_url"])
        target = url.join(path)
        if target.host != url.host or target.port != url.port or target.scheme != url.scheme:
            raise StateConflict("Connection credentials cannot be sent to a different origin")
        secret = self.store.unseal(record["sealed"])
        headers = {record["header_name"]: secret["token"]} if secret.get("token") else {}
        try:
            response = await self.http.request(
                method, str(target), headers=headers, json=body, timeout=30
            )
            if method not in {"GET", "HEAD"} and response.status_code >= 500:
                raise IntegrationError(
                    "http", "mutation_unknown", "Remote API mutation outcome is unknown"
                )
            try:
                data = response.json()
            except ValueError:
                data = response.text[:24000]
            return self.store.redact({"status": response.status_code, "body": data})
        except httpx.HTTPError:
            raise IntegrationError(
                "http",
                "request_failed" if method in {"GET", "HEAD"} else "mutation_unknown",
                "HTTP connection request failed; check status before repeating a mutation",
            ) from None

    async def close(self):
        for pending in self.pending.values():
            await pending["client"].disconnect()
        for client in self.clients.values():
            await client.disconnect()
        self.pending.clear()
        self.clients.clear()
