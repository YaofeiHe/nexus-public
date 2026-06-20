from __future__ import annotations

from nexus.tools.safety import detect_high_risk_actions


def test_safety_gate_blocks_write_action_terms() -> None:
    risks = detect_high_risk_actions("下一步需要 pip install 后登录并 git push")
    assert {"install", "login", "push"} <= set(risks)
