"""
Tests for run_pipeline progress reporting.

WHY: the async job flow drives a real, staged progress bar from these
callbacks. If a stage stops emitting (e.g. a node is reordered), the user's
progress bar would silently stall — so the ordered 1..5 sequence is the
contract the frontend depends on.
"""
from web.workflow import run_pipeline


def test_progress_cb_fires_five_ordered_stages(sample_financial_data):
    """A full run reports stages 1..5 in order, regardless of data path.

    No api_key / no stock_code keeps it offline and deterministic.
    """
    seen = []

    def cb(step, label):
        seen.append((step, label))

    run_pipeline(
        company_name="测试公司",
        current_period="2025年报",
        financial_data=sample_financial_data,
        progress_cb=cb,
    )

    steps = [s for s, _ in seen]
    assert steps == [1, 2, 3, 4, 5]
    # Each stage carries a non-empty human-readable label.
    assert all(label for _, label in seen)


def test_progress_cb_is_optional(sample_financial_data):
    """Omitting progress_cb must not break existing callers."""
    state = run_pipeline(
        company_name="测试公司",
        current_period="2025年报",
        financial_data=sample_financial_data,
    )
    assert state["status"] == "completed"
