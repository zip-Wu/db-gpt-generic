"""OpenAI-shaped customer gateway with per-request SM3 authentication."""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from dbgpt.core import ModelMetadata, ModelOutput
from dbgpt.model.proxy.base import register_proxy_model_adapter

from .chatgpt import OpenAICompatibleDeployModelParameters, OpenAILLMClient


@dataclass
class CustomerGatewayParameters(OpenAICompatibleDeployModelParameters):
    provider: str = "proxy/customer_gateway"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    app_key: str = field(default="", metadata={"tags": "privacy"})
    app_secret: str = field(default="", metadata={"tags": "privacy"})
    ca_file: Optional[str] = None
    verify_tls: bool = True
    read_timeout: int = 120


def gateway_headers(app_key, app_secret):
    rt = str(time.time_ns() // 1_000_000)
    sign = hashlib.new("sm3", (app_key + app_secret + rt).encode()).hexdigest()
    return {"appKey": app_key, "rt": rt, "sign": sign}


def check_gateway_error(data):
    if data.get("success") is False or data.get("code") not in (None, 0, "0"):
        raise ValueError(
            f"Customer gateway error {data.get('code')}: {data.get('msg', 'unknown')}"
        )


class CustomerGatewayClient(OpenAILLMClient):
    @classmethod
    def param_class(cls):
        return CustomerGatewayParameters

    @classmethod
    def new_client(cls, model_params, default_executor=None):
        if not model_params.app_key or not model_params.app_secret:
            raise ValueError("Customer gateway app_key and app_secret are required")
        client = cls(
            api_key="unused",
            model=model_params.real_provider_model_name,
            model_alias=model_params.real_provider_model_name,
            context_length=model_params.context_length or 32768,
        )
        client.gateway_params = model_params
        return client

    def _build_request(self, request, stream=False):
        payload = super()._build_request(request, stream)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        return payload

    def _http_client(self):
        params = self.gateway_params
        return httpx.AsyncClient(
            verify=params.ca_file or params.verify_tls,
            timeout=httpx.Timeout(params.read_timeout, connect=15),
            trust_env=False,
        )

    async def generate_v1(self, messages, payload):
        params = self.gateway_params
        async with self._http_client() as client:
            response = await client.post(
                params.api_base,
                headers=gateway_headers(params.app_key, params.app_secret),
                json={**payload, "messages": messages, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
        check_gateway_error(data)
        if not data.get("choices"):
            raise ValueError("Customer gateway returned no choices")
        message = data["choices"][0]["message"]
        return ModelOutput.build(
            message.get("content") or "",
            message.get("reasoning_content") or "",
            usage=data.get("usage"),
            tool_calls=message.get("tool_calls"),
        )

    async def generate_stream_v1(self, messages, payload):
        from httpx_sse import aconnect_sse

        params = self.gateway_params
        text = reasoning = ""
        calls = {}
        usage = None
        async with self._http_client() as client:
            async with aconnect_sse(
                client,
                "POST",
                params.api_base,
                headers=gateway_headers(params.app_key, params.app_secret),
                json={**payload, "messages": messages, "stream": True},
            ) as source:
                source.response.raise_for_status()
                if "text/event-stream" not in source.response.headers.get(
                    "content-type", ""
                ):
                    await source.response.aread()
                    check_gateway_error(source.response.json())
                    raise ValueError("Customer gateway did not return an event stream")
                done = False
                async for event in source.aiter_sse():
                    if event.data.strip() == "[DONE]":
                        done = True
                        break
                    if not event.data:
                        continue
                    data = json.loads(event.data)
                    check_gateway_error(data)
                    usage = data.get("usage") or usage
                    for choice in data.get("choices") or []:
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta") or {}
                        text += delta.get("content") or ""
                        reasoning += delta.get("reasoning_content") or ""
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(
                                part.get("index", 0),
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if part.get("id"):
                                call["id"] = part["id"]
                            for key in ("name", "arguments"):
                                call["function"][key] += (
                                    part.get("function") or {}
                                ).get(key) or ""
                    yield ModelOutput.build(text, reasoning, usage=usage)
                if not done:
                    raise ValueError("Customer gateway stream ended before [DONE]")
        yield ModelOutput.build(
            text,
            reasoning,
            usage=usage,
            tool_calls=[calls[i] for i in sorted(calls)] or None,
        )


register_proxy_model_adapter(
    CustomerGatewayClient,
    supported_models=[
        ModelMetadata(
            model="qwen3.8_27b",
            context_length=32768,
            function_calling=True,
        )
    ],
)
