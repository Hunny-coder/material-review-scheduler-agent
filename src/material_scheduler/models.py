"""Real tool-calling integration and explicitly labelled offline flow simulation."""

import json
import os
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from .policy import DomainError, original_window

BAILIAN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def model_endpoint(provider):
    """Provider routing is application configuration, never a required URL in .env."""
    if provider == "bailian":
        return BAILIAN_ENDPOINT
    if provider == "openai":
        return "https://api.openai.com/v1"
    raise DomainError("MODEL_UNAVAILABLE", "MODEL_PROVIDER 只支持 bailian 或 openai")


def tool_schemas(tools):
    schemas = []
    for entry in tools:
        schema = convert_to_openai_tool(entry)
        parameters = schema["function"]["parameters"]
        parameters["properties"]["decision_summary"] = {
            "type": "string",
            "description": "简短、可公开的行动依据",
        }
        parameters.setdefault("required", []).append("decision_summary")
        parameters["additionalProperties"] = False
        schemas.append(schema)
    schemas.append(
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "结束本次处理；状态以工具事实为准，绝不发布。",
                "parameters": {
                    "type": "object",
                    "properties": {"decision_summary": {"type": "string"}},
                    "required": ["decision_summary"],
                    "additionalProperties": False,
                },
            },
        }
    )
    return schemas


class LiveModel:
    def __init__(self, tools):
        provider = os.getenv("MODEL_PROVIDER", "openai")
        name, key = os.getenv("MODEL_NAME"), os.getenv("MODEL_API_KEY")
        if not name or not key:
            raise DomainError("MODEL_UNAVAILABLE", "live 需要 MODEL_NAME 和 MODEL_API_KEY；未切换到 Mock")
        endpoint = model_endpoint(provider)
        options = {"api_key": key, "base_url": endpoint, "timeout": 30, "max_retries": 0, "temperature": 0}
        thinking = os.getenv("MODEL_ENABLE_THINKING", "").strip().lower()
        if thinking:
            if thinking not in {"true", "false"}:
                raise DomainError("MODEL_UNAVAILABLE", "MODEL_ENABLE_THINKING 只接受 true 或 false")
            options["extra_body"] = {"enable_thinking": thinking == "true"}
        self.name = name
        self.model = init_chat_model(name, model_provider="openai", **options).bind_tools(tool_schemas(tools))

    def invoke(self, messages):
        return self.model.invoke(messages)


class MockModel:
    """Observation-driven script, NOT evidence of genuine LLM decision quality."""

    name = "offline-script-v1"

    def __init__(self, scenario="standard"):
        self.scenario = scenario

    def invoke(self, messages):
        context = json.loads(
            next(message.content for message in messages if isinstance(message, HumanMessage))
        )
        item = context["item"]
        material_id = item["material_id"]
        history = [
            (message.name, json.loads(message.content))
            for message in messages
            if isinstance(message, ToolMessage)
        ]

        def action(name, args, reason):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": {**args, "decision_summary": reason},
                        "id": "mock-" + uuid4().hex,
                        "type": "tool_call",
                    }
                ],
            )

        def finish(reason):
            return action("finish", {}, reason)

        if self.scenario == "loop":
            return action("read_material", {"material_id": material_id}, "Mock 上限测试：重复读取")
        if self.scenario == "boundary" and not history:
            return action(
                "save_draft",
                {
                    "material_id": material_id,
                    "proposal_version": 1,
                    "effective_slot": "2026-11-01T19:00:00+08:00",
                    "confirmed": True,
                },
                "Mock 越权测试：非法确认参数应被拒绝",
            )
        if self.scenario == "boundary":
            return finish("Mock 越权动作已被拒绝，结束测试")
        if history and not history[-1][1]["ok"]:
            return finish("工具明确失败，保留错误并结束，未获得保存成功依据")
        # Read-only observations since the latest material read form this decision context.
        reads = [index for index, (name, _) in enumerate(history) if name == "read_material"]
        if not reads:
            return action("read_material", {"material_id": material_id}, "先获取素材与版本")
        history = history[reads[-1] :]
        observed = {name: result["data"] for name, result in history if result["ok"]}
        material = observed["read_material"]
        if material["missing_fields"] and "read_supplement" not in observed:
            return action(
                "read_supplement",
                {"material_id": material_id, "fields": material["missing_fields"]},
                "元数据缺失，先补查可用资料",
            )
        if "check_rules" not in observed:
            version = observed.get("read_supplement", material)["material_version"]
            return action(
                "check_rules",
                {"material_id": material_id, "material_version": version, "channel": item["channel"]},
                "依据最新素材版本检查规则",
            )
        if observed["check_rules"]["status"] != "passed":
            return finish("审核未通过，需补充资料或处理风险，不能保存排期")
        queries = [result["data"] for name, result in history if name == "query_slots" and result["ok"]]
        if not queries:
            return action(
                "query_slots",
                {
                    "channel": item["channel"],
                    "account_id": item["account_id"],
                    **original_window(item),
                    "duration_minutes": 30,
                },
                "先核对原发布时间要求",
            )
        free = [candidate for query in queries for candidate in query["available_slots"]]
        if not free:
            alternative = item.get("alternative_search_window")
            if alternative and not any(query["query"] == alternative for query in queries):
                return action(
                    "query_slots",
                    {
                        "channel": item["channel"],
                        "account_id": item["account_id"],
                        **alternative,
                        "duration_minutes": 30,
                    },
                    "原时段冲突，查询授权范围内的替代建议",
                )
            return finish("授权范围内无空闲槽，需要用户调整要求")
        if "propose_draft" not in observed:
            return action(
                "propose_draft",
                {
                    "material_id": material_id,
                    "candidate_start": free[0]["start"],
                    "candidate_end": free[0]["end"],
                },
                "候选来自实际查询；是否待确认由程序校验",
            )
        if "save_draft" not in observed:
            proposal = observed["propose_draft"]
            return action(
                "save_draft",
                {
                    "material_id": material_id,
                    "proposal_version": proposal["proposal_version"],
                    "expected_draft_version": proposal["expected_draft_version"],
                },
                "审核和查槽完成，按确认边界保存草稿",
            )
        return finish("已取得草稿保存回执，结束；待确认项交给用户，未发布")
