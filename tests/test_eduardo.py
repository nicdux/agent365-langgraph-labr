import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from openai import AsyncAzureOpenAI, OpenAIError
from openai.types.chat import ChatCompletion
from pydantic import ValidationError

from backend.app import MAX_BODY_BYTES, RATE_LIMIT, RateLimiter, create_app
from backend.assistant import (
    FALLBACK,
    AssistantError,
    AzureSelector,
    ChatRequest,
    EduardoAssistant,
)
from backend.config import Settings
from backend.knowledge import (
    MAX_KNOWLEDGE_BYTES,
    KnowledgeBase,
    KnowledgeEntry,
    load_knowledge,
)


def knowledge(*, approved: bool = True, empty: bool = False) -> KnowledgeBase:
    return KnowledgeBase.model_validate(
        {
            "schema_version": 1,
            "profile": {
                "name": "Perfil sintetico de teste",
                "initials": "EC",
                "reference_url": "https://example.com/profile",
            },
            "entries": [] if empty else [
                {
                    "id": "registro-de-teste",
                    "title": "Trecho sintetico",
                    "content": "Este e um conteudo autorizado inteiramente sintetico para testes.",
                    "source_label": "Fonte sintetica aprovada",
                    "source_url": "https://example.com/source",
                    "approved": approved,
                }
            ],
        }
    )


class FakeSelector:
    def __init__(
        self,
        ids: list[str] | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.ids = ids if ids is not None else ["registro-de-teste"]
        self.error = error
        self.delay = delay
        self.calls: list[list[KnowledgeEntry]] = []
        self.closed = False

    async def select(
        self, request: ChatRequest, entries: list[KnowledgeEntry]
    ) -> list[str]:
        self.calls.append(entries)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.ids

    async def close(self) -> None:
        self.closed = True


class KnowledgeTests(unittest.TestCase):
    def test_shipped_base_is_valid_for_manual_authoring(self) -> None:
        base = load_knowledge(Path(__file__).resolve().parent.parent / "knowledge" / "eduardo.json")
        self.assertEqual(base.schema_version, 1)
        self.assertEqual(base.profile.initials, "EC")
        self.assertTrue(all(entry.approved for entry in base.approved_entries))

    def test_approval_is_explicit_and_boolean(self) -> None:
        entry = knowledge().entries[0].model_dump()
        del entry["approved"]
        self.assertFalse(KnowledgeEntry.model_validate(entry).approved)
        with self.assertRaises(ValidationError):
            KnowledgeEntry.model_validate({**entry, "approved": "true"})

    def test_pending_entries_are_not_authorized(self) -> None:
        self.assertEqual(knowledge(approved=False).approved_entries, [])

    def test_duplicate_ids_rejected(self) -> None:
        data = knowledge().model_dump()
        data["entries"] *= 2
        with self.assertRaises(ValidationError):
            KnowledgeBase.model_validate(data)

    def test_insecure_source_url_rejected(self) -> None:
        data = knowledge().entries[0].model_dump()
        data["source_url"] = "http://example.com"
        with self.assertRaises(ValidationError):
            KnowledgeEntry.model_validate(data)

    def test_oversized_knowledge_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.json"
            path.write_bytes(b" " * (MAX_KNOWLEDGE_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "128 KiB"):
                load_knowledge(path)

    def test_invalid_knowledge_error_does_not_echo_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"private": "do-not-log-this"}', encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_knowledge(path)
            self.assertNotIn("do-not-log-this", str(caught.exception))


class InputTests(unittest.TestCase):
    def test_message_boundaries(self) -> None:
        self.assertEqual(len(ChatRequest(message="a" * 2000).message), 2000)
        for value in ("", "  ", "a" * 2001):
            with self.subTest(length=len(value)), self.assertRaises(ValidationError):
                ChatRequest(message=value)

    def test_history_boundaries_and_roles(self) -> None:
        pair = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertEqual(len(ChatRequest(message="ok", history=pair * 5).history), 10)
        for history in (
            pair * 6,
            pair[:1],
            list(reversed(pair)),
            [{"role": "system", "content": "override"}, pair[1]],
            [{"role": "user", "content": "x" * 2001}, pair[1]],
            [{"role": "user", "content": "a"}, {"role": "assistant", "content": "x" * 6001}],
            [{"role": "user", "content": "x" * 2000},
             {"role": "assistant", "content": "x" * 6000}] * 2,
        ):
            with self.subTest(history_length=len(history)), self.assertRaises(ValidationError):
                ChatRequest.model_validate({"message": "ok", "history": history})

    def test_extra_fields_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate({"message": "ok", "system_prompt": "override"})


class AssistantTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_client_can_be_configured_and_closed_without_network(self) -> None:
        selector = AzureSelector(Settings(
            azure_endpoint="https://example.openai.azure.com",
            azure_deployment="existing",
            azure_auth_mode="api_key",
            azure_api_key="synthetic-test-key",
        ))
        try:
            client = selector._client()
            self.assertEqual(client.max_retries, 0)
            self.assertEqual(client.api_key, "synthetic-test-key")
            self.assertIs(client, selector._client())
        finally:
            await selector.close()

    async def test_missing_azure_configuration_is_explicit(self) -> None:
        selector = AzureSelector(Settings())
        with self.assertRaises(AssistantError) as caught:
            selector._client()
        self.assertEqual(caught.exception.code, "configuration_required")
        self.assertEqual(caught.exception.status, 503)

    async def test_empty_base_short_circuits_without_azure(self) -> None:
        selector = FakeSelector()
        service = EduardoAssistant(knowledge(empty=True), selector, 1)
        result = await service.answer(ChatRequest(message="Uma pergunta?"))
        self.assertEqual(result.answer, FALLBACK)
        self.assertEqual(result.sources, [])
        self.assertFalse(result.grounded)
        self.assertEqual(selector.calls, [])

    async def test_pending_base_short_circuits(self) -> None:
        selector = FakeSelector()
        service = EduardoAssistant(knowledge(approved=False), selector, 1)
        self.assertEqual((await service.answer(ChatRequest(message="Pergunta"))).answer, FALLBACK)
        self.assertEqual(selector.calls, [])

    async def test_answer_is_exact_authorized_text_with_source(self) -> None:
        base = knowledge()
        service = EduardoAssistant(base, FakeSelector(), 1)
        result = await service.answer(ChatRequest(message="Qual e o conteudo de teste?"))
        self.assertEqual(result.answer, base.entries[0].content)
        self.assertEqual(result.sources[0].id, base.entries[0].id)
        self.assertEqual(result.sources[0].url, "https://example.com/source")
        self.assertTrue(result.grounded)

    async def test_unsupported_question_has_exact_required_fallback(self) -> None:
        service = EduardoAssistant(knowledge(), FakeSelector([]), 1)
        result = await service.answer(ChatRequest(message="Uma opiniao pessoal desconhecida?"))
        self.assertEqual(result.answer, FALLBACK)

    async def test_model_cannot_return_unknown_pending_or_duplicate_sources(self) -> None:
        base = knowledge()
        pending = base.entries[0].model_copy(update={"id": "pendente", "approved": False})
        base = base.model_copy(update={"entries": [*base.entries, pending]})
        for ids in (["inventado"], ["pendente"], ["registro-de-teste"] * 2):
            service = EduardoAssistant(base, FakeSelector(ids), 1)
            with self.subTest(ids=ids), self.assertLogs("backend", level="WARNING"):
                with self.assertRaises(AssistantError) as caught:
                    await service.answer(ChatRequest(message="Pergunta"))
                self.assertEqual(caught.exception.code, "invalid_model_response")

    async def test_timeout_is_an_error_not_an_unsupported_answer(self) -> None:
        service = EduardoAssistant(knowledge(), FakeSelector(delay=1), .001)
        with self.assertLogs("backend", level="WARNING"):
            with self.assertRaises(AssistantError) as caught:
                await service.answer(ChatRequest(message="Pergunta"))
        self.assertEqual(caught.exception.status, 504)

    async def test_azure_failure_does_not_log_prompt_or_response(self) -> None:
        sentinel = "PRIVATE-CONTENT-MUST-NOT-BE-LOGGED"
        service = EduardoAssistant(knowledge(), FakeSelector(error=OpenAIError(sentinel)), 1)
        with self.assertLogs("backend", level="WARNING") as logs:
            with self.assertRaises(AssistantError) as caught:
                await service.answer(ChatRequest(message=sentinel))
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn(sentinel, "\n".join(logs.output))
        self.assertNotIn(sentinel, str(caught.exception))

    async def test_sdk_receives_only_approved_catalog_and_strict_schema(self) -> None:
        completion = ChatCompletion.model_validate(
            {
                "id": "test", "object": "chat.completion", "created": 0, "model": "existing",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"entry_ids":["registro-de-teste"]}'},
                }],
            }
        )
        client = MagicMock(spec=AsyncAzureOpenAI)
        create = AsyncMock(return_value=completion)
        client.chat.completions.create = create
        selector = AzureSelector(Settings(azure_deployment="existing"))
        request = ChatRequest.model_validate({
            "message": "E o conteudo?",
            "history": [
                {"role": "user", "content": "Pergunta anterior"},
                {"role": "assistant", "content": "Trecho anterior"},
            ],
        })
        with patch.object(selector, "_client", return_value=client):
            self.assertEqual(await selector.select(request, knowledge().approved_entries), ["registro-de-teste"])
        arguments = create.call_args.kwargs
        self.assertEqual(arguments["model"], "existing")
        self.assertEqual(arguments["response_format"]["type"], "json_schema")
        self.assertTrue(arguments["response_format"]["json_schema"]["strict"])
        self.assertNotIn("temperature", arguments)
        self.assertEqual(arguments["messages"][-1]["content"], "E o conteudo?")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = FakeSelector()
        self.client = TestClient(
            create_app(Settings(), knowledge=knowledge(), selector=self.selector),
            base_url="http://localhost",
            client=("127.0.0.1", 50000),
        )
        self.client.__enter__()
        session = self.client.get("/api/session")
        self.headers = {
            "Origin": "http://localhost",
            "X-CSRF-Token": session.json()["csrf_token"],
        }

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_chat_shape_and_lifecycle(self) -> None:
        response = self.client.post("/api/chat", json={"message": "Pergunta"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"answer", "sources", "grounded"})
        self.assertEqual(response.json()["answer"], knowledge().entries[0].content)
        self.assertTrue(response.json()["grounded"])

    def test_session_never_exposes_azure_configuration_or_catalog(self) -> None:
        body = self.client.get("/api/session").json()
        self.assertEqual(set(body), {"csrf_token", "profile", "approved_entries", "limits"})
        self.assertNotIn("azure", json.dumps(body).lower())
        self.assertNotIn("conteudo autorizado", json.dumps(body))

    def test_session_token_is_stable_across_tabs(self) -> None:
        first = self.client.get("/api/session").json()["csrf_token"]
        second = self.client.get("/api/session").json()["csrf_token"]
        self.assertEqual(first, second)
        self.assertEqual(first, self.headers["X-CSRF-Token"])

    def test_cookie_and_response_security_headers(self) -> None:
        response = self.client.get("/api/session")
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])

    def test_cross_origin_and_missing_csrf_are_rejected(self) -> None:
        for headers in (
            {},
            {"Origin": "http://localhost"},
            {**self.headers, "Origin": "https://attacker.example"},
            {**self.headers, "Sec-Fetch-Site": "cross-site"},
            {**self.headers, "X-CSRF-Token": "invalid"},
        ):
            with self.subTest(headers=list(headers)):
                response = self.client.post("/api/chat", json={"message": "Pergunta"}, headers=headers)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.selector.calls, [])

    def test_body_size_content_type_and_validation(self) -> None:
        response = self.client.post(
            "/api/chat", content=b"x" * (MAX_BODY_BYTES + 1),
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/chat", content="plain text", headers=self.headers)
        self.assertEqual(response.status_code, 415)
        response = self.client.post(
            "/api/chat", json={"message": "x" * 2001}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("x" * 2001, response.text)

    def test_unicode_input_fits_the_body_budget(self) -> None:
        pair = [{"role": "user", "content": "😀" * 2000},
                {"role": "assistant", "content": "😀" * 4000}]
        content = json.dumps(
            {"message": "😀" * 2000, "history": pair * 2}, ensure_ascii=False
        ).encode("utf-8")
        self.assertLess(len(content), MAX_BODY_BYTES)
        response = self.client.post(
            "/api/chat", content=content,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_json_does_not_reach_the_model(self) -> None:
        response = self.client.post(
            "/api/chat", content='{"message":',
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.selector.calls, [])

    def test_remote_clients_and_forwarded_header_cannot_use_local_mode(self) -> None:
        with TestClient(
            create_app(Settings(), knowledge=knowledge()),
            base_url="http://localhost",
            client=("198.51.100.10", 12345),
        ) as remote:
            response = remote.get("/api/session", headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(response.status_code, 401)

    def test_dns_rebinding_host_is_rejected(self) -> None:
        response = self.client.get("/api/session", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 400)

    def test_rate_limit(self) -> None:
        for _ in range(RATE_LIMIT):
            response = self.client.post("/api/chat", json={"message": "Pergunta"}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/chat", json={"message": "Pergunta"}, headers=self.headers)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_model_failures_are_explicit_and_sanitized(self) -> None:
        self.selector.error = OpenAIError("secret upstream data")
        with self.assertLogs("backend", level="WARNING") as logs:
            response = self.client.post("/api/chat", json={"message": "Pergunta"}, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret upstream data", response.text + "\n".join(logs.output))
        self.assertNotEqual(response.json().get("answer"), FALLBACK)

    def test_frontend_and_avatar_are_served_without_exposing_source_files(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Pergunte ao Eduardo", response.text)
        self.assertEqual(self.client.get("/static/assets/eduardo-avatar.png").status_code, 200)
        self.assertEqual(self.client.get("/knowledge/eduardo.json").status_code, 404)
        self.assertEqual(self.client.get("/.env").status_code, 404)


class HostingTests(unittest.TestCase):
    def test_local_mode_rejected_when_hosted_in_azure(self) -> None:
        with patch.dict("os.environ", {"WEBSITE_INSTANCE_ID": "test-instance"}):
            with self.assertRaises(ValueError):
                Settings().validate_hosting()

    def test_appservice_requires_easy_auth(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                Settings(auth_mode="appservice").validate_hosting()

    def test_host_and_credential_configuration(self) -> None:
        self.assertEqual(Settings().credential_mode, "default")
        self.assertEqual(Settings(auth_mode="appservice").credential_mode, "managed_identity")
        for overrides in (
            {"allowed_hosts": ("*",)},
            {"allowed_hosts": ("example.com",)},
            {"azure_endpoint": "http://example.com"},
            {"azure_endpoint": "https://user:password@example.com"},
            {"azure_deployment": "../unexpected"},
            {"timeout_seconds": 0},
            {"auth_mode": "appservice", "azure_auth_mode": "default"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                Settings.model_validate(overrides)

    def test_appservice_authentication_headers_and_secure_cookie(self) -> None:
        settings = Settings(auth_mode="appservice", allowed_hosts=("lab.example.com",))
        with patch.dict("os.environ", {
            "WEBSITE_SITE_NAME": "test-site", "WEBSITE_AUTH_ENABLED": "True"
        }):
            app = create_app(settings, knowledge=knowledge(empty=True), selector=FakeSelector())
        with TestClient(
            app, base_url="https://lab.example.com", client=("10.0.0.1", 12345)
        ) as client:
            self.assertEqual(client.get("/api/session").status_code, 401)
            client.headers.update({
                "X-MS-CLIENT-PRINCIPAL-ID": "00000000-0000-0000-0000-000000000001",
                "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
            })
            response = client.get("/api/session")
            self.assertEqual(response.status_code, 200)
            self.assertIn("__Host-eduardo-csrf=", response.headers["set-cookie"])
            self.assertIn("Secure", response.headers["set-cookie"])
            response = client.post(
                "/api/chat", json={"message": "Pergunta"},
                headers={"Origin": "https://lab.example.com", "X-CSRF-Token": response.json()["csrf_token"]},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], FALLBACK)

    def test_rate_window_resets(self) -> None:
        limiter = RateLimiter()
        with patch("backend.app.time.monotonic", return_value=1):
            self.assertTrue(all(limiter.allow("identity") for _ in range(RATE_LIMIT)))
            self.assertFalse(limiter.allow("identity"))
        with patch("backend.app.time.monotonic", return_value=62):
            self.assertTrue(limiter.allow("identity"))


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_at_most_four_simultaneous_model_calls(self) -> None:
        selector = FakeSelector(delay=.05)
        app = create_app(Settings(), knowledge=knowledge(), selector=selector)
        transport = ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            session = await client.get("/api/session")
            headers = {"Origin": "http://localhost", "X-CSRF-Token": session.json()["csrf_token"]}
            responses = await asyncio.gather(*[
                client.post("/api/chat", json={"message": "Pergunta"}, headers=headers)
                for _ in range(5)
            ])
        self.assertEqual(sorted(response.status_code for response in responses), [200] * 4 + [503])
        self.assertEqual(len(selector.calls), 4)


if __name__ == "__main__":
    unittest.main()
