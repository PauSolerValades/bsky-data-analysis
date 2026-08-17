import json, sys, collections

path = sys.argv[1]
stats = collections.Counter()      # (collection, via_collection) -> n
no_via = collections.Counter()
example = {}

with open(path) as f:
    for line in f:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("kind") != "commit":
            continue
        c = ev.get("commit") or {}
        coll = c.get("collection", "?")
        rec = c.get("record") or {}
        via = rec.get("via")
        if via and isinstance(via, dict) and "uri" in via:
            vc = via["uri"].split("/")[3] if via["uri"].count("/") >= 4 else "?"
            stats[(coll, vc)] += 1
            example.setdefault((coll, vc), via["uri"])
        else:
            no_via[coll] += 1

print(f"== {path}")
print("\n(collection, what via points to) -> count:")
for (coll, vc), n in stats.most_common(30):
    print(f"  {coll:40s} -> {vc:25s} {n:8d}   e.g. {example[(coll, vc)]}")
print("\nrecords WITHOUT via, by collection:")
for coll, n in no_via.most_common(10):
    print(f"  {coll:40s} {n:8d}")
