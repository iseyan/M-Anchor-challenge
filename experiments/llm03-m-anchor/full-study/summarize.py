"""Summarize metrics already derived by engine replay; never call legacy score.summarize."""
from collections import defaultdict
from numbers import Real


def summarize(episodes):
    cells=defaultdict(lambda:dict(n=0,sums=defaultdict(float)))
    for e in episodes:
        for sender in ("all",e["sender"]):
            c=cells[(sender,e["condition"],e["label"])];c["n"]+=1
            for k,v in e["metrics"].items():
                if isinstance(v,Real):c["sums"][k]+=float(v)
    return [{"sender":s,"condition":c,"label":lab,"n":v["n"],"sums":dict(v["sums"]),
             "means":{k:x/v["n"] for k,x in v["sums"].items()}} for (s,c,lab),v in sorted(cells.items())]
