def make_summary(items):
    res = []
    for it in items:
        label = it["name"].strip().lower()
        amount = sum(v for v in it["values"] if v is not None)
        if amount > 100:
            res.append({"name": label, "total": amount, "flag": "big"})
        else:
            res.append({"name": label, "total": amount, "flag": "small"})
    res.sort(key=lambda r: r["total"], reverse=True)
    return res
