"""Tests de cohérence du dépôt : fichiers de configuration valides (non-régression UNI-11)."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", sorted((ROOT / ".github" / "workflows").glob("*.yml")))
def test_github_workflows_are_valid_yaml(path):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert workflow["jobs"], path.name
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert isinstance(step.get("run", ""), str)
            uses = step.get("uses", "")
            if uses and not uses.startswith("./"):
                ref = uses.split("@")[1]
                assert len(ref) == 40, f"action non épinglée par SHA : {uses}"


@pytest.mark.parametrize("path", sorted((ROOT / "config").rglob("*.yaml")))
def test_config_yaml_files_are_valid(path):
    assert yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted((ROOT / "config" / "debezium").glob("*.json")))
def test_debezium_connectors_are_valid_json(path):
    connector = json.loads(path.read_text(encoding="utf-8"))
    assert connector["config"]["topic.prefix"].endswith(".raw")


def test_lock_files_pin_and_hash_every_dependency():
    for lock in (ROOT / "requirements").glob("*.lock"):
        text = lock.read_text(encoding="utf-8")
        pins = [line for line in text.splitlines() if "==" in line and not line.startswith("#")]
        assert pins and text.count("--hash=sha256:") >= len(pins), lock.name
