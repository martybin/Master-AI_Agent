from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_assignment=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # The project .env is what the owner edits. A shell that still exports an
        # older OLLAMA_MODEL must not keep that stale value after the file changes.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    master_api_key: SecretStr = SecretStr("")
    database_path: Path = Path("data/master-agent.sqlite3")
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: float = Field(default=180, ge=1, le=600)
    ollama_num_ctx: int = Field(default=16384, ge=4096, le=32768)
    ollama_num_predict: int = Field(default=6000, ge=512, le=16384)
    planner_max_attempts: int = Field(default=3, ge=1, le=5)
    n8n_base_url: str = "http://127.0.0.1:5678"
    n8n_api_key: SecretStr = SecretStr("")
    n8n_ollama_base_url: str = "http://127.0.0.1:11434"
    http_allowed_hosts: list[str] = Field(default_factory=list)
    workflow_timezone: str = "Asia/Tehran"
    docs_enabled: bool = True
    knowledge_path: Path | None = None
    local_workspace: Path = Field(default_factory=Path.cwd)
    local_vault_key: Path | None = None
    local_max_steps: int = Field(default=10, ge=1, le=40)
    coding_max_steps: int = Field(default=20, ge=4, le=60)
    local_plugin_modules: list[str] = Field(default_factory=list)

    @field_validator("ollama_base_url", "n8n_base_url", "n8n_ollama_base_url")
    @classmethod
    def service_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Service URL must be HTTP(S), without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("workflow_timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return value

    @field_validator("http_allowed_hosts")
    @classmethod
    def exact_hosts(cls, hosts: list[str]) -> list[str]:
        import re

        if any(not re.fullmatch(r"[a-zA-Z0-9.-]+", host) for host in hosts):
            raise ValueError("Use exact hostnames only; no wildcard, port, path or scheme")
        return sorted({host.lower() for host in hosts})
