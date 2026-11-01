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
    assert d == c and re.fullmatch(r"[a-z0-9]+([.-][a-z0-9]+)*", d["name"]) and d["skills"] == ["./skills"]   # claude plugin validate: paths must start with ./
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
    z = zipfile.ZipFile(whl[0])
    for src in (ROOT / "packages/devmem/src/devmem").glob("*.py"):   # every module, not just cli.py: a stale module breaks the plugin quietly
        assert f"devmem/{src.name}" in names and z.read(f"devmem/{src.name}").decode().replace("\r\n", "\n") == src.read_text(encoding="utf-8"), \
            f"{src.name} differs: rebuild the wheel (uv build --wheel packages/devmem -o plugins/devmem/skills/devmem/vendor)"
    assert any(n.endswith("entry_points.txt") for n in names)


def test_skill_frontmatter_is_valid_yaml():
    """An unquoted `: ` in a description breaks YAML; Claude Code then loads the skill with no metadata and it never triggers."""
    import yaml
    for sk in (PLUG / "skills").glob("*/SKILL.md"):
        meta = yaml.safe_load(sk.read_text(encoding="utf-8").split("---")[1])
        assert meta["name"] == sk.parent.name and meta["description"], sk


def test_vendored_files_are_committed():
    """`uv build -o vendor` drops a `*` .gitignore there; anything it hides would be missing from the installed plugin."""
    tracked = set(subprocess.run(["git", "ls-files", "plugins/devmem/skills/devmem/vendor"], cwd=ROOT, capture_output=True, text=True).stdout.split())
    for f in (PLUG / "skills/devmem/vendor").iterdir():
        if f.name != ".gitignore":
            assert f"plugins/devmem/skills/devmem/vendor/{f.name}" in tracked, f"{f.name} is not in git (git add -f it): the plugin would ship without it"
