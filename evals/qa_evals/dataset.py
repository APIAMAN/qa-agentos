"""Golden dataset loading."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List

import yaml

EVALS_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = EVALS_DIR.parent
DATASETS_DIR = EVALS_DIR / "datasets"


@dataclass(frozen=True)
class Case:
    id: str
    log_path: pathlib.Path
    note: str
    expected: Dict[str, Any]

    @property
    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class Dataset:
    name: str
    version: str
    parser_version: str
    root: pathlib.Path
    cases: List[Case]
    duplicates: List[Dict[str, str]]
    distinct: List[Dict[str, str]]

    def case(self, case_id: str) -> Case:
        return next(c for c in self.cases if c.id == case_id)


def load_dataset(name: str = "bug-analyzer", version: str = "v1") -> Dataset:
    root = DATASETS_DIR / name / version
    doc = yaml.safe_load((root / "dataset.yaml").read_text(encoding="utf-8"))
    return Dataset(
        name=doc["dataset"],
        version=doc["version"],
        parser_version=doc["parser_version"],
        root=root,
        cases=[Case(c["id"], root / c["log"], c["note"], c["expected"]) for c in doc["cases"]],
        duplicates=list(doc.get("duplicates") or []),
        distinct=list(doc.get("distinct") or []),
    )


def load_outputs(path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    """Agent outputs keyed by case id (``reference_outputs.json`` or a recorded live run)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return dict(doc["outputs"])
