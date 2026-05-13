import json

import pandas as pd

from app.agents.analysis.llm.schemas import Insight, LLMOutput, Recommendation
from app.agents.analysis.runner import AnalysisAgent
from app.orchestrator.schemas import OrchestratorResponse


class MockLLMClient:
    model = "mock-analysis-model"

    def chat_json_schema(self, *args, **kwargs):
        return LLMOutput(
            insights=[
                Insight(
                    text="La série temporelle progresse sur la période.",
                    confidence=0.9,
                    supporting_stats=["n", "trend_direction"],
                )
            ],
            recommendations=[
                Recommendation(
                    text="Surveiller la continuité de la tendance.",
                    priority="medium",
                )
            ],
            overall_confidence=0.9,
        )


def _btc_timeseries_records():
    dates = pd.date_range("2025-03-01", periods=31, freq="D")
    return [
        {
            "date": d.strftime("%Y-%m-%d"),
            "close_usd": 80000.0 + idx * 100.0,
            "symbol": "BTC",
        }
        for idx, d in enumerate(dates)
    ]


def test_analysis_agent_returns_json_safe_timeseries_visualization():
    agent = AnalysisAgent.build_default(
        llm_client=MockLLMClient(),
        neo4j_driver=None,
    )
    response = agent.run(
        instruction={
            "task": "descriptive",
            "input_steps": ["sql_1"],
            "semantic_context": {},
            "question_id": "q-test",
        },
        upstream_results={
            "sql_1": {
                "records": _btc_timeseries_records(),
                "columns": ["date", "close_usd", "symbol"],
                "row_count": 31,
            }
        },
        semantic_context={},
    )
    payload = response.to_dict()

    assert len(payload["visualizations"]) == 1
    assert payload["metadata"]["subtype"] in ("timeseries_single", "timeseries_multi")
    assert not [
        warning
        for warning in payload["warnings"]
        if "viz" in warning.lower() or "visualisation" in warning.lower()
    ]

    orchestrator_response = OrchestratorResponse(
        session_id="session-test",
        question="prix Bitcoin en mars 2025",
        visualizations=payload["visualizations"],
        insights=payload["insights"],
        recommendations=payload["recommendations"],
    )
    dumped = orchestrator_response.model_dump(mode="json")
    json.dumps(dumped)


def test_descriptive_non_timeseries_shapes_keep_empty_viz_with_viz_warning():
    agent = AnalysisAgent(insight_generator=None, kg_writer=None)

    cases = [
        {
            "records": [
                {"symbol": "BTC", "close_usd": 100.0},
                {"symbol": "ETH", "close_usd": 50.0},
            ],
            "columns": ["symbol", "close_usd"],
        },
        {
            "records": [
                {"close_usd": 100.0},
                {"close_usd": 101.0},
                {"close_usd": 102.0},
            ],
            "columns": ["close_usd"],
        },
    ]

    for case in cases:
        response = agent.run(
            instruction={"task": "descriptive", "input_steps": ["sql_1"]},
            upstream_results={
                "sql_1": {
                    "records": case["records"],
                    "columns": case["columns"],
                    "row_count": len(case["records"]),
                }
            },
            semantic_context={},
        ).to_dict()

        assert response["visualizations"] == []
        assert any(
            "viz" in warning.lower() or "visualisation" in warning.lower()
            for warning in response["warnings"]
        )
