"""Multi-turn conversation state and the authoritative scenario snapshot.

Module B keeps a scenario ledger, but `parent_scenario_id` there is *lineage
only*: B rebuilds each scenario from its config defaults and applies just the
fields in the current request. Sending only a delta therefore silently resets
every untouched parameter (verified: ratio 0.35 -> 0.2). C must own the full
snapshot and resend it on every turn.
"""
from __future__ import annotations


from dataclasses import replace
import threading
from typing import Any, Iterable

from agent.contracts import (
    SCENARIO_CHANGE_KEYS,
    ScenarioParams,
    ScenarioState,
    digest,
    utc_now,
)
from agent.errors import GuardUnknownResult


class Session:
    """One conversation. Scenarios are append-only."""

    def __init__(self, session_id: str, defaults: ScenarioParams, *,
                 valid_shelter_ids: frozenset[str] | None = None) -> None:
        self.session_id = session_id
        self._defaults = defaults
        self._valid_shelter_ids = valid_shelter_ids
        self._lock = threading.RLock()
        self._history: list[ScenarioState] = []
        self._results: dict[str, dict[str, Any]] = {}
        first = ScenarioState(
            scenario_id=self._make_scenario_id(defaults, turn=1),
            parent_scenario_id=None,
            params=defaults,
            changes={},
            turn=1,
        )
        self._history.append(first)
        self._current = first

    @property
    def defaults(self) -> ScenarioParams:
        return self._defaults

    @property
    def valid_shelter_ids(self) -> frozenset[str] | None:
        return self._valid_shelter_ids

    # ------------------------------------------------------------------ #
    # Scenario planning
    # ------------------------------------------------------------------ #
    def _make_scenario_id(self, params: ScenarioParams, turn: int) -> str:
        return f"c-{self.session_id[:8]}-t{turn:02d}-{params.fingerprint[:8]}"

    @property
    def current(self) -> ScenarioState:
        with self._lock:
            return self._current

    @property
    def history(self) -> tuple[ScenarioState, ...]:
        with self._lock:
            return tuple(self._history)

    def plan_turn(self, changes: dict[str, Any] | None = None) -> ScenarioState:
        """Return the *next* scenario without committing it.

        Only the explicitly supplied keys change; everything else is copied from
        the current snapshot.
        """
        changes = dict(changes or {})
        unknown = set(changes) - set(SCENARIO_CHANGE_KEYS)
        if unknown:
            raise ValueError(f"unsupported scenario change keys: {sorted(unknown)}")
        with self._lock:
            base = self._current
            turn = base.turn + 1
            params = base.params.merged(changes) if changes else base.params
            return ScenarioState(
                scenario_id=self._make_scenario_id(params, turn),
                parent_scenario_id=base.scenario_id,
                params=params,
                changes=changes,
                turn=turn,
            )

    def commit(self, state: ScenarioState, result_id: str | None = None) -> ScenarioState:
        """Append the executed scenario to history and make it current.

        When a turn reuses the current scenario (no parameter change), only the
        result link is refreshed - the parameter snapshot is never rewritten.
        """
        with self._lock:
            stored = state.with_result(result_id) if result_id else state
            if self._history and self._history[-1].scenario_id == stored.scenario_id:
                self._history[-1] = stored
            else:
                self._history.append(stored)
            self._current = stored
            return stored

    def rollback_to(self, scenario_id: str) -> ScenarioState:
        """Return to an earlier scenario by id (used by 'undo that change')."""
        with self._lock:
            for state in reversed(self._history):
                if state.scenario_id == scenario_id:
                    self._current = state
                    return state
        raise GuardUnknownResult(f"Unknown scenario_id in this session: {scenario_id}",
                                 details={"scenario_id": scenario_id})

    def find_scenario(self, scenario_id: str) -> ScenarioState | None:
        with self._lock:
            for state in self._history:
                if state.scenario_id == scenario_id:
                    return state
        return None

    def lineage(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "scenario_id": row.scenario_id,
                    "parent_scenario_id": row.parent_scenario_id,
                    "turn": row.turn,
                    "changes": row.changes,
                    "params": row.params.to_dict(),
                    "result_id": row.result_id,
                }
                for row in self._history
            ]

    # ------------------------------------------------------------------ #
    # Result registry (summaries only — full documents stay in B)
    # ------------------------------------------------------------------ #
    def remember_result(self, result_id: str, *, scenario_id: str, data_version: str,
                        summary: dict[str, Any], options_digest: str | None = None) -> None:
        with self._lock:
            self._results[result_id] = {
                "result_id": result_id,
                "scenario_id": scenario_id,
                "data_version": data_version,
                "summary": summary,
                "options_digest": options_digest,
                "stored_at": utc_now(),
            }

    def get_result(self, result_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._results.get(result_id)
        if row is None:
            raise GuardUnknownResult(
                "result_id was not produced by this session; refusing to explain or compare it.",
                details={"result_id": result_id},
            )
        return row

    def has_result(self, result_id: str) -> bool:
        with self._lock:
            return result_id in self._results

    def known_result_ids(self) -> list[str]:
        with self._lock:
            return list(self._results)

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = {
                "session_id": self.session_id,
                "turn": self._current.turn,
                "current": self._current.to_dict(),
                "lineage": self.lineage(),
                "result_ids": list(self._results),
            }
            if self._defaults.region_id is not None:
                payload["region_id"] = self._defaults.region_id
            return payload

    # ------------------------------------------------------------------ #
    def dump(self) -> dict[str, Any]:
        """Full durable form: snapshot plus the projected result registry."""
        snapshot = self.snapshot()
        with self._lock:
            snapshot["results"] = dict(self._results)
        return snapshot

    def restore(self, snapshot: dict[str, Any]) -> "Session":
        """Rebuild a session from `snapshot()` output.

        Only the *verified* parts are restored: parameter snapshots, lineage and
        the projected summaries this session already saw. A restored session
        never claims a Module B job is still running - the caller is responsible
        for marking interrupted turns.
        """
        states: list[ScenarioState] = []
        for row in snapshot.get("lineage") or []:
            params = ScenarioParams(**row["params"])
            states.append(ScenarioState(
                scenario_id=row["scenario_id"],
                parent_scenario_id=row.get("parent_scenario_id"),
                params=params,
                changes=dict(row.get("changes") or {}),
                turn=int(row.get("turn") or 1),
                created_at=row.get("created_at") or utc_now(),
                result_id=row.get("result_id"),
            ))
        if not states:
            raise ValueError("session snapshot contains no scenario states")
        with self._lock:
            self._history = states
            self._current = states[-1]
            self._results = dict(snapshot.get("results") or {})
            first_params = states[0].params
            if first_params.region_id is not None and self._defaults.region_id is None:
                self._defaults = replace(
                    self._defaults,
                    region_id=first_params.region_id,
                    data_version=first_params.data_version,
                )
        return self


class SessionRegistry:
    """In-memory session table for the MCP process.

    Sessions are conversation-scoped. They are intentionally *not* persisted:
    a restart must not resurrect a half-verified planning context. Result ids
    remain resolvable through Module B itself.
    """

    def __init__(self, defaults: ScenarioParams) -> None:
        self._defaults = defaults
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str, *,
                      defaults: ScenarioParams | None = None,
                      valid_shelter_ids: frozenset[str] | None = None) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id, defaults or self._defaults,
                                  valid_shelter_ids=valid_shelter_ids)
                self._sessions[session_id] = session
            return session

    def configure(self, session_id: str, defaults: ScenarioParams, *,
                  valid_shelter_ids: frozenset[str] | None = None) -> Session:
        with self._lock:
            session = Session(session_id, defaults, valid_shelter_ids=valid_shelter_ids)
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def reset(self, session_id: str, *,
              defaults: ScenarioParams | None = None,
              valid_shelter_ids: frozenset[str] | None = None) -> Session:
        with self._lock:
            existing = self._sessions.get(session_id)
            chosen_defaults = defaults or (existing.defaults if existing is not None else self._defaults)
            chosen_shelters = (
                valid_shelter_ids
                if valid_shelter_ids is not None
                else (existing.valid_shelter_ids if existing is not None else None)
            )
            session = Session(session_id, chosen_defaults, valid_shelter_ids=chosen_shelters)
            self._sessions[session_id] = session
            return session

    def ids(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._sessions)
