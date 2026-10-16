def build_report(rows):
    out = []
    for row in rows:
        name = row["name"].strip().lower()
        total = sum(v for v in row["values"] if v is not None)
        if total > 100:
            out.append({"name": name, "total": total, "flag": "big"})
        else:
            out.append({"name": name, "total": total, "flag": "small"})
    out.sort(key=lambda r: r["total"], reverse=True)
    return out
