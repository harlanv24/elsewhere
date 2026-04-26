from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from worldsim.debug import DebugLogger


class LLMClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMClientConfig:
    model: str
    base_url: str = "http://localhost:8080/v1"
    api_key: str | None = None
    organization: str | None = None
    project: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 5000
    temperature: float | None = 0.7
    json_mode: bool = True
    stream: bool = True

    @classmethod
    def from_env(cls) -> LLMClientConfig:
        api_key = os.getenv("WORLDSIM_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        openai_default = bool(api_key) and not os.getenv("WORLDSIM_LLM_BASE_URL")
        default_base_url = "https://api.openai.com/v1" if openai_default else cls.base_url
        default_model = "gpt-5.2-chat-latest" if openai_default else "Qwen2.5-7B-Coder"
        model = os.getenv("WORLDSIM_LLM_MODEL", default_model)
        base_url = os.getenv("WORLDSIM_LLM_BASE_URL", default_base_url)
        timeout = float(os.getenv("WORLDSIM_LLM_TIMEOUT", str(cls.timeout_seconds)))
        max_tokens = int(os.getenv("WORLDSIM_LLM_MAX_TOKENS", str(cls.max_tokens)))
        temperature_env = os.getenv("WORLDSIM_LLM_TEMPERATURE")
        if temperature_env is not None:
            temperature = float(temperature_env)
        elif base_url.startswith("https://api.openai.com/"):
            temperature = None
        else:
            temperature = cls.temperature
        json_mode = os.getenv("WORLDSIM_LLM_JSON_MODE", "1").lower() not in {"0", "false", "no"}
        stream = os.getenv("WORLDSIM_LLM_STREAM", "1").lower() not in {"0", "false", "no"}
        return cls(
            model=model,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            organization=os.getenv("OPENAI_ORG_ID"),
            project=os.getenv("OPENAI_PROJECT_ID"),
            timeout_seconds=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            stream=stream,
        )


class LLMClient:
    """Tiny OpenAI-compatible chat completions client."""

    def __init__(self, config: LLMClientConfig, debug_logger: DebugLogger | None = None) -> None:
        self.config = config
        self.debug_logger = debug_logger

    @classmethod
    def from_env(cls, debug_logger: DebugLogger | None = None) -> LLMClient:
        return cls(LLMClientConfig.from_env(), debug_logger)

    def complete(self, system: str, user: str, response_schema: dict[str, object] | None = None) -> str:
        if self.config.stream:
            return self.complete_streaming(system, user, response_schema=response_schema)
        payload = self._request_payload(system, user, json_mode=self.config.json_mode, response_schema=response_schema)
        return self._post_chat_completion(payload)

    def complete_streaming(
        self,
        system: str,
        user: str,
        on_delta: Callable[[str], None] | None = None,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        payload = self._request_payload(system, user, json_mode=self.config.json_mode, response_schema=response_schema)
        payload["stream"] = True
        return self._post_streaming_chat_completion(payload, on_delta)

    def _request_payload(
        self,
        system: str,
        user: str,
        json_mode: bool,
        response_schema: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        if self._uses_openai_api():
            payload["max_completion_tokens"] = self.config.max_tokens
        else:
            payload["max_tokens"] = self.config.max_tokens
        if json_mode:
            payload["response_format"] = self._response_format(response_schema)
        return payload

    def _post_chat_completion(self, payload: dict[str, object]) -> str:
        data = json.dumps(payload).encode("utf-8")
        self._log_request(payload)
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                self._log_response(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._log_error("http_error", code=exc.code, detail=detail)
            if exc.code == 400 and "response_format" in payload:
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                self._log_error("retry_without_response_format", code=exc.code)
                return self._post_chat_completion(retry_payload)
            raise LLMClientError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            self._log_error("url_error", detail=str(exc))
            raise LLMClientError(f"LLM request failed: {exc}") from exc

        try:
            result: dict[str, Any] = json.loads(raw)
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise LLMClientError(
                    "LLM response was truncated before it finished. Increase WORLDSIM_LLM_MAX_TOKENS."
                )
            return str(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMClientError("LLM response did not match OpenAI chat completions format.") from exc

    def _post_streaming_chat_completion(
        self,
        payload: dict[str, object],
        on_delta: Callable[[str], None] | None,
    ) -> str:
        data = json.dumps(payload).encode("utf-8")
        self._log_request(payload)
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            headers=self._request_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return self._read_sse_response(response, on_delta)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._log_error("stream_http_error", code=exc.code, detail=detail)
            if exc.code == 400 and "response_format" in payload:
                retry_payload = dict(payload)
                retry_payload.pop("response_format", None)
                self._log_error("stream_retry_without_response_format", code=exc.code)
                return self._post_streaming_chat_completion(retry_payload, on_delta)
            raise LLMClientError(f"LLM streaming request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            self._log_error("stream_url_error", detail=str(exc))
            raise LLMClientError(f"LLM streaming request failed: {exc}") from exc

    def _read_sse_response(self, response: Any, on_delta: Callable[[str], None] | None) -> str:
        chunks: list[str] = []
        raw_events: list[str] = []
        finish_reason: str | None = None
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            raw_events.append(data)
            try:
                event = json.loads(data)
                choice = event["choices"][0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta", {})
                content = delta.get("content")
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise LLMClientError("LLM streaming response did not match OpenAI SSE format.") from exc
            if not content:
                continue
            chunks.append(str(content))
            if on_delta is not None:
                on_delta(str(content))
        text = "".join(chunks)
        if self.debug_logger is not None:
            self.debug_logger.log(
                "llm_stream_response",
                chunk_count=len(chunks),
                content=text,
                finish_reason=finish_reason,
                raw_event_count=len(raw_events),
                raw_events=raw_events,
            )
        if finish_reason == "length":
            raise LLMClientError(
                "LLM streaming response was truncated before it finished. Increase WORLDSIM_LLM_MAX_TOKENS."
            )
        return text

    def _log_request(self, payload: dict[str, object]) -> None:
        if self.debug_logger is None:
            return
        self.debug_logger.log(
            "llm_request",
            endpoint=f"{self.config.base_url}/chat/completions",
            payload=payload,
        )

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.config.organization:
            headers["OpenAI-Organization"] = self.config.organization
        if self.config.project:
            headers["OpenAI-Project"] = self.config.project
        return headers

    def _uses_openai_api(self) -> bool:
        return self.config.base_url.startswith("https://api.openai.com/")

    def _response_format(self, response_schema: dict[str, object] | None) -> dict[str, object]:
        if self._uses_openai_api() and response_schema is not None:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "worldsim_director_response",
                    "schema": response_schema,
                    "strict": False,
                },
            }
        return {"type": "json_object"}

    def _log_response(self, raw: str) -> None:
        if self.debug_logger is None:
            return
        self.debug_logger.log("llm_response", raw=raw)

    def _log_error(self, kind: str, **fields: object) -> None:
        if self.debug_logger is None:
            return
        self.debug_logger.log("llm_error", kind=kind, **fields)
