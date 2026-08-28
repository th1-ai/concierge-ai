"""Shared test plumbing (factory-seeded; a repo may extend it).

Two guarantees for every agent-specific test module:

1. Tests never read the hotel's own `config/hotel.yaml` / `config/agent.yaml`
   (an edit there must never turn `make test` red) - `AGENT_CONFIG_DIR` points
   at temp copies of the shipped `.example.yaml` files.
2. Tests never write into the hotel's working copy - `AGENT_REPO_ROOT` points
   at a temp sandbox holding copies of `prompts/`, `knowledge/`, `fixtures/`
   and an empty `data/`, so a mock adapter's `data/exports/*.jsonl` or the
   SQLite store can never leave a phantom "sent" record behind.

The shared `test_core_*.py` modules manage these variables themselves, so the
fixture is a no-op for them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_repo(request, tmp_path, monkeypatch):
    module_file = os.path.basename(str(getattr(request.node, "path", "")))
    if module_file.startswith("test_core_"):
        yield
        return
    cfg_dir = tmp_path / "isolated-config"
    cfg_dir.mkdir(exist_ok=True)
    for name in ("hotel", "agent"):
        example = REPO_ROOT / "config" / f"{name}.example.yaml"
        if example.exists():
            shutil.copy(example, cfg_dir / f"{name}.yaml")
    sandbox = tmp_path / "isolated-repo"
    if not sandbox.exists():
        sandbox.mkdir()
        for name in ("prompts", "fixtures"):
            src = REPO_ROOT / name
            if src.exists():
                shutil.copytree(src, sandbox / name)
        # knowledge/ and config/: the SHIPPED state right after `make setup`
        # (examples materialised), never the hotel's own edited files - a
        # translated disclosure.md or a filled-in property.md must not change
        # what a test sees.
        for name in ("knowledge", "config"):
            src = REPO_ROOT / name
            dst = sandbox / name
            dst.mkdir()
            if not src.exists():
                continue
            for f in src.iterdir():
                if f.is_file() and (".example." in f.name or f.name == "README.md"):
                    shutil.copy(f, dst / f.name)
                    if ".example." in f.name:
                        real = dst / f.name.replace(".example.", ".")
                        if not real.exists():
                            shutil.copy(f, real)
        (sandbox / "data" / "imports").mkdir(parents=True)
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENT_REPO_ROOT", str(sandbox))
    for var in ("AGENT_MODE", "LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    yield
