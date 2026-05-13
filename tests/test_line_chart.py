import json

import pandas as pd

from app.agents.analysis.viz.line_chart import line_chart


def test_line_chart_returns_json_safe_plotly_dict():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2025-03-01", periods=3, freq="D"),
            "close_usd": [100.0, 101.5, 103.0],
            "symbol": ["BTC", "BTC", "BTC"],
        }
    )

    result = line_chart(df, {"x_col": "date", "y_cols": ["close_usd"]})

    assert "data" in result
    assert "layout" in result
    assert len(result["data"]) >= 1
    assert isinstance(result["data"][0]["x"], list)
    assert isinstance(result["data"][0]["y"], list)
    json.dumps(result)
