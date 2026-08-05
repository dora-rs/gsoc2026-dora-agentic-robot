"""Live-LLM test (Week 9) — a real model completes the pick-and-place.

This makes a real, paid OpenAI API call, so it is double-gated: it runs only when
both `OPENAI_API_KEY` and `RUN_LIVE_LLM=1` are set, and never fires during an
ordinary `pytest` run. It is the automated form of `agent.live_pick_place_demo`:
a real GPT-4o drives the mission through the real tools and skills, and success
is judged by outcome — the ball must end on the green plate.

    RUN_LIVE_LLM=1 OPENAI_API_KEY=sk-... pytest tests/test_live_llm.py -q
"""

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("OPENAI_API_KEY") and os.environ.get("RUN_LIVE_LLM") == "1"),
        reason="live LLM test needs OPENAI_API_KEY and RUN_LIVE_LLM=1",
    ),
]


def test_live_llm_completes_pick_and_place():
    from agent.live_pick_place_demo import run_live

    node = run_live(verbose=False)
    assert node.ball_on_plate(), f"ball ended at {node.ball_position}"
    assert not node.holding and not node.gripper_closed
