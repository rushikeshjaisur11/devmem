import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLUG = ROOT / "plugins/devmem"


def test_manifests_and_marketplace():
    d = json.loads((PLUG / ".devin-plugin/plugin.json").read_text())
    c = json.loads((PLUG / ".claude-plugin/plugin.json").read_text())
    assert d == c and re.fullmatch(r"[a-z0-9]+([.-][a-z0-9]+)*", d["name"]) and d["skills"] == ["skills"]
    m = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert m["plugins"][0]["source"] == "./plugins/devmem" and (ROOT / m["plugins"][0]["source"]).is_dir()


def test_skills_have_frontmatter_and_valid_cli_references():
    cli = (ROOT / "packages/devmem/src/devmem/cli.py").read_text()
    for sk in (PLUG / "skills").glob("*/SKILL.md"):
        text = sk.read_text()
        fm = re.match(r"---\nname: ([\w-]+)\ndescription: (.+)\n---\n", text)
        assert fm and fm.group(1) == sk.parent.name and len(fm.group(2)) < 400
        for cmd in set(re.findall(r"`devmem ([a-z-]+)", text)):
            assert f'"{cmd}"' in cli, f"{sk.parent.name} mentions unknown command devmem {cmd}"


def test_bundled_wheel_matches_package_and_bootstrap_installs_it(tmp_path):
    whl = list((PLUG / "skills/devmem/vendor").glob("devmem-*.whl"))
    assert len(whl) == 1
    ver = re.search(r'version = "([^"]+)"', (ROOT / "packages/devmem/pyproject.toml").read_text()).group(1)
    assert whl[0].name.startswith(f"devmem-{ver}-")
    names = zipfile.ZipFile(whl[0]).namelist()
    cli_in_wheel = zipfile.ZipFile(whl[0]).read("devmem/cli.py").decode()
    assert cli_in_wheel == (ROOT / "packages/devmem/src/devmem/cli.py").read_text(), "rebuild the wheel: pip wheel --no-deps packages/devmem -w plugins/devmem/skills/devmem/vendor"
    assert any(n.endswith("entry_points.txt") for n in names)
