from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote, urlencode

import httpx

from app.schemas.model_gateway import ModelProviderPreset


PROVIDER_PRESETS: dict[str, ModelProviderPreset] = {
    "openai": ModelProviderPreset(id="openai", name="OpenAI", protocol="openai_responses", base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY", models=["gpt-5.4", "gpt-5-mini"]),
    "azure_openai": ModelProviderPreset(id="azure_openai", name="Azure OpenAI", protocol="azure_openai", base_url="https://RESOURCE.openai.azure.com", api_key_env="AZURE_OPENAI_API_KEY", models=[], supports_model_sync=False),
    "anthropic": ModelProviderPreset(id="anthropic", name="Anthropic", protocol="anthropic", base_url="https://api.anthropic.com", api_key_env="ANTHROPIC_API_KEY", models=["claude-sonnet-4-6", "claude-haiku-4-5"]),
    "gemini": ModelProviderPreset(id="gemini", name="Google Gemini", protocol="gemini", base_url="https://generativelanguage.googleapis.com/v1beta", api_key_env="GEMINI_API_KEY", models=["gemini-3-flash-preview", "gemini-3.1-pro-preview"]),
    "deepseek": ModelProviderPreset(id="deepseek", name="DeepSeek", protocol="openai_chat", base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY", models=["deepseek-chat", "deepseek-reasoner"]),
    "dashscope": ModelProviderPreset(id="dashscope", name="通义千问 / DashScope", protocol="openai_chat", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key_env="DASHSCOPE_API_KEY", models=["qwen-plus", "qwen-max"]),
    "zhipu": ModelProviderPreset(id="zhipu", name="智谱 GLM", protocol="openai_chat", base_url="https://open.bigmodel.cn/api/paas/v4", api_key_env="ZHIPU_API_KEY", models=["glm-4-plus", "glm-4-flash"]),
    "volcengine": ModelProviderPreset(id="volcengine", name="豆包 / 火山方舟", protocol="openai_chat", base_url="https://ark.cn-beijing.volces.com/api/v3", api_key_env="VOLCENGINE_API_KEY", models=[]),
    "moonshot": ModelProviderPreset(id="moonshot", name="Kimi / Moonshot", protocol="openai_chat", base_url="https://api.moonshot.cn/v1", api_key_env="MOONSHOT_API_KEY", models=["moonshot-v1-8k", "kimi-k2.5"]),
    "siliconflow": ModelProviderPreset(id="siliconflow", name="硅基流动", protocol="openai_chat", base_url="https://api.siliconflow.cn/v1", api_key_env="SILICONFLOW_API_KEY", models=[]),
    "openrouter": ModelProviderPreset(id="openrouter", name="OpenRouter", protocol="openai_chat", base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY", models=[]),
    "xai": ModelProviderPreset(id="xai", name="xAI", protocol="openai_chat", base_url="https://api.x.ai/v1", api_key_env="XAI_API_KEY", models=["grok-4", "grok-4-fast"]),
    "mistral": ModelProviderPreset(id="mistral", name="Mistral AI", protocol="openai_chat", base_url="https://api.mistral.ai/v1", api_key_env="MISTRAL_API_KEY", models=["mistral-large-latest", "mistral-small-latest"]),
    "ollama": ModelProviderPreset(id="ollama", name="Ollama", protocol="openai_chat", base_url="http://localhost:11434/v1", api_key_env="OLLAMA_API_KEY", models=[], requires_api_key=False),
    "vllm": ModelProviderPreset(id="vllm", name="vLLM", protocol="openai_chat", base_url="http://localhost:8000/v1", api_key_env="VLLM_API_KEY", models=[], requires_api_key=False),
    "openai_compatible": ModelProviderPreset(id="openai_compatible", name="OpenAI Compatible", protocol="openai_chat", base_url="https://api.example.com/v1", api_key_env="MODEL_API_KEY", models=[]),
}


@dataclass(slots=True)
class GatewayResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None


class GatewayUpstreamError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Upstream HTTP {status_code}: {detail}")
        self.status_code = status_code


def _headers(protocol: str, api_key: str, config: dict[str, object]) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if protocol == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": str(config.get("anthropic_version", "2023-06-01"))})
    elif protocol == "gemini":
        headers["x-goog-api-key"] = api_key
    elif protocol == "azure_openai":
        headers["api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if config.get("organization"):
        headers["OpenAI-Organization"] = str(config["organization"])
    return headers


def _request_spec(channel: object, model: str, messages: list[dict[str, str]], system_prompt: str,
                  temperature: float, max_tokens: int, stream: bool) -> tuple[str, dict[str, str], dict[str, object]]:
    protocol = str(channel.protocol)
    base = str(channel.base_url).rstrip("/")
    config = dict(channel.config or {})
    if protocol == "anthropic":
        body: dict[str, object] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": stream}
        if system_prompt:
            body["system"] = system_prompt
        return f"{base}/v1/messages", {}, body
    if protocol == "gemini":
        contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]} for item in messages]
        body = {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{base}/models/{quote(model, safe='')}:{action}", {}, body
    if protocol == "azure_openai":
        api_version = str(config.get("api_version", "2025-04-01-preview"))
        body = {"messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
                "temperature": temperature, "max_tokens": max_tokens, "stream": stream}
        return f"{base}/openai/deployments/{quote(model, safe='')}/chat/completions?{urlencode({'api-version': api_version})}", {}, body
    if protocol == "openai_responses":
        body = {"model": model, "input": messages, "temperature": temperature, "max_output_tokens": max_tokens, "stream": stream}
        if system_prompt:
            body["instructions"] = system_prompt
        return f"{base}/responses", {}, body
    body = {"model": model, "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": stream}
    return f"{base}/chat/completions", {}, body


def _error(response: httpx.Response) -> GatewayUpstreamError:
    try:
        body = response.json()
        detail = body.get("error", body)
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("detail") or json.dumps(detail, ensure_ascii=False)
    except Exception:
        detail = response.text[:500]
    return GatewayUpstreamError(response.status_code, str(detail)[:500])


def call_model(channel: object, model: str, messages: list[dict[str, str]], system_prompt: str,
               temperature: float, max_tokens: int, api_key: str) -> GatewayResponse:
    url, extra_headers, payload = _request_spec(channel, model, messages, system_prompt, temperature, max_tokens, False)
    headers = _headers(str(channel.protocol), api_key, dict(channel.config or {})) | extra_headers
    with httpx.Client(timeout=float(channel.timeout_seconds), follow_redirects=False) as client:
        response = client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise _error(response)
    body = response.json()
    protocol = str(channel.protocol)
    if protocol == "anthropic":
        text = "".join(str(item.get("text", "")) for item in body.get("content", []) if item.get("type") == "text")
        usage = body.get("usage") or {}
        return GatewayResponse(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), body.get("stop_reason"))
    if protocol == "gemini":
        candidate = (body.get("candidates") or [{}])[0]
        text = "".join(str(item.get("text", "")) for item in candidate.get("content", {}).get("parts", []))
        usage = body.get("usageMetadata") or {}
        return GatewayResponse(text, int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0)), candidate.get("finishReason"))
    if protocol == "openai_responses":
        text = str(body.get("output_text") or "")
        if not text:
            text = "".join(str(part.get("text", "")) for item in body.get("output", []) for part in item.get("content", []) if part.get("type") in {"output_text", "text"})
        usage = body.get("usage") or {}
        return GatewayResponse(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), body.get("status"))
    choice = (body.get("choices") or [{}])[0]
    usage = body.get("usage") or {}
    return GatewayResponse(str(choice.get("message", {}).get("content", "")), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), choice.get("finish_reason"))


def stream_model(channel: object, model: str, messages: list[dict[str, str]], system_prompt: str,
                 temperature: float, max_tokens: int, api_key: str) -> Iterator[dict[str, object]]:
    url, extra_headers, payload = _request_spec(channel, model, messages, system_prompt, temperature, max_tokens, True)
    headers = _headers(str(channel.protocol), api_key, dict(channel.config or {})) | extra_headers
    with httpx.Client(timeout=float(channel.timeout_seconds), follow_redirects=False) as client:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                response.read()
                raise _error(response)
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                event = json.loads(raw)
                protocol = str(channel.protocol)
                if protocol == "anthropic":
                    if event.get("type") == "content_block_delta":
                        yield {"type": "delta", "text": event.get("delta", {}).get("text", "")}
                    elif event.get("type") == "message_delta":
                        usage = event.get("usage") or {}
                        yield {"type": "usage", "completion_tokens": int(usage.get("output_tokens", 0))}
                elif protocol == "gemini":
                    candidate = (event.get("candidates") or [{}])[0]
                    text = "".join(str(item.get("text", "")) for item in candidate.get("content", {}).get("parts", []))
                    if text:
                        yield {"type": "delta", "text": text}
                    usage = event.get("usageMetadata") or {}
                    if usage:
                        yield {"type": "usage", "prompt_tokens": int(usage.get("promptTokenCount", 0)), "completion_tokens": int(usage.get("candidatesTokenCount", 0))}
                elif protocol == "openai_responses":
                    if event.get("type") == "response.output_text.delta":
                        yield {"type": "delta", "text": event.get("delta", "")}
                    elif event.get("type") == "response.completed":
                        usage = event.get("response", {}).get("usage") or {}
                        yield {"type": "usage", "prompt_tokens": int(usage.get("input_tokens", 0)), "completion_tokens": int(usage.get("output_tokens", 0))}
                else:
                    choice = (event.get("choices") or [{}])[0]
                    text = choice.get("delta", {}).get("content", "")
                    if text:
                        yield {"type": "delta", "text": text}
                    usage = event.get("usage") or {}
                    if usage:
                        yield {"type": "usage", "prompt_tokens": int(usage.get("prompt_tokens", 0)), "completion_tokens": int(usage.get("completion_tokens", 0))}


def fetch_models(channel: object, api_key: str) -> list[str]:
    base = str(channel.base_url).rstrip("/")
    protocol = str(channel.protocol)
    if protocol == "azure_openai":
        raise RuntimeError("Azure OpenAI deployments must be configured manually")
    url = f"{base}/models"
    headers = _headers(protocol, api_key, dict(channel.config or {}))
    with httpx.Client(timeout=float(channel.timeout_seconds), follow_redirects=False) as client:
        response = client.get(url, headers=headers)
    if response.status_code >= 400:
        raise _error(response)
    body = response.json()
    if protocol == "gemini":
        return sorted({str(item.get("name", "")).removeprefix("models/") for item in body.get("models", []) if "generateContent" in item.get("supportedGenerationMethods", [])})
    return sorted({str(item.get("id", "")) for item in body.get("data", []) if item.get("id")})
