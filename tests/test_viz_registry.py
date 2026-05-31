from app.agents.analysis.stats.descriptive import SHAPE_TIMESERIES
from app.agents.analysis.viz import aggregation_chart  # noqa: F401
from app.agents.analysis.viz.line_chart import line_chart
from app.agents.analysis.viz.templates import (
    default_viz_for_shape,
    get_default_viz_name_for_shape,
    get_viz,
)


def test_timeseries_default_viz_uses_registered_viz_name():
    assert get_default_viz_name_for_shape(SHAPE_TIMESERIES) == "line"
    assert default_viz_for_shape(SHAPE_TIMESERIES) is line_chart


def test_aggregation_viz_is_registered():
    assert get_viz("aggregation_summary").__name__ == "aggregation_chart"
