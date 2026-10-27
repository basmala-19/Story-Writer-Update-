import copy
import json
from pathlib import Path

from story_service.pipeline.models import AgeSpec, Concepts
from story_service.validators.script import validate_raw_script

FIXTURES = Path(__file__).parent / "fixtures"


def load_inputs(name: str = "golden") -> dict:
    return json.loads((FIXTURES / "s4" / f"{name}_inputs.json").read_text(encoding="utf-8"))


def load_llm_fixture(name: str) -> dict:
    return json.loads((FIXTURES / "llm" / name).read_text(encoding="utf-8"))


def golden_script() -> dict:
    return copy.deepcopy(load_llm_fixture("s4_script.json"))


def validate(raw: dict, inputs: dict | None = None):
    inp = inputs or load_inputs("golden")
    return validate_raw_script(
        raw,
        concepts=Concepts.model_validate(inp["concepts"]),
        age_spec=AgeSpec.model_validate(inp["age_spec"]),
        language=inp["intake"]["language"],
        wps=inp["wps"],
    )


def failed_names(result: dict) -> set[str]:
    return {c["name"] for c in result["checks"] if not c["passed"] and c["severity"] != "minor"}


def scene(raw: dict, sid: str) -> dict:
    return next(s for s in raw["scenes"] if s["id"] == sid)


def line(raw: dict, lid: str) -> dict:
    return next(l for s in raw["scenes"] for l in s["lines"] if l["id"] == lid)
