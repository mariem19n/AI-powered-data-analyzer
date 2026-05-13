import pandas as pd

from app.agents.analysis.runner import _records_to_dataframe


def test_records_to_dataframe_converts_string_dtype_iso_dates():
    source = pd.DataFrame(
        {
            "date": ["2025-03-01", "2025-03-02", "2025-03-03"],
            "close_usd": [100.0, 101.5, 103.0],
        }
    ).astype({"date": "string"})
    assert pd.api.types.is_string_dtype(source["date"])

    df = _records_to_dataframe(source.to_dict("records"))

    assert pd.api.types.is_datetime64_any_dtype(df["date"])
