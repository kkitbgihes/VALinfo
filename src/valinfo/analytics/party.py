"""Определение пати (групп игроков, играющих вместе).

Списки матчей и разобранные матчи берутся из общих `HistoryStore` / `MatchStore`: если матч уже скачали ради
статистики игрока, для пати он второй раз не качается (и наоборот).
"""
from __future__ import annotations

import base64
import json
import time
from collections import Counter, defaultdict
from itertools import combinations

from valinfo.config import PARTY_HISTORY_MATCHES, PARTY_MAX_AGE_DAYS, PARTY_MIN_SHARED


def _find_key(o, key):
    if isinstance(o, dict):
        if o.get(key):
            return o[key]
        for v in o.values():
            r = _find_key(v, key)
            if r:
                return r
    return None


def _decode_blob(s):
    if not s:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    except Exception:
        return None


def party_from_own(riot, me, subjects):
    d = riot.get(f"{riot.glz}/parties/v1/players/{me}")
    pid = (d or {}).get("CurrentPartyID")
    if not pid:
        return False, []
    party = riot.get(f"{riot.glz}/parties/v1/parties/{pid}")
    if not party:
        return False, []
    members = [m.get("Subject") for m in party.get("Members") or []]
    return True, [s for s in members if s in subjects]


def party_from_presences(local, subjects):
    by_pid = defaultdict(list)
    try:
        presences = local.get("/chat/v4/presences").get("presences", [])
    except Exception:
        return []
    for p in presences:
        s = p.get("puuid")
        if s not in subjects:
            continue
        data = _decode_blob(p.get("private"))
        if data is None and p.get("privateJwt"):
            parts = p["privateJwt"].split(".")
            data = _decode_blob(parts[1]) if len(parts) > 1 else None
        pid = _find_key(data, "partyId")
        if pid:
            by_pid[pid].append(s)
    return [g for g in by_pid.values() if len(g) >= 2]


def detect_team_parties(riot, local, me, players, team_id, matches, history, pool, on_partial=None):
    members = [p["subject"] for p in players if p["team"] == team_id]
    mset = set(members)

    parent = {s: s for s in members}
    size = {s: 1 for s in members}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb or size[ra] + size[rb] > 5:
            return
        parent[rb] = ra
        size[ra] += size[rb]

    def snapshot():
        groups = defaultdict(list)
        for s in members:
            groups[find(s)].append(s)
        return [g for g in groups.values() if len(g) >= 2]

    locked = set()
    if me in mset:
        own_known, own_members = party_from_own(riot, me, mset)
        if own_known:
            locked = set(own_members) | {me}
            for s in own_members[1:]:
                union(own_members[0], s)

    for group in party_from_presences(local, mset):
        for s in group[1:]:
            union(group[0], s)

    if on_partial:
        on_partial(snapshot())

    todo = [s for s in members if s not in locked]
    if len(todo) >= 2:
        hist = dict(zip(todo, pool.map(
            lambda s: history.entries(riot, s, None, PARTY_HISTORY_MATCHES), todo)))

        now_ms = time.time() * 1000
        max_age_ms = PARTY_MAX_AGE_DAYS * 86400 * 1000
        by_match = defaultdict(set)
        for s in todo:
            for e in hist.get(s, []):
                if max_age_ms and e.started_ms and now_ms - e.started_ms > max_age_ms:
                    continue
                by_match[e.mid].add(s)
        shared = [(mid, sorted(w)) for mid, w in by_match.items() if len(w) >= 2]

        # Качаем только общие матчи (и только те, которых ещё нет в хранилище)
        summaries = dict(zip((m for m, _ in shared), matches.get_many(riot, [m for m, _ in shared], pool)))

        hits = Counter()
        for mid, who in shared:
            pids = (summaries.get(mid).parties if summaries.get(mid) else {})
            for a, b in combinations(who, 2):
                if pids.get(a) and pids.get(a) == pids.get(b):
                    hits[(a, b)] += 1

        for (a, b), n in hits.most_common():
            if n >= PARTY_MIN_SHARED:
                union(a, b)

    return snapshot()
