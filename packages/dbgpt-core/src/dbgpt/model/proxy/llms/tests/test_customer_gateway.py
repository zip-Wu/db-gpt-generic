import hashlib
import json

import httpx
import pytest

from dbgpt.model.proxy.llms.customer_gateway import (
    CustomerGatewayClient,
    CustomerGatewayParameters,
    gateway_headers,
)


def make_client(handler):
    client = CustomerGatewayClient.new_client(
        CustomerGatewayParameters(
            name="qwen3.8_27b",
            api_base="https://gateway.test/aiApi/workflow/llmProxy",
            app_key="key",
            app_secret="secret",
        )
    )
    client._http_client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    return client


def test_sm3(monkeypatch):
    monkeypatch.setattr("time.time_ns", lambda: 1234567890000000)
    headers = gateway_headers("key", "secret")
    assert headers["rt"] == "1234567890"
    assert headers["sign"] == hashlib.new("sm3", b"keysecret1234567890").hexdigest()


@pytest.mark.asyncio
async def test_nonstream_and_fresh_signature(monkeypatch):
    timestamps = iter([1000000, 2000000])
    monkeypatch.setattr("time.time_ns", lambda: next(timestamps))
    seen = []

    def handler(request):
        assert request.url.path == "/aiApi/workflow/llmProxy"
        assert "authorization" not in request.headers
        seen.append(request.headers["sign"])
        payload = json.loads(request.content)
        assert payload["tools"] == [{"type": "function"}]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "x",
                                    "function": {"name": "lookup", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = make_client(handler)
    for _ in range(2):
        result = await client.generate_v1([], {"tools": [{"type": "function"}]})
        assert result.tool_calls[0]["id"] == "x"
    assert seen[0] != seen[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_http_200_business_error(stream):
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={
                "success": False,
                "code": 10004,
                "msg": "model missing",
            },
        )
    )
    with pytest.raises(ValueError, match="10004"):
        if stream:
            _ = [item async for item in client.generate_stream_v1([], {})]
        else:
            await client.generate_v1([], {})


@pytest.mark.asyncio
async def test_stream_tools_and_missing_usage():
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "content": "",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "query", "arguments": '{"a":'},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"content": None}, "finish_reason": "tool_calls"}]},
    ]
    body = "".join("data:" + json.dumps(chunk) + "\n\n" for chunk in chunks)
    body += "data: [DONE]\n\n"
    client = make_client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=body,
        )
    )
    results = [item async for item in client.generate_stream_v1([], {})]
    assert results[-1].tool_calls[0]["function"] == {
        "name": "query",
        "arguments": '{"a":1}',
    }


@pytest.mark.asyncio
async def test_truncated_stream():
    client = make_client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )
    )
    with pytest.raises(ValueError, match="before"):
        _ = [item async for item in client.generate_stream_v1([], {})]
