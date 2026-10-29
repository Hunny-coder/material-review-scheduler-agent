"""LangGraph executes a bounded model → guarded tool → observation loop."""

import json
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from .models import LiveModel, MockModel
from .policy import DomainError, canonical, failure
from .prompts import SYSTEM_PROMPT


class GraphState(TypedDict):
    messages: list
    pending_calls: list
    done: bool


def run_item(runtime, trace, max_model_calls=12, max_tool_calls=12, model=None, batch_budget=None):
    budget = batch_budget if batch_budget is not None else {"model": 60, "tool": 60}
    try:
        model = model or (
            LiveModel(runtime.read_tools)
            if runtime.mode == "live"
            else MockModel(runtime.fault.get("model", "standard"))
        )
    except DomainError as exc:
        runtime.final_status, runtime.stop_reason = "failed", exc.code
        runtime.last_error = failure(exc)
        trace.emit("model_error", code=exc.code, summary=exc.message)
        return runtime.result()
    except Exception as exc:  # noqa: BLE001 -- constructor errors are also provider failures
        runtime.final_status, runtime.stop_reason = "failed", "MODEL_UNAVAILABLE"
        trace.emit("model_error", code=runtime.stop_reason, exception_type=type(exc).__name__)
        return runtime.result()

    def decide(state):
        if runtime.model_calls >= max_model_calls or budget["model"] <= 0:
            runtime.final_status, runtime.stop_reason = "incomplete", "MAX_MODEL_CALLS"
            trace.emit("budget_exhausted", stop_reason=runtime.stop_reason)
            return {"done": True}
        runtime.model_calls += 1
        budget["model"] -= 1
        try:
            message = model.invoke(state["messages"])
        except Exception as exc:  # noqa: BLE001 -- isolate provider failures without leaking credentials
            runtime.final_status, runtime.stop_reason = "failed", "MODEL_UNAVAILABLE"
            # Never expose provider exception text, which may contain credentials or request headers.
            trace.emit("model_error", code=runtime.stop_reason, exception_type=type(exc).__name__)
            return {"done": True}
        # Only public response metadata and numeric usage; never log model reasoning content.
        trace.emit(
            "model_response",
            material_id=runtime.item["material_id"],
            model_call=runtime.model_calls,
            response_id=message.id,
            model_name=message.response_metadata.get("model_name"),
            finish_reason=message.response_metadata.get("finish_reason"),
            usage=message.usage_metadata,
        )
        calls = message.tool_calls or []
        invalid = message.invalid_tool_calls or []
        if invalid or (not calls and message.response_metadata.get("finish_reason") == "tool_calls"):
            if runtime.tool_calls >= max_tool_calls or budget["tool"] <= 0:
                runtime.final_status, runtime.stop_reason = "incomplete", "MAX_TOOL_CALLS"
                trace.emit("budget_exhausted", stop_reason=runtime.stop_reason)
                return {"done": True}
            runtime.tool_calls += 1
            budget["tool"] -= 1
            runtime.last_error = failure(
                DomainError("INVALID_MODEL_ACTION", "模型工具调用无法解析，未执行任何业务动作")
            )
            trace.emit(
                "model_invalid_action",
                model_call=runtime.model_calls,
                code="INVALID_MODEL_ACTION",
                invalid_tool_names=[entry.get("name") for entry in invalid],
                summary="模型返回工具结束标记但无可执行调用，已拒绝且计入预算",
            )
            # Do not forward malformed assistant tool calls into the provider's message protocol.
            # This is an explicit runtime observation; the next model call chooses how to proceed.
            feedback = HumanMessage(
                content=canonical(
                    {
                        "source": "runtime_action_validator",
                        "observation": runtime.last_error,
                        "instruction": "上一轮工具调用没有执行。请根据此前工具观察，选择一个合法工具调用；不要声称保存成功。",
                    }
                )
            )
            return {"messages": state["messages"] + [feedback], "pending_calls": []}
        if not calls:
            trace.emit("model_finish", summary="模型未选择工具，业务状态仍以现有证据为准")
            return {"messages": state["messages"] + [message], "done": True}
        return {"messages": state["messages"] + [message], "pending_calls": calls}

    def execute(state):
        messages = list(state["messages"])
        for call in state["pending_calls"]:
            if runtime.tool_calls >= max_tool_calls or budget["tool"] <= 0:
                runtime.final_status, runtime.stop_reason = "incomplete", "MAX_TOOL_CALLS"
                trace.emit("budget_exhausted", stop_reason=runtime.stop_reason)
                return {"messages": messages, "done": True}
            runtime.tool_calls += 1
            budget["tool"] -= 1
            args = dict(call["args"])
            reason = args.pop("decision_summary", "")
            trace.emit(
                "model_action",
                material_id=runtime.item["material_id"],
                action_source="model" if runtime.mode == "live" else "mock_script",
                model_call=runtime.model_calls,
                tool_call_id=call["id"],
                next_action=call["name"],
                tool_args=args,
                decision_summary=str(reason)[:600],
                based_on_sequence=trace.sequence,
            )
            if call["name"] == "finish":
                trace.emit("finish", summary="根据工具事实组装结果")
                return {"messages": messages, "done": True}
            result = runtime.execute(call["name"], args)
            trace.emit(
                "tool_result",
                material_id=runtime.item["material_id"],
                tool_name=call["name"],
                tool_call_id=call["id"],
                tool_args=args,
                observation_summary=result["summary"],
                result=result,
            )
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False), name=call["name"], tool_call_id=call["id"]
                )
            )
        return {"messages": messages, "pending_calls": []}

    graph = StateGraph(GraphState)
    graph.add_node("model", decide)
    graph.add_node("tools", execute)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model", lambda state: END if state["done"] else ("tools" if state["pending_calls"] else "model")
    )
    graph.add_conditional_edges("tools", lambda state: END if state["done"] else "model")
    app = graph.compile()
    initial = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=canonical({"item": runtime.item, "mode": runtime.mode})),
        ],
        "pending_calls": [],
        "done": False,
    }
    try:
        app.invoke(initial, {"recursion_limit": 200})
    except GraphRecursionError:
        runtime.final_status, runtime.stop_reason = "incomplete", "GRAPH_LIMIT"
        trace.emit("budget_exhausted", stop_reason=runtime.stop_reason)
    return runtime.result()
