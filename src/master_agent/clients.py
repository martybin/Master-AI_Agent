"""Bounded HTTP clients. No hidden execution, activation, downloads or mutation retries."""

from typing import Any
from urllib.parse import quote

import httpx

from master_agent.compiler import create_payload
from master_agent.config import Settings
from master_agent.ir import WorkflowIR


class IntegrationError(RuntimeError):
    def __init__(self, service: str, code: str, message: str):
        self.service, self.code = service, code
        super().__init__(message)


class OllamaClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def models(self) -> list[str]:
        try:
            response = await self.client.get(self.settings.ollama_base_url + "/api/tags", timeout=5)
            response.raise_for_status()
            return [item["name"] for item in response.json()["models"]]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise IntegrationError(
                "ollama", "unavailable", "Cannot read Ollama model inventory"
            ) from exc

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None = None,
        num_predict: int | None = None,
    ) -> str:
        try:
            response = await self.client.post(
                self.settings.ollama_base_url + "/api/chat",
                json={
                    "model": self.settings.ollama_model,
                    "messages": messages,
                    "format": schema or WorkflowIR.model_json_schema(),
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0,
                        "seed": 42,
                        "num_ctx": self.settings.ollama_num_ctx,
                        "num_predict": num_predict or self.settings.ollama_num_predict,
                    },
                },
                timeout=self.settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            content = result["message"]["content"]
            if not isinstance(content, str) or not content or len(content) > 100_000:
                raise ValueError("Missing or oversized response")
            return content
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise IntegrationError(
                "ollama",
                "generation_failed",
                "Ollama generation failed; check model, service and timeout",
            ) from exc


class N8nClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        key = self.settings.n8n_api_key.get_secret_value()
        if not key:
            raise IntegrationError("n8n", "not_configured", "Set N8N_API_KEY before contacting n8n")
        try:
            response = await self.client.request(
                method,
                self.settings.n8n_base_url + "/api/v1" + path,
                headers={"X-N8N-API-KEY": key},
                timeout=20,
                **kwargs,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except httpx.HTTPStatusError as exc:
            # Never return response bodies: they may contain credentials or prompts.
            raise IntegrationError(
                "n8n",
                f"http_{exc.response.status_code}",
                f"n8n returned HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationError(
                "n8n", "request_failed", "n8n request failed; mutation outcome may be unknown"
            ) from exc

    async def create_workflow(self, workflow: dict) -> dict:
        if workflow.get("active") is not False:
            raise ValueError("Only inactive compiled workflows may be created")
        result = await self._request("POST", "/workflows", json=create_payload(workflow))
        if (
            not isinstance(result.get("id"), str)
            or not result["id"]
            or result.get("active") is not False
            or result.get("activeVersionId") is not None
        ):
            raise IntegrationError(
                "n8n", "unexpected_create", "n8n did not confirm an inactive workflow"
            )
        return result

    async def get_workflow(self, workflow_id: str) -> dict:
        return await self._request("GET", "/workflows/" + quote(workflow_id, safe=""))

    async def list_workflows(self, cursor: str | None = None) -> dict:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", "/workflows", params=params)

    async def activate(self, workflow_id: str, *, confirmed: bool, version_id: str) -> dict:
        if confirmed is not True:
            raise ValueError("Explicit human confirmation is required")
        return await self._request(
            "POST",
            "/workflows/" + quote(workflow_id, safe="") + "/activate",
            json={"versionId": version_id},
        )

    async def deactivate(self, workflow_id: str) -> dict:
        return await self._request(
            "POST", "/workflows/" + quote(workflow_id, safe="") + "/deactivate"
        )

    async def executions(self, workflow_id: str) -> dict:
        return await self._request(
            "GET",
            "/executions",
            params={"workflowId": workflow_id, "includeData": "false", "limit": 20},
        )
