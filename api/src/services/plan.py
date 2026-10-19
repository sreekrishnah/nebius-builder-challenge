from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from src.lib.supabase import supabase, unwrap, unwrap_maybe
from src.services.ambitions import list_ambitions
from src.services.assumptions import list_assumptions

async def get_plan_status(user_id: str) -> Dict[str, Any]:
    """
    Computes the status of every user assumption and ambition plan,
    linking real-world evidence and challenges.
    """
    ambitions = await list_ambitions(user_id)
    active_ambitions = [a for a in ambitions if a.status != 'archived']
    
    plan_ambitions: List[Dict[str, Any]] = []
    all_challenged_dev_ids = set()
    assumptions_by_ambition: Dict[str, List[Any]] = {}

    for amb in active_ambitions:
        assumptions = await list_assumptions(user_id, amb.id)
        assumptions_by_ambition[amb.id] = assumptions
        for a in assumptions:
            if a.challenged_by_development_id:
                all_challenged_dev_ids.add(a.challenged_by_development_id)

    # Batch lookup developments and sources for challenged assumptions
    dev_map: Dict[str, Dict[str, Any]] = {}
    src_map: Dict[str, List[Dict[str, Any]]] = {}
    
    if all_challenged_dev_ids:
        dev_ids_list = list(all_challenged_dev_ids)
        try:
            dev_res = supabase.table('developments').select('id, headline, occurred_at')\
                .in_('id', dev_ids_list).execute()
            dev_rows = unwrap('plan.developments', dev_res)
            dev_rows = dev_rows if isinstance(dev_rows, list) else [dev_rows]
            for d in dev_rows:
                if d and isinstance(d, dict) and d.get('id'):
                    dev_map[d['id']] = d

            src_res = supabase.table('story_sources').select('story_id, publisher, url')\
                .limit(20).execute()
            src_rows = unwrap('plan.sources', src_res)
            src_rows = src_rows if isinstance(src_rows, list) else [src_rows]
        except Exception:
            pass

    for amb in active_ambitions:
        assumptions = assumptions_by_ambition.get(amb.id, [])
        plan_assumptions: List[Dict[str, Any]] = []

        for a in assumptions:
            if a.status in ('challenged', 'reconsider'):
                dev = dev_map.get(a.challenged_by_development_id or '')
                evidence = [
                    {
                        "developmentId": a.challenged_by_development_id or "unknown",
                        "effect": "challenges",
                        "reason": a.challenge_reason or "Recent development challenges this assumption.",
                        "reconsider": "Review this assumption against recent developments.",
                        "headline": dev.get('headline') if dev else "Challenging event observed",
                        "occurredAt": dev.get('occurred_at') if dev else (a.challenged_at or a.created_at),
                        "publisherCount": 1,
                        "counts": True,
                        "sources": [],
                    }
                ]
                timeline = [
                    {"at": a.created_at, "kind": "recorded", "text": "Assumption recorded"},
                    {"at": a.challenged_at or a.updated_at, "kind": "evidence", "effect": "challenges", "text": f"Challenged: {a.challenge_reason or 'New event observed'}"}
                ]
                state = "reconsider"
                threats = 1
            elif a.status == 'watch':
                dev = dev_map.get(a.challenged_by_development_id or '')
                evidence = [
                    {
                        "developmentId": a.challenged_by_development_id or "unknown",
                        "effect": "watch",
                        "reason": a.challenge_reason or "Recent development relates to this assumption.",
                        "reconsider": "Keep an eye on this assumption.",
                        "headline": dev.get('headline') if dev else "Relevant event observed",
                        "occurredAt": dev.get('occurred_at') if dev else (a.challenged_at or a.created_at),
                        "publisherCount": 1,
                        "counts": True,
                        "sources": [],
                    }
                ]
                timeline = [
                    {"at": a.created_at, "kind": "recorded", "text": "Assumption recorded"},
                    {"at": a.challenged_at or a.updated_at, "kind": "evidence", "effect": "watch", "text": f"Watch: {a.challenge_reason or 'New event observed'}"}
                ]
                state = "watch"
                threats = 0
            else:
                evidence = []
                timeline = [
                    {"at": a.created_at, "kind": "recorded", "text": "Assumption recorded and holding"}
                ]
                state = "stable"
                threats = 0

            plan_assumptions.append({
                "id": a.id,
                "statement": a.statement,
                "state": state,
                "threats": threats,
                "confirmations": 0,
                "opportunities": 0,
                "decidedAt": getattr(a, 'decided_at', None),
                "evidence": evidence,
                "timeline": timeline,
            })

        amb_state = "reconsider" if any(pa["state"] == "reconsider" for pa in plan_assumptions) else (
            "watch" if any(pa["state"] == "watch" for pa in plan_assumptions) else "stable"
        )

        plan_ambitions.append({
            "id": amb.id,
            "title": amb.title,
            "state": amb_state,
            "assumptions": plan_assumptions,
        })

    all_pa = [pa for a in plan_ambitions for pa in a["assumptions"]]
    stable_count = sum(1 for pa in all_pa if pa["state"] == "stable")
    watch_count = sum(1 for pa in all_pa if pa["state"] == "watch")
    reconsider_count = sum(1 for pa in all_pa if pa["state"] == "reconsider")

    totals = {
        "assumptions": len(all_pa),
        "stable": stable_count,
        "watch": watch_count,
        "reconsider": reconsider_count,
        "confirmations": 0,
        "opportunities": 0,
    }

    return {
        "ambitions": plan_ambitions,
        "totals": totals,
        "checked": stable_count,
        "setAside": 0,
    }
