"""LangGraph research workflow.

Nodes call the tool whitelist. They do not call an exchange, read secrets, or execute generated code.
v1 does not call an external chat model, so report numbers can only come from tool results.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from pquant.agents.tools import ResearchToolbox


class ResearchState(TypedDict, total=False):
    objective: str
    spec: dict[str, Any]
    errors: list[str]
    node_status: dict[str, str]
    experiment_id: str | None
    run_ids: list[str]
    report: str
    model_name: str


def build_research_graph(tools: ResearchToolbox):
    def planner(state: ResearchState) -> ResearchState:
        status = dict(state.get("node_status") or {})
        spec = dict(state.get("spec") or {})
        spec["objective"] = state.get("objective") or spec.get("objective") or "research"
        created = tools.invoke("create_experiment", spec)
        status["planner"] = "ok" if created.get("ok") else "rejected"
        errors = list(created.get("errors") or [])
        return {
            "objective": spec["objective"],
            "spec": created.get("spec") or spec,
            "errors": errors,
            "node_status": status,
            "experiment_id": created.get("experiment_id"),
            "run_ids": [],
            "report": "",
            "model_name": "deterministic-langgraph-v1",
        }

    def evaluator(state: ResearchState) -> ResearchState:
        status = dict(state.get("node_status") or {})
        if state.get("errors"):
            status["evaluator"] = "skipped"
            return {**state, "node_status": status}
        spec = state["spec"]
        run_ids: list[str] = []
        for symbol in spec["symbols"]:
            for strategy_id in spec["strategy_ids"]:
                result = tools.invoke(
                    "run_backtest",
                    {
                        "symbol": symbol,
                        "strategy_id": strategy_id,
                        "parameter_overrides": spec.get("parameter_overrides") or {},
                        "fee_scenario": spec.get("fee_scenario") or "base",
                        "slippage_scenario": spec.get("slippage_scenario") or "base",
                        "validation_scheme": spec.get("validation_scheme") or "full",
                        "experiment_budget": spec.get("experiment_budget") or 6,
                        "date_start": spec.get("date_start"),
                        "date_end": spec.get("date_end"),
                    },
                )
                if result.get("run_id"):
                    run_ids.append(result["run_id"])
        status["evaluator"] = "ok"
        return {**state, "node_status": status, "run_ids": run_ids}

    def risk(state: ResearchState) -> ResearchState:
        status = dict(state.get("node_status") or {})
        for run_id in state.get("run_ids") or []:
            tools.invoke("get_risk_metrics", {"run_id": run_id})
        tools.invoke("compare_backtests", {"run_ids": state.get("run_ids") or []})
        status["risk"] = "ok" if state.get("run_ids") else "no_runs"
        return {**state, "node_status": status}

    def report(state: ResearchState) -> ResearchState:
        status = dict(state.get("node_status") or {})
        generated = tools.invoke("generate_report", {"run_ids": state.get("run_ids") or []})
        text = generated["report"]
        if state.get("errors"):
            text = "Experiment rejected before any backtest.\n" + "\n".join(state["errors"]) + "\n" + text
        status["report"] = "ok"
        return {**state, "node_status": status, "report": text}

    def route_after_planner(state: ResearchState) -> str:
        if state.get("errors"):
            return "report"
        return "evaluator"

    graph = StateGraph(ResearchState)
    graph.add_node("planner", planner)
    graph.add_node("evaluator", evaluator)
    graph.add_node("risk", risk)
    graph.add_node("report", report)
    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_after_planner, {"evaluator": "evaluator", "report": "report"})
    graph.add_edge("evaluator", "risk")
    graph.add_edge("risk", "report")
    graph.add_edge("report", END)
    return graph.compile()


def run_research(tools: ResearchToolbox, spec: dict[str, Any]) -> dict[str, Any]:
    app = build_research_graph(tools)
    result = app.invoke({"objective": spec.get("objective", "research"), "spec": spec, "node_status": {}})
    return dict(result)
