"""Runs the A2UI project's own v1.0 test cases against our validator.

The cases in upstream_cases/ are copied unchanged from a2ui-project/a2ui,
specification/v1_0/test/cases (Apache 2.0). Only those that test the agent to
renderer messages are included, since those are what this package validates.
"""

import json
from pathlib import Path

import pytest

from a2ui_free.validate import schema_valid

HERE = Path(__file__).parent / "upstream_cases"
_decoder = json.JSONDecoder()


def cases():
    for file in sorted(HERE.glob("*.json")):
        if file.name == "testing_catalog.json":
            continue
        doc, _ = _decoder.raw_decode(file.read_text(encoding="utf-8"))
        for i, test in enumerate(doc["tests"]):
            yield pytest.param(doc, test, id=f"{file.stem}-{i}")


@pytest.mark.parametrize("doc,test", list(cases()))
def test_upstream_case(doc, test):
    catalog = doc.get("catalog")
    if isinstance(catalog, str):
        catalog = json.loads((HERE / catalog).read_text(encoding="utf-8"))
    assert schema_valid(test["data"], catalog) is test["valid"], test.get("description")
