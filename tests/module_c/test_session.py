"""Session state: the full snapshot is the source of truth, not B's lineage field."""
from __future__ import annotations

import pytest

from agent.contracts import ScenarioParams
from agent.harness.session import Session


def make_session() -> Session:
    return Session("s-unit", ScenarioParams(
        population_demand_ratio=0.2, max_walk_time_min=15.0, budget_units=None,
        disabled_shelter_ids=(), data_version="zhongshan_pilot_v0.1"))


def test_only_named_fields_change():
    session = make_session()
    first = session.plan_turn({"population_demand_ratio": 0.35})
    session.commit(first, "r1")
    second = session.plan_turn({"max_walk_time_min": 20})

    assert second.params.population_demand_ratio == 0.35  # not reset to 0.2
    assert second.params.max_walk_time_min == 20
    assert second.params.budget_units is None
    assert second.changes == {"max_walk_time_min": 20}
    assert second.parent_scenario_id == first.scenario_id


def test_budget_and_disabled_shelters_survive_later_turns():
    session = make_session()
    state = session.plan_turn({"budget_units": 6, "disabled_shelter_ids": ["zs_1"]})
    session.commit(state, "r1")
    later = session.plan_turn({"max_walk_time_min": 12})
    assert later.params.budget_units == 6
    assert later.params.disabled_shelter_ids == ("zs_1",)


def test_history_is_immutable_after_new_turn():
    session = make_session()
    state = session.plan_turn({"population_demand_ratio": 0.35})
    session.commit(state, "r1")
    before = session.history[1].params.to_dict()
    session.plan_turn({"population_demand_ratio": 0.5})
    assert session.history[1].params.to_dict() == before


def test_scenario_ids_unique_and_parent_linked():
    session = make_session()
    ids = [session.current.scenario_id]
    for ratio in (0.3, 0.4, 0.5):
        state = session.plan_turn({"population_demand_ratio": ratio})
        session.commit(state, f"r-{ratio}")
        ids.append(state.scenario_id)
    assert len(set(ids)) == len(ids)
    assert session.history[-1].parent_scenario_id == ids[-2]


def test_unknown_change_key_is_rejected():
    session = make_session()
    with pytest.raises(ValueError):
        session.plan_turn({"population": 0.3})


def test_commit_without_changes_reuses_scenario_id():
    session = make_session()
    base_id = session.current.scenario_id
    session.commit(session.current, "r1")
    assert session.current.scenario_id == base_id
    assert session.current.result_id == "r1"
    assert len(session.history) == 1


def test_unknown_result_reference_is_rejected():
    session = make_session()
    with pytest.raises(Exception) as excinfo:
        session.get_result("not-mine")
    assert "GUARD_UNKNOWN_RESULT" in str(getattr(excinfo.value, "code", ""))


def test_rollback_returns_to_earlier_scenario():
    session = make_session()
    first = session.current
    second = session.plan_turn({"population_demand_ratio": 0.4})
    session.commit(second, "r2")
    restored = session.rollback_to(first.scenario_id)
    assert restored.scenario_id == first.scenario_id
    assert session.current.params.population_demand_ratio == 0.2
