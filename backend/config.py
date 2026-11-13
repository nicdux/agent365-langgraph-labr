import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    auth_mode: Literal["local", "appservice"] = "local"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    knowledge_path: Path = ROOT / "knowledge" / "eduardo.json"
    azure_endpoint: str = Field(default="", repr=False)
    azure_deployment: str = ""
    azure_api_version: str = "2024-10-21"
    azure_auth_mode: Literal["auto", "default", "managed_identity", "api_key"] = "auto"
    azure_api_key: str = Field(default="", repr=False)
    managed_identity_client_id: str | None = None
    timeout_seconds: float = Field(default=30, ge=1, le=60)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if not self.allowed_hosts or any(
            not re.fullmatch(r"[a-zA-Z0-9.-]+", host) for host in self.allowed_hosts
        ):
            raise ValueError("EDUARDO_ALLOWED_HOSTS deve conter hosts exatos, sem curingas.")
        if self.auth_mode == "local" and not set(self.allowed_hosts) <= {
            "localhost", "127.0.0.1"
        }:
            raise ValueError("O modo local permite apenas localhost e 127.0.0.1.")
        if self.auth_mode == "appservice" and self.azure_auth_mode == "default":
            raise ValueError("No Azure, use managed_identity ou api_key via Key Vault.")
        if self.azure_endpoint:
            endpoint = urlsplit(self.azure_endpoint)
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or endpoint.path not in ("", "/")
            ):
                raise ValueError("AZURE_OPENAI_ENDPOINT deve ser uma URL base HTTPS.")
        if self.azure_deployment and not re.fullmatch(
            r"[a-zA-Z0-9._-]{1,120}", self.azure_deployment
        ):
            raise ValueError("AZURE_OPENAI_DEPLOYMENT_NAME invalido.")
        return self

    @property
    def credential_mode(self) -> str:
        if self.azure_auth_mode != "auto":
            return self.azure_auth_mode
        return "managed_identity" if self.auth_mode == "appservice" else "default"

    def validate_hosting(self) -> None:
        if self.auth_mode == "local" and os.environ.get("WEBSITE_INSTANCE_ID"):
            raise ValueError("No App Service, configure EDUARDO_AUTH_MODE=appservice.")
        if self.auth_mode == "appservice" and (
            not os.environ.get("WEBSITE_SITE_NAME")
            or os.environ.get("WEBSITE_AUTH_ENABLED", "").lower() != "true"
        ):
            raise ValueError("O modo appservice exige Easy Auth habilitado no App Service.")

    @classmethod
    def from_environment(cls) -> "Settings":
        values: dict[str, object] = {}
        variables = {
            "auth_mode": "EDUARDO_AUTH_MODE",
            "knowledge_path": "EDUARDO_KNOWLEDGE_PATH",
            "azure_endpoint": "AZURE_OPENAI_ENDPOINT",
            "azure_deployment": "AZURE_OPENAI_DEPLOYMENT_NAME",
            "azure_api_version": "AZURE_OPENAI_API_VERSION",
            "azure_auth_mode": "AZURE_OPENAI_AUTH_MODE",
            "azure_api_key": "AZURE_OPENAI_API_KEY",
            "managed_identity_client_id": "AZURE_MANAGED_IDENTITY_CLIENT_ID",
            "timeout_seconds": "EDUARDO_TIMEOUT_SECONDS",
        }
        for field, variable in variables.items():
            value = os.environ.get(variable, "").strip()
            if value:
                values[field] = value
        if os.environ.get("EDUARDO_ALLOWED_HOSTS"):
            values["allowed_hosts"] = tuple(
                host.strip()
                for host in os.environ["EDUARDO_ALLOWED_HOSTS"].split(",")
                if host.strip()
            )
        return cls.model_validate(values)
