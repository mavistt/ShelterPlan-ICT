"""A-side independent integer-flow Oracle primitives.

This module intentionally has no dependency on Module B, the HTTP app, or an
Agent.  It is suitable for small, fully specified fixtures and for auditing
the boundary of a GIS-derived scenario.  Real A v2 inputs contain fractional
population/capacity values and hypothetical options, so the release builder
does not silently promote a flow bound to an optimality proof.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable


@dataclass(frozen=True)
class FlowResult:
    served_scaled: int
    scale: int
    total_demand_scaled: int
    total_capacity_scaled: int

    @property
    def served(self) -> float:
        return self.served_scaled / self.scale

    @property
    def status(self) -> str:
        return "INDEPENDENT_ORACLE_PROVEN"


class _Dinic:
    def __init__(self, size: int) -> None:
        self.graph: list[list[list[int]]] = [[] for _ in range(size)]

    def add_edge(self, start: int, end: int, capacity: int) -> None:
        forward = [end, int(capacity), len(self.graph[end])]
        reverse = [start, 0, len(self.graph[start])]
        self.graph[start].append(forward)
        self.graph[end].append(reverse)

    def max_flow(self, source: int, sink: int) -> int:
        total = 0
        while True:
            level = [-1] * len(self.graph)
            level[source] = 0
            queue = [source]
            for node in queue:
                for end, capacity, _ in self.graph[node]:
                    if capacity > 0 and level[end] < 0:
                        level[end] = level[node] + 1
                        queue.append(end)
            if level[sink] < 0:
                return total
            current = [0] * len(self.graph)

            def send(node: int, pushed: int) -> int:
                if node == sink:
                    return pushed
                while current[node] < len(self.graph[node]):
                    edge = self.graph[node][current[node]]
                    end, capacity, reverse_index = edge
                    if capacity > 0 and level[end] == level[node] + 1:
                        amount = send(end, min(pushed, capacity))
                        if amount:
                            edge[1] -= amount
                            self.graph[end][reverse_index][1] += amount
                            return amount
                    current[node] += 1
                return 0

            while True:
                pushed = send(source, 10**30)
                if not pushed:
                    break
                total += pushed


def independent_max_flow(
    demand_capacity: dict[str, float],
    shelter_capacity: dict[str, float],
    reachable_edges: Iterable[tuple[str, str]],
    *,
    scale: int = 1000,
) -> FlowResult:
    """Solve a bipartite capacity flow independently of Module B.

    ``reachable_edges`` is a set/list of (demand_id, shelter_id) pairs.  The
    integer scale is explicit in the result so rounding is auditable.
    """
    if scale <= 0:
        raise ValueError("scale must be positive")
    demand_ids = sorted(demand_capacity)
    shelter_ids = sorted(shelter_capacity)
    demand_index = {value: index for index, value in enumerate(demand_ids)}
    shelter_index = {value: index for index, value in enumerate(shelter_ids)}
    source = 0
    demand_start = 1
    shelter_start = demand_start + len(demand_ids)
    sink = shelter_start + len(shelter_ids)
    flow = _Dinic(sink + 1)
    total_demand = 0
    total_capacity = 0
    for demand_id in demand_ids:
        capacity = max(0, int(round(float(demand_capacity[demand_id]) * scale)))
        total_demand += capacity
        flow.add_edge(source, demand_start + demand_index[demand_id], capacity)
    for shelter_id in shelter_ids:
        capacity = max(0, int(round(float(shelter_capacity[shelter_id]) * scale)))
        total_capacity += capacity
        flow.add_edge(shelter_start + shelter_index[shelter_id], sink, capacity)
    for demand_id, shelter_id in sorted(set(reachable_edges)):
        if demand_id in demand_index and shelter_id in shelter_index:
            flow.add_edge(demand_start + demand_index[demand_id], shelter_start + shelter_index[shelter_id], total_demand)
    return FlowResult(flow.max_flow(source, sink), scale, total_demand, total_capacity)


def enumerate_portfolios(
    options: list[dict[str, Any]],
    budget_units: float,
    demand_capacity: dict[str, float],
    shelter_capacity: dict[str, float],
    reachable_edges: Iterable[tuple[str, str]],
    *,
    scale: int = 1000,
) -> dict[str, Any]:
    """Enumerate a small option set and return the best independently proven flow.

    This is intentionally bounded.  The function refuses more than 12 options
    because the public benchmark contract treats larger cases as a separate
    stress interface rather than a normal evaluation task.
    """
    if len(options) > 12:
        raise ValueError("independent enumeration is capped at 12 options")
    edges = list(reachable_edges)
    rows: list[dict[str, Any]] = []
    for size in range(len(options) + 1):
        for subset in combinations(options, size):
            cost = sum(float(row.get("cost_units") or 0) for row in subset)
            if cost > float(budget_units) + 1e-9:
                continue
            capacities = dict(shelter_capacity)
            for row in subset:
                shelter_id = str(row["shelter_id"])
                capacities[shelter_id] = capacities.get(shelter_id, 0.0) + float(row.get("delta_capacity_persons") or 0)
            result = independent_max_flow(demand_capacity, capacities, edges, scale=scale)
            rows.append({
                "option_ids": [str(row["option_id"]) for row in subset],
                "cost_units": cost,
                "served": result.served,
                "served_scaled": result.served_scaled,
                "oracle_status": result.status,
            })
    if not rows:
        return {"status": "NOT_PROVEN", "reason": "no feasible portfolio", "feasible_portfolio_count": 0}
    best = max(rows, key=lambda row: (row["served_scaled"], -row["cost_units"], row["option_ids"]))
    return {
        "status": "INDEPENDENT_ORACLE_PROVEN",
        "proof_level": "INDEPENDENT_ORACLE_PROVEN",
        "feasible_portfolio_count": len(rows),
        "best": best,
        "portfolios": rows,
        "rounding_scale": scale,
        "note": "Exact only for the supplied integer-scaled bipartite fixture and enumerated option set.",
    }
