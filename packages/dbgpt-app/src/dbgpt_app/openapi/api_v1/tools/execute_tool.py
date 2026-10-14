"""execute_tool tool — run a registered business tool by name."""

import json
import logging
from typing import Any, Dict

from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)


def make_execute_tool(react_state: Dict[str, Any]):
    def _accepted_params_hint(name: str) -> str:
        """把某个工具的真实参数名拼成提示串。

        为什么需要（2026-09-17 现场实测）：模型有时会自造参数名（job_search 被传
        {"city","status","limit"}，真实签名是 district/keyword/category/salary_min/top_n），
        而报错原文 "got an unexpected keyword argument 'city'" 对模型不够可操作 ——
        实测它据此回答"服务不可用"就放弃了。这里把可用参数名一并回给它，下一轮即可自我纠正。
        """
        try:
            from dbgpt._private.config import Config
            from dbgpt.agent.resource.manage import get_resource_manager
            from dbgpt.agent.resource.resource_api import AgentResource, ResourceType

            res = get_resource_manager(Config().SYSTEM_APP).build_resource_by_type(
                ResourceType.Tool.value,
                AgentResource(type=ResourceType.Tool.value, value=name),
            )
            inner = getattr(res, "_tool", res)
            args = getattr(inner, "args", None) or {}
            names = list(args.keys())
            if names:
                return (
                    "【参数名提示】该工具实际接受的参数名是: "
                    + ", ".join(names)
                    + "。请用这些参数名重新调用；不了解的参数直接省略，不要自造参数名。"
                )
        except Exception:
            pass  # 拿不到元数据就不提示，不能因此影响原有报错路径
        return ""

    @tool(description="Execute a tool by name with JSON args.")
    async def execute_tool(tool_name: str, args: dict) -> str:
        from dbgpt._private.config import Config
        from dbgpt.agent.resource.manage import get_resource_manager
        from dbgpt.agent.resource.resource_api import AgentResource, ResourceType
        from dbgpt.agent.resource.tool.pack import ToolPack

        CFG = Config()
        try:
            from dbgpt.agent.resource.connector.confirmation import (
                _PENDING_CONFIRMATIONS,
            )
            from dbgpt.agent.resource.connector.manager import (
                ConnectorManager as _ConnectorManager,
            )

            connector_manager = CFG.SYSTEM_APP.get_component(
                "connector_manager", _ConnectorManager, default_component=None
            )
            if connector_manager is not None:
                interceptor = connector_manager.get_confirmation_interceptor()
                registry = connector_manager.get_confirmation_registry()
                if interceptor.should_confirm(tool_name, args):
                    import asyncio
                    import uuid

                    confirm_id = str(uuid.uuid4())
                    registry.register(confirm_id)
                    _PENDING_CONFIRMATIONS[confirm_id] = {
                        "confirm_id": confirm_id,
                        "tool_name": tool_name,
                        "args_summary": interceptor._summarize_args(args),
                        "message": f"即将执行写操作 {tool_name}，是否确认？",
                        "timeout": 300,
                    }
                    try:
                        approved = await asyncio.wait_for(
                            registry.wait_for(confirm_id), timeout=300
                        )
                    except asyncio.TimeoutError:
                        approved = False
                    finally:
                        _PENDING_CONFIRMATIONS.pop(confirm_id, None)
                    if not approved:
                        return json.dumps(
                            {
                                "chunks": [
                                    {
                                        "output_type": "text",
                                        "content": "用户拒绝了此操作，工具执行已取消。",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
        except Exception:
            # Confirmation support is optional; keep the existing graceful
            # degradation when connector components are unavailable.
            pass

        rm = get_resource_manager(CFG.SYSTEM_APP)
        try:
            tool_resource = rm.build_resource_by_type(
                ResourceType.Tool.value,
                AgentResource(type=ResourceType.Tool.value, value=tool_name),
            )
            tool_pack = ToolPack([tool_resource])
            result = await tool_pack.async_execute(resource_name=tool_name, **args)
            result_str = str(result)
            # Cap output size; larger results are persisted by ToolResultStorage.
            MAX_EXECUTE_TOOL_OUTPUT = 20_000
            if len(result_str) > MAX_EXECUTE_TOOL_OUTPUT:
                result_str = (
                    result_str[:MAX_EXECUTE_TOOL_OUTPUT]
                    + f"\n\n... [truncated at {MAX_EXECUTE_TOOL_OUTPUT} chars]"
                )
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": result_str}]},
                ensure_ascii=False,
            )
        except Exception as primary_exc:
            _param_hint = _accepted_params_hint(tool_name)
            # Connector tools are normally called directly, but models may
            # route them through execute_tool. Preserve the active-pack
            # fallback used before the tools/ refactor.
            try:
                from dbgpt.agent.resource.connector.manager import (
                    ConnectorManager as _ConnectorManager,
                )

                connector_manager = CFG.SYSTEM_APP.get_component(
                    "connector_manager",
                    _ConnectorManager,
                    default_component=None,
                )
                if connector_manager is not None:
                    for connector_id, pack in connector_manager._active_packs.items():
                        if tool_name in pack._resources:
                            result = await pack.async_execute(
                                resource_name=tool_name, **args
                            )
                            logger.info(
                                "execute_tool dispatched '%s' via "
                                "ConnectorManager fallback (connector=%s). "
                                "Prefer direct Action call for connector tools.",
                                tool_name,
                                connector_id,
                            )
                            return json.dumps(
                                {
                                    "chunks": [
                                        {
                                            "output_type": "text",
                                            "content": str(result),
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
            except Exception as fallback_exc:
                logger.warning(
                    "execute_tool fallback to ConnectorManager failed for '%s': %s",
                    tool_name,
                    fallback_exc,
                )
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Tool execute failed: {fallback_exc} "
                                    f"(primary lookup error: {primary_exc}) "
                                    f"{_param_hint}"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Tool execute failed: {primary_exc} {_param_hint}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    return execute_tool
