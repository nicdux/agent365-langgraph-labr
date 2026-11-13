import asyncio
import json
import logging
from collections import Counter
from typing import Literal, Protocol, TypeVar

from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError
from azure.identity.aio import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from openai import (
    APITimeoutError, AsyncAzureOpenAI, BadRequestError, OpenAIError, RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.config import Settings
from backend.guardrails import (
    CLASSIFIER_PROMPT,
    DOMAIN_REFUSAL,
    MAX_ANSWER_CHARS,
    OFFICIAL_REFERENCES,
    SAFETY_RESPONSE,
    SYSTEM_PROMPT,
    UNCERTAIN_RESPONSE,
    VALIDATOR_PROMPT,
    DraftAnswer,
    ResponseVerdict,
    TopicDecision,
    has_only_verified_links,
)

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 12000
MAX_ASSISTANT_CHARS = MAX_ANSWER_CHARS
MAX_SESSION_MESSAGES = 40
ModelT = TypeVar("ModelT", bound=BaseModel)


class ChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HistoryMessage(ChatModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_ASSISTANT_CHARS)

    @model_validator(mode="after")
    def user_limit(self) -> "HistoryMessage":
        if self.role == "user" and len(self.content) > MAX_MESSAGE_CHARS:
            raise ValueError("Pergunta do historico muito longa.")
        return self


class ChatRequest(ChatModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryMessage] = Field(
        default_factory=list, max_length=MAX_HISTORY_MESSAGES
    )

    @model_validator(mode="after")
    def validate_history(self) -> "ChatRequest":
        if len(self.history) % 2:
            raise ValueError("O historico deve conter pares completos.")
        if sum(len(item.content) for item in self.history) > MAX_HISTORY_CHARS:
            raise ValueError("Historico muito longo.")
        for index, item in enumerate(self.history):
            expected = "user" if index % 2 == 0 else "assistant"
            if item.role != expected:
                raise ValueError("Ordem de mensagens invalida.")
        return self


class Source(ChatModel):
    id: str
    title: str
    label: str
    url: str | None


class ChatResponse(ChatModel):
    answer: str
    sources: list[Source]
    grounded: bool


class AssistantError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class MicrosoftModel(Protocol):
    async def classify(self, request: ChatRequest) -> TopicDecision: ...

    async def generate(self, request: ChatRequest) -> DraftAnswer: ...

    async def validate(
        self, request: ChatRequest, draft: DraftAnswer
    ) -> ResponseVerdict: ...

    async def close(self) -> None: ...


class ContentFiltered(Exception):
    pass


class AzureMicrosoftModel:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: AsyncAzureOpenAI | None = None
        self.credential: AsyncTokenCredential | None = None

    def _client(self) -> AsyncAzureOpenAI:
        if self.client is not None:
            return self.client
        settings = self.settings
        if not settings.azure_endpoint or not settings.azure_deployment:
            raise AssistantError(
                "configuration_required",
                "O Azure OpenAI ainda não foi configurado no servidor.",
                503,
            )
        token_provider = None
        if settings.credential_mode == "api_key":
            if not settings.azure_api_key:
                raise AssistantError(
                    "configuration_required",
                    "A credencial do serviço de IA não foi configurada no servidor.",
                    503,
                )
        else:
            if settings.credential_mode == "managed_identity":
                self.credential = ManagedIdentityCredential(
                    client_id=settings.managed_identity_client_id
                )
            else:
                self.credential = DefaultAzureCredential(
                    exclude_interactive_browser_credential=True
                )
            token_provider = get_bearer_token_provider(
                self.credential, "https://cognitiveservices.azure.com/.default"
            )
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            azure_deployment=settings.azure_deployment,
            api_version=settings.azure_api_version,
            api_key=(
                settings.azure_api_key if settings.credential_mode == "api_key" else None
            ),
            azure_ad_token_provider=token_provider,
            timeout=settings.timeout_seconds,
            max_retries=0,
        )
        return self.client

    async def _structured(
        self, prompt: str, payload: dict[str, object],
        response_type: type[ModelT], tokens: int,
    ) -> ModelT:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        completion = await self._client().chat.completions.create(
            model=self.settings.azure_deployment,
            messages=messages,
            max_completion_tokens=tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                },
            },
        )
        if not completion.choices:
            raise ValueError("Resposta estruturada ausente.")
        choice = completion.choices[0]
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise ContentFiltered()
        if choice.finish_reason != "stop" or not choice.message.content:
            raise ValueError("Resposta estruturada incompleta.")
        return response_type.model_validate_json(choice.message.content)

    async def classify(self, request: ChatRequest) -> TopicDecision:
        return await self._structured(
            CLASSIFIER_PROMPT, {"conversation": request.model_dump()}, TopicDecision, 1024
        )

    async def generate(self, request: ChatRequest) -> DraftAnswer:
        return await self._structured(
            SYSTEM_PROMPT,
            {"conversation": request.model_dump(), "references": OFFICIAL_REFERENCES},
            DraftAnswer, 2048,
        )

    async def validate(
        self, request: ChatRequest, draft: DraftAnswer
    ) -> ResponseVerdict:
        return await self._structured(
            VALIDATOR_PROMPT,
            {"conversation": request.model_dump(), "candidate": draft.model_dump()},
            ResponseVerdict, 1024,
        )

    async def close(self) -> None:
        try:
            if self.client is not None:
                await self.client.close()
        finally:
            if self.credential is not None:
                await self.credential.close()


class EduardoAssistant:
    def __init__(
        self, model: MicrosoftModel, timeout_seconds: float
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.metrics: Counter[str] = Counter()

    def _record(self, outcome: str) -> None:
        self.metrics[outcome] += 1
        LOGGER.info("assistant_guardrail outcome=%s", outcome)

    async def _checked_answer(self, request: ChatRequest) -> ChatResponse:
        topic = await self.model.classify(request)
        self._record("classification_" + topic.decision)
        if topic.decision in ("refuse", "injection"):
            return ChatResponse(answer=DOMAIN_REFUSAL, sources=[], grounded=False)
        if topic.decision == "clarify":
            draft = DraftAnswer(answer=topic.clarification, source_ids=[])
        else:
            draft = await self.model.generate(request)
        verdict = await self.model.validate(request, draft)
        if not verdict.within_scope or not verdict.follows_instructions:
            self._record("output_blocked")
            return ChatResponse(answer=DOMAIN_REFUSAL, sources=[], grounded=False)
        if not verdict.appropriately_cautious or not has_only_verified_links(draft.answer):
            self._record("output_uncertain")
            return ChatResponse(answer=UNCERTAIN_RESPONSE, sources=[], grounded=False)
        self._record("clarification" if topic.decision == "clarify" else "answer_validated")
        return ChatResponse(
            answer=draft.answer,
            sources=[
                Source(
                    id=source_id,
                    title=OFFICIAL_REFERENCES[source_id]["title"],
                    label="Microsoft Learn",
                    url=OFFICIAL_REFERENCES[source_id]["url"],
                )
                for source_id in draft.source_ids
            ],
            grounded=False,
        )

    async def answer(self, request: ChatRequest) -> ChatResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._checked_answer(request)
        except ContentFiltered:
            self._record("content_filtered")
            return ChatResponse(answer=SAFETY_RESPONSE, sources=[], grounded=False)
        except BadRequestError as error:
            if error.code == "content_filter":
                self._record("content_filtered")
                return ChatResponse(answer=SAFETY_RESPONSE, sources=[], grounded=False)
            LOGGER.warning("assistant_failure category=BadRequestError")
            raise AssistantError(
                "model_unavailable", "O serviço de IA não conseguiu processar a consulta.", 502
            ) from None
        except (TimeoutError, APITimeoutError):
            self._record("timeout")
            LOGGER.warning("assistant_failure category=timeout")
            raise AssistantError(
                "model_timeout",
                "A consulta demorou mais que o esperado. Tente novamente.",
                504,
            ) from None
        except RateLimitError:
            self._record("upstream_rate_limited")
            LOGGER.warning("assistant_failure category=RateLimitError")
            raise AssistantError(
                "model_rate_limited",
                "O deployment atingiu um limite de uso. Aguarde um minuto e tente novamente.",
                429,
            ) from None
        except (OpenAIError, AzureError) as error:
            self._record("upstream_error")
            LOGGER.warning("assistant_failure category=%s", type(error).__name__)
            raise AssistantError(
                "model_unavailable",
                "O serviço de IA está indisponível. Tente novamente em instantes.",
                502,
            ) from None
        except ValueError:
            self._record("invalid_model_response")
            LOGGER.warning("assistant_failure category=invalid_model_response")
            raise AssistantError(
                "invalid_model_response",
                "Não foi possível validar a resposta de IA. Tente novamente.",
                502,
            ) from None
