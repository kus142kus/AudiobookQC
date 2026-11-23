"""Compare automatically found candidates with a human-verified error list.

A known-error benchmark is the only way to estimate precision/recall. Synthetic
unit tests cannot measure real audiobook proofreading accuracy.
"""
from __future__ import annotations
from collections import defaultdict


def score_findings(truth: list[dict], findings: list[dict], tolerance: float = 3.0) -> dict:
    """Deterministic one-to-one temporal matching, with kind equality.

    CSV labels: start seconds, optional end seconds, kind. When annotations
    span long regions, interval distance is used rather than start alone.
    """
    if tolerance<0:raise ValueError('Negative tolerance')
    groups=defaultdict(lambda:{'tp':0,'fp':0,'fn':0})
    kinds=sorted(set(x['kind'] for x in truth+findings))
    for kind in kinds:
        actual=[x for x in truth if x['kind']==kind]
        predicted=[x for x in findings if x['kind']==kind]
        pairs=[]
        for i,a in enumerate(actual):
            a0,a1=float(a['start']),float(a.get('end',a['start']))
            for j,p in enumerate(predicted):
                b0,b1=float(p['start']),float(p.get('end',p['start']))
                gap=max(0,a0-b1,b0-a1)
                if gap<=tolerance:pairs.append((gap,abs(a0-b0),i,j))
        used_a,used_p=set(),set()
        for gap,diff,i,j in sorted(pairs):
            if i not in used_a and j not in used_p:
                used_a.add(i);used_p.add(j)
        groups[kind]={'tp':len(used_a),'fp':len(predicted)-len(used_p),
                      'fn':len(actual)-len(used_a)}
    totals={k:sum(v[k] for v in groups.values()) for k in ('tp','fp','fn')}
    def rates(q):
        precision=q['tp']/(q['tp']+q['fp']) if q['tp']+q['fp'] else None
        recall=q['tp']/(q['tp']+q['fn']) if q['tp']+q['fn'] else None
        return {**q,'precision':precision,'recall':recall}
    return {'total':rates(totals),'by_kind':{k:rates(v) for k,v in groups.items()}}
