"""Language-model providers behind one router.

Each provider either yields text or raises ``ProviderError`` before the first
token. The router uses that rule to fall back. Once a token has been streamed,
fallback would duplicate the start of the answer, so the error is surfaced.

The fake provider is extractive. It is not a fallback for Gemini or OpenAI:
a fake answer next to real embeddings would look grounded and would not be.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.errors import ProviderError
from app.grounding import (
    blocks_from_prompt,
    extractive_answer,
    question_from_prompt,
    summary_payload,
)


class LLMProvider(Protocol):
    """Complete or stream one prompt. Failures raise ``ProviderError``."""

    name: str

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        """Return the full model text."""

    def stream(self, prompt: str) -> Iterator[str]:
        """Yield text pieces. Raise before the first piece when the call fails."""


class FakeLLM:
    """Deterministic extractive model for demo mode and tests."""

    name = "fake"

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        """Quote the documents, or return summary JSON when the prompt asks for it."""
        blocks = blocks_from_prompt(prompt)
        question = question_from_prompt(prompt)
        if as_json or prompt.startswith("CLAIM_SUMMARY_JSON"):
            payload = summary_payload(question, blocks, abstained=False)
            return json.dumps(payload, ensure_ascii=False)
        return extractive_answer(question, blocks)

    def stream(self, prompt: str) -> Iterator[str]:
        """Yield the extractive answer word by word so the UI can stream it."""
        text = self.complete(prompt)
        words = text.split(" ")
        for index, word in enumerate(words):
            piece = word if index == len(words) - 1 else f"{word} "
            if piece:
                yield piece


class GeminiLLM:
    """Gemini generateContent, with a hard timeout from settings."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        """One non-streaming call. JSON mode asks Gemini for a JSON mime type."""
        if not self._settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is empty.")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._settings.gemini_chat_model}:generateContent"
        )
        return self._post(url, prompt, _gemini_generation(as_json=as_json))

    def stream(self, prompt: str) -> Iterator[str]:
        """Parse Gemini's SSE stream and yield only new text."""
        if not self._settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is empty.")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._settings.gemini_chat_model}:streamGenerateContent"
        )
        timeout = httpx.Timeout(self._settings.llm_timeout_seconds)
        try:
            with httpx.stream(
                "POST",
                url,
                params={"key": self._settings.gemini_api_key, "alt": "sse"},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": _gemini_generation(as_json=False),
                },
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                accumulated = ""
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    piece = _gemini_text(line.removeprefix("data:").strip())
                    if not piece:
                        continue
                    # Some responses repeat the full text so far. Yield only the suffix.
                    if piece.startswith(accumulated):
                        delta = piece[len(accumulated) :]
                        accumulated = piece
                    else:
                        delta = piece
                        accumulated += piece
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini failed: {exc}") from exc

    def _post(self, url: str, prompt: str, generation: dict[str, object]) -> str:
        try:
            response = httpx.post(
                url,
                params={"key": self._settings.gemini_api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": generation,
                },
                timeout=self._settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            text = _gemini_visible_text(response.json())
            if not text:
                raise ProviderError("Gemini returned no text.")
            return text
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"Gemini failed: {exc}") from exc


class OpenAICompatibleLLM:
    """Chat completions against OpenAI or any server that speaks the same API.

    ``local`` is the same protocol pointed at LM Studio Bionic. That server
    does not require a key and often rejects JSON mode, so those are optional.
    """

    name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        name: str = "openai",
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        json_mode: bool = True,
        timeout: float | None = None,
    ) -> None:
        self.name = name
        self._settings = settings
        self._base_url = (base_url or settings.openai_base_url).rstrip("/")
        self._model = model or settings.openai_chat_model
        self._api_key = settings.openai_api_key if api_key is None else api_key
        self._json_mode = json_mode
        self._timeout = timeout if timeout is not None else settings.llm_timeout_seconds

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        """Non-streaming chat completion."""
        payload = self._payload(prompt, stream=False, as_json=as_json)
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"{self.name} failed: {exc}") from exc

    def stream(self, prompt: str) -> Iterator[str]:
        """Yield content deltas. ``[DONE]`` ends the stream."""
        payload = self._payload(prompt, stream=True, as_json=False)
        try:
            with httpx.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=httpx.Timeout(self._timeout),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    piece = _openai_delta(data)
                    if piece:
                        yield piece
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} failed: {exc}") from exc

    def _payload(self, prompt: str, *, stream: bool, as_json: bool) -> dict:
        payload: dict = {
            "model": self._model,
            "temperature": 0,
            "stream": stream,
            "messages": [{"role": "user", "content": prompt}],
        }
        if as_json and self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            if self.name == "openai":
                raise ProviderError("OPENAI_API_KEY is empty.")
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}


class LLMRouter:
    """Try providers in order. Fall back only when the attempt produced no text."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ProviderError("No language providers are configured.")
        self.providers = providers
        self.last_provider = providers[0].name

    def complete(self, prompt: str, *, as_json: bool = False) -> str:
        """Return the first successful completion."""
        errors: list[str] = []
        for provider in self.providers:
            try:
                text = provider.complete(prompt, as_json=as_json)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                continue
            self.last_provider = provider.name
            return text
        raise ProviderError("Every provider failed: " + "; ".join(errors))

    def stream(self, prompt: str) -> Iterator[str]:
        """Stream from the first provider that can start. Do not restart mid-answer."""
        errors: list[str] = []
        for provider in self.providers:
            started = False
            try:
                for piece in provider.stream(prompt):
                    started = True
                    self.last_provider = provider.name
                    yield piece
                self.last_provider = provider.name
                return
            except ProviderError as exc:
                if started:
                    raise
                errors.append(f"{provider.name}: {exc}")
        raise ProviderError("Every provider failed: " + "; ".join(errors))


def build_llm_router() -> LLMRouter:
    """Build the configured chain. Fake is never inserted as a silent fallback."""
    settings = get_settings()
    names = [settings.llm_provider]
    if settings.llm_fallback and settings.llm_fallback != settings.llm_provider:
        if settings.llm_provider != "fake" and settings.llm_fallback == "fake":
            raise ProviderError(
                "The fake provider cannot be the fallback for a real provider."
            )
        names.append(settings.llm_fallback)
    return LLMRouter([_build_provider(name, settings) for name in names])


def _build_provider(name: str, settings: Settings) -> LLMProvider:
    """Construct one provider. Unknown names fail here, not halfway through a claim."""
    if name == "fake":
        return FakeLLM()
    if name == "gemini":
        return GeminiLLM(settings)
    if name == "openai":
        return OpenAICompatibleLLM(settings)
    if name == "local":
        if not settings.local_llm_model.strip():
            raise ProviderError(
                "LOCAL_LLM_MODEL is empty. Use the model identifier shown in Bionic."
            )
        return OpenAICompatibleLLM(
            settings,
            name="local",
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            json_mode=False,
            timeout=settings.local_llm_timeout_seconds,
        )
    raise ProviderError(f"Unknown language provider: {name}")


def _gemini_generation(*, as_json: bool) -> dict[str, object]:
    """Request shape for current Gemini flash models.

    3.8 Flash ignores ``temperature`` and a later release will reject it, so
    this request never sends it. ``thinkingLevel`` ``low`` is accepted by
    3.5 and 3.8. ``minimal`` is an error. JSON mode is only for the summary call.
    """
    generation: dict[str, object] = {"thinkingConfig": {"thinkingLevel": "low"}}
    if as_json:
        generation["responseMimeType"] = "application/json"
    return generation


def _gemini_text(raw: str) -> str:
    """Pull the visible answer out of one Gemini SSE JSON payload."""
    try:
        return _gemini_visible_text(json.loads(raw))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


def _gemini_visible_text(payload: dict) -> str:
    """Join answer parts and drop reasoning parts.

    Gemini 3 can put chain-of-thought in ``parts[0]`` with ``thought: true``.
    The stream and the summary parser both need the answer text only.
    """
    parts = payload["candidates"][0]["content"]["parts"]
    pieces: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought"):
            continue
        text = part.get("text") or ""
        if text:
            pieces.append(text)
    return "".join(pieces)


def _openai_delta(raw: str) -> str:
    """Pull one content delta out of an OpenAI stream event."""
    try:
        payload = json.loads(raw)
        return payload["choices"][0]["delta"].get("content") or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""
