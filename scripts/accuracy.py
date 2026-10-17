import json, pathlib, sys, re
from collections import Counter
from devmem_app import scip
from devmem_app.indexer import index_repo
root, scipf, gj = sys.argv[1:4]
idx = index_repo(root, "x", write=False)
syms = {s["id"]: s for s in idx["symbols"]}
docs = scip.parse(pathlib.Path(scipf).read_bytes())
oracle, covered = scip.edges_from(idx, docs)
O = {(e["src"], e["dst"]) for e in oracle}
S = {(e["src"], e["dst"]): e["conf"] for e in idx["edges"] if e["type"] in ("calls","tests") and not e["dst"].startswith("table:") and e["src"] in syms and e["dst"] in syms}
# restrict to src files SCIP covered, and non-test src (tests are noisy)
cov = lambda p: p.split("#")[0] in covered
S = {k: v for k, v in S.items() if cov(k[0]) and syms[k[0]]["kind"] != "module"}
O = {k for k in O if cov(k[0])}
def pr(name, pred):
    tp = len(pred & O)
    print(f"{name:34} edges={len(pred):5} precision={tp/max(len(pred),1):.2f} recall={tp/max(len(O),1):.2f}")
pr("devmem structural (all)", set(S))
for c in ("structural", "ambiguous"):
    pr(f"  conf={c}", {k for k, v in S.items() if v == c})
# graphify
g = json.load(open(gj))
byfile = {}
for s in idx["symbols"]:
    if s["kind"] != "module": byfile.setdefault(s["file"], []).append(s)
def contain(path, line):
    best=None
    for s in byfile.get(path, []):
        if s["line"] <= line <= s["end_line"] and (best is None or s["end_line"]-s["line"] < best["end_line"]-best["line"]): best=s
    return best
nid = {n["id"]: n for n in g["nodes"]}
def locate(n):
    f = n.get("source_file",""); m = re.match(r"L(\d+)", n.get("source_location") or "")
    return contain(f, int(m.group(1))) if m else None
G = {}
for e in g["links"]:
    if e["relation"] not in ("calls", "indirect_call"): continue
    a, b = nid.get(e["source"]), nid.get(e["target"])
    if not a or not b: continue
    sa, sb = locate(a), locate(b)
    if sa and sb and sa["id"] != sb["id"] and cov(sa["id"]) and sa["kind"] != "module": G[(sa["id"], sb["id"])] = e["confidence"]
pr("graphify calls (all)", set(G))
pr("  graphify EXTRACTED", {k for k, v in G.items() if v == "EXTRACTED"})
pr("union devmem+graphify", set(S) | set(G))
pr("intersection devmem&graphify", set(S) & set(G))
amb = {k for k, v in S.items() if v == "ambiguous"}
pr("ambiguous & graphify", amb & set(G)); pr("ambiguous - graphify", amb - set(G))
st = {k for k, v in S.items() if v == "structural"}
pr("structural & graphify", st & set(G)); pr("structural - graphify", st - set(G))
print("oracle edges", len(O), "| graphify-only precise:", len((set(G)-set(S)) & O), "| devmem-only precise:", len((set(S)-set(G)) & O))
fp = sorted(set(S) - O)[:8]
print("devmem false-positive samples:"); [print("  ", a, "->", b, S[(a,b)]) for a,b in fp]
fn = sorted(O - set(S))[:8]
print("devmem missed samples:"); [print("  ", a, "->", b) for a,b in fn]
