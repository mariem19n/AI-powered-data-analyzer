from app.orchestrator.aggregator import ResponseAggregator
from app.orchestrator.schemas import (
    AgentType,
    ExecutionPlan,
    ExecutionStep,
    IntentType,
    StepResult,
    StepStatus,
)


def test_aggregator_collects_sql_and_analysis_warnings():
    plan = ExecutionPlan(
        plan_id="plan-test",
        intent=IntentType.AGGREGATION,
        signature="sig-test",
        steps=[
            ExecutionStep(
                step_id="sql_1",
                agent=AgentType.SQL_AGENT,
                description="SQL",
            ),
            ExecutionStep(
                step_id="analyse_1",
                agent=AgentType.ANALYSIS_AGENT,
                description="Analysis",
                depends_on=["sql_1"],
            ),
        ],
    )
    step_results = {
        "sql_1": StepResult(
            step_id="sql_1",
            status=StepStatus.SUCCESS,
            data={
                "records": [],
                "columns": [],
                "row_count": 0,
                "warnings": ["SQL warning"],
            },
        ),
        "analyse_1": StepResult(
            step_id="analyse_1",
            status=StepStatus.SUCCESS,
            data={
                "insights": [],
                "visualizations": [],
                "recommendations": [],
                "warnings": ["Analysis warning"],
            },
        ),
    }

    response = ResponseAggregator().aggregate(
        session_id="session-test",
        question="q",
        intent=None,
        plan=plan,
        step_results=step_results,
        total_duration_s=0.1,
        llm_calls=0,
    )

    assert response.warnings == [
        "[sql_1] SQL warning",
        "[analyse_1] Analysis warning",
    ]
