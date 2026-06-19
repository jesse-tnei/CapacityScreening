from __future__ import annotations

import numpy as np

from .models import Bus, Branch, Coupler, CandidateSite, NetworkSnapshot


def _union_find(n: int):
    """Union-Find with path compression and union-by-lower-index."""
    parent = list(range(n))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    return find, union


def merge_closed_couplers(snap: NetworkSnapshot) -> NetworkSnapshot:
    """Collapse every closed coupler so its endpoint buses share one node.

    Each set of buses connected by closed couplers is replaced by one
    calculation node represented by the highest-voltage busbar in the set.
    """
    n = len(snap.buses)
    find, union = _union_find(n)
    for c in snap.couplers:
        if c.closed:
            union(c.bus_a, c.bus_b)

    roots_in_order: list[int] = []
    seen: set[int] = set()
    for i in range(n):
        r = find(i)
        if r not in seen:
            seen.add(r)
            roots_in_order.append(r)
    root_to_new = {r: idx for idx, r in enumerate(roots_in_order)}
    old_to_new = [root_to_new[find(i)] for i in range(n)]

    rep: dict[int, int] = {}
    for old_idx, bus in enumerate(snap.buses):
        new_idx = old_to_new[old_idx]
        if new_idx not in rep:
            rep[new_idx] = old_idx
            continue
        cur = snap.buses[rep[new_idx]]
        if (bus.is_busbar, bus.v_nom_kv) > (cur.is_busbar, cur.v_nom_kv):
            rep[new_idx] = old_idx

    new_buses: list[Bus] = []
    for new_idx in range(len(roots_in_order)):
        old_bus = snap.buses[rep[new_idx]]
        new_buses.append(Bus(
            idx=new_idx,
            name=old_bus.name,
            substation=old_bus.substation,
            v_nom_kv=old_bus.v_nom_kv,
            is_busbar=old_bus.is_busbar,
            in_service=True,
        ))

    new_branches: list[Branch] = []
    name_to_old_idx: dict[str, int] = {}
    for br in snap.branches:
        f = old_to_new[br.from_bus]
        t = old_to_new[br.to_bus]
        if f == t:
            continue
        new_branches.append(Branch(
            idx=len(new_branches),
            name=br.name, cls=br.cls,
            from_bus=f, to_bus=t,
            x_pu=br.x_pu, s_rated_mva=br.s_rated_mva,
            in_service=True,
            local_to_subs=list(br.local_to_subs),
        ))
        name_to_old_idx[br.name] = br.idx

    new_couplers: list[Coupler] = []
    for c in snap.couplers:
        if c.closed:
            continue
        a = old_to_new[c.bus_a]
        b = old_to_new[c.bus_b]
        if a == b:
            continue
        new_couplers.append(Coupler(name=c.name, bus_a=a, bus_b=b, closed=False))

    new_branch_idx_by_name = {nb.name: nb.idx for nb in new_branches}
    new_cands: list[CandidateSite] = []
    for cand in snap.candidates:
        new_test = old_to_new[cand.test_bus]
        new_locals = [
            new_branch_idx_by_name[snap.branches[k].name]
            for k in cand.local_branch_idx
            if snap.branches[k].name in new_branch_idx_by_name
        ]
        if len(new_locals) < 2:
            continue
        new_cands.append(CandidateSite(
            substation=cand.substation,
            test_bus=new_test,
            local_branch_idx=new_locals,
        ))

    new_intact = np.array([
        snap.intact_flows_mw[name_to_old_idx[nb.name]] for nb in new_branches
    ])

    return NetworkSnapshot(
        buses=new_buses,
        branches=new_branches,
        couplers=new_couplers,
        candidates=new_cands,
        slack_bus_idx=old_to_new[snap.slack_bus_idx],
        intact_flows_mw=new_intact,
        nodal_injections_mw=np.zeros(len(new_buses)),
    )


def prune_to_slack_component(snap: NetworkSnapshot) -> NetworkSnapshot:
    """Drop buses/branches/couplers/candidates not reachable from the slack."""
    n = len(snap.buses)
    adj: list[list[int]] = [[] for _ in range(n)]
    for br in snap.branches:
        if br.in_service:
            adj[br.from_bus].append(br.to_bus)
            adj[br.to_bus].append(br.from_bus)

    visited = [False] * n
    visited[snap.slack_bus_idx] = True
    stack = [snap.slack_bus_idx]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if not visited[v]:
                visited[v] = True
                stack.append(v)

    if all(visited):
        return snap

    old_to_new = [-1] * n
    next_idx = 0
    for i in range(n):
        if visited[i]:
            old_to_new[i] = next_idx
            next_idx += 1

    new_buses = [
        Bus(idx=old_to_new[i],
            name=snap.buses[i].name,
            substation=snap.buses[i].substation,
            v_nom_kv=snap.buses[i].v_nom_kv,
            is_busbar=snap.buses[i].is_busbar,
            in_service=True)
        for i in range(n) if visited[i]
    ]

    new_branches: list[Branch] = []
    name_to_old_idx: dict[str, int] = {}
    for br in snap.branches:
        if not (visited[br.from_bus] and visited[br.to_bus]):
            continue
        new_branches.append(Branch(
            idx=len(new_branches),
            name=br.name, cls=br.cls,
            from_bus=old_to_new[br.from_bus],
            to_bus=old_to_new[br.to_bus],
            x_pu=br.x_pu, s_rated_mva=br.s_rated_mva,
            in_service=True,
            local_to_subs=list(br.local_to_subs),
        ))
        name_to_old_idx[br.name] = br.idx

    new_couplers = [
        Coupler(name=c.name,
                bus_a=old_to_new[c.bus_a],
                bus_b=old_to_new[c.bus_b],
                closed=c.closed)
        for c in snap.couplers
        if visited[c.bus_a] and visited[c.bus_b]
    ]

    new_branch_idx_by_name = {nb.name: nb.idx for nb in new_branches}
    new_cands: list[CandidateSite] = []
    for cand in snap.candidates:
        if not visited[cand.test_bus]:
            continue
        new_locals = [
            new_branch_idx_by_name[snap.branches[k].name]
            for k in cand.local_branch_idx
            if snap.branches[k].name in new_branch_idx_by_name
        ]
        if len(new_locals) < 2:
            continue
        new_cands.append(CandidateSite(
            substation=cand.substation,
            test_bus=old_to_new[cand.test_bus],
            local_branch_idx=new_locals,
        ))

    new_intact = np.array([
        snap.intact_flows_mw[name_to_old_idx[nb.name]] for nb in new_branches
    ])

    return NetworkSnapshot(
        buses=new_buses,
        branches=new_branches,
        couplers=new_couplers,
        candidates=new_cands,
        slack_bus_idx=old_to_new[snap.slack_bus_idx],
        intact_flows_mw=new_intact,
        nodal_injections_mw=np.zeros(len(new_buses)),
    )


def with_coupler_closed(snap: NetworkSnapshot, coupler_name: str) -> NetworkSnapshot:
    """Return a new snapshot with the named coupler merged (bus_b into bus_a)."""
    coup = next(c for c in snap.couplers if c.name == coupler_name)
    a, b = coup.bus_a, coup.bus_b
    if a == b:
        return snap
    if b < a:
        a, b = b, a

    n = len(snap.buses)
    new_idx = [i if i < b else (a if i == b else i - 1) for i in range(n)]

    new_buses = [bs for k, bs in enumerate(snap.buses) if k != b]
    for k, bs in enumerate(new_buses):
        bs.idx = k

    name_to_old: dict[str, int] = {br.name: br.idx for br in snap.branches}
    new_branches: list[Branch] = []
    for br in snap.branches:
        f, t = new_idx[br.from_bus], new_idx[br.to_bus]
        if f == t:
            continue
        new_branches.append(Branch(
            idx=len(new_branches),
            name=br.name, cls=br.cls,
            from_bus=f, to_bus=t,
            x_pu=br.x_pu, s_rated_mva=br.s_rated_mva,
            in_service=br.in_service,
            local_to_subs=list(br.local_to_subs),
        ))

    new_intact = np.array([snap.intact_flows_mw[name_to_old[nb.name]]
                           for nb in new_branches])

    new_cands: list[CandidateSite] = []
    for c in snap.candidates:
        local_names = {snap.branches[k].name for k in c.local_branch_idx}
        local_new = [nb.idx for nb in new_branches if nb.name in local_names]
        new_cands.append(CandidateSite(
            substation=c.substation,
            test_bus=new_idx[c.test_bus],
            local_branch_idx=local_new,
        ))

    new_couplers = [
        Coupler(name=cc.name,
                bus_a=new_idx[cc.bus_a], bus_b=new_idx[cc.bus_b],
                closed=(cc.name == coupler_name) or cc.closed)
        for cc in snap.couplers if cc.name != coupler_name
    ]

    return NetworkSnapshot(
        buses=new_buses,
        branches=new_branches,
        couplers=new_couplers,
        candidates=new_cands,
        slack_bus_idx=new_idx[snap.slack_bus_idx],
        intact_flows_mw=new_intact,
        nodal_injections_mw=np.zeros(len(new_buses)),
    )
