from app.agents.analysis.tasks.comparison import ComparisonTask


def test_comparison_task_compares_volume_series_and_builds_chart():
    result = ComparisonTask().run(
        df=None,
        instruction={"task": "comparison", "input_steps": ["sql_1", "sql_2"]},
        upstream_results={
            "sql_1": {
                "records": [
                    {"date": "2026-03-01", "symbol": "BTC", "volume": 100.0},
                    {"date": "2026-03-02", "symbol": "BTC", "volume": 120.0},
                    {"date": "2026-03-03", "symbol": "BTC", "volume": 140.0},
                ],
                "columns": ["date", "symbol", "volume"],
                "row_count": 3,
            },
            "sql_2": {
                "records": [
                    {"date": "2026-03-01", "symbol": "ETH", "volume": 50.0},
                    {"date": "2026-03-02", "symbol": "ETH", "volume": 60.0},
                    {"date": "2026-03-03", "symbol": "ETH", "volume": 80.0},
                ],
                "columns": ["date", "symbol", "volume"],
                "row_count": 3,
            },
        },
    )

    assert result.insights
    assert result.visualizations
    assert result.recommendations
    assert result.warnings == []
    assert result.stats["aligned_points"] == 3
    assert result.stats["higher_volume_asset"] == "BTC"
    assert result.stats["average_volume_pct_diff"] == 89.473684
    assert result.stats["metrics_by_symbol"]["BTC"]["mean_volume"] == 120.0
    assert result.stats["metrics_by_symbol"]["ETH"]["median_volume"] == 60.0
    assert result.stats["metrics_by_symbol"]["ETH"]["total_volume"] == 190.0


def test_comparison_task_warns_when_input_step_is_missing():
    result = ComparisonTask().run(
        df=None,
        instruction={"task": "comparison", "input_steps": ["sql_1", "sql_2"]},
        upstream_results={
            "sql_1": {
                "records": [
                    {"date": "2026-03-01", "symbol": "BTC", "volume": 100.0},
                ],
                "columns": ["date", "symbol", "volume"],
                "row_count": 1,
            },
        },
    )

    assert result.insights
    assert result.visualizations == []
    assert any("sql_2" in warning for warning in result.warnings)
    assert any("Moins de deux series" in warning for warning in result.warnings)
