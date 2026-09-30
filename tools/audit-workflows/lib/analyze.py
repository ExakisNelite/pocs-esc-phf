import math
import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.events import AliasEvent, DocumentStartEvent


def _plain_values(value: Any, ancestors: set[int], budget: list[int]) -> Any:
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("YAML expansion exceeds the node limit")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (dict, list)):
        identifier = id(value)
        if identifier in ancestors:
            raise ValueError("Recursive YAML aliases are unsupported")
        ancestors.add(identifier)
        try:
            if isinstance(value, dict):
                if not all(isinstance(name, str) for name in value):
                    raise ValueError("Workflow mapping keys must be strings")
                return {name: _plain_values(content, ancestors, budget) for name, content in value.items()}
            return [_plain_values(content, ancestors, budget) for content in value]
        finally:
            ancestors.remove(identifier)
    raise ValueError("Workflow values must be finite and JSON-compatible")


def parse_reference(value: Any, repository: str | None = None, commit_sha: str | None = None) -> dict:
    if not isinstance(value, str) or "${{" in value:
        return {"raw": value, "status": "invalid_syntax"}
    local = re.fullmatch(r"[.$]/\.github/workflows/([^/@]+\.ya?ml)", value)
    if local:
        return {"raw": value, "status": "valid", "repository": repository,
                "path": f".github/workflows/{local[1]}", "ref": commit_sha, "local": True}
    external = re.fullmatch(r"([^/]+/[^/]+)/(\.github/workflows/[^/@]+\.ya?ml)@(.+)", value)
    if not external or external[3].startswith("refs/"):
        return {"raw": value, "status": "invalid_syntax"}
    return {"raw": value, "status": "valid", "repository": external[1],
            "path": external[2], "ref": external[3], "local": False}


def analyze_workflow(source: str, provenance: dict) -> dict:
    parser = YAML(typ="safe", pure=True)
    parser.version = (1, 2)
    parser.allow_duplicate_keys = False
    try:
        aliases = 0
        document_count = 0
        for event in parser.parse(source):
            if isinstance(event, DocumentStartEvent):
                document_count += 1
                if event.version not in (None, (1, 2)):
                    raise ValueError("YAML 1.2 is required")
            if isinstance(event, AliasEvent):
                aliases += 1
                if aliases > 100:
                    raise ValueError("Too many YAML aliases")
        if document_count != 1:
            raise ValueError("Exactly one YAML document is required")
        document = _plain_values(parser.load(source), set(), [100_000])
    except Exception as error:
        raise ValueError("Invalid YAML, duplicate keys or multiple documents") from error
    if not isinstance(document, dict):
        raise ValueError("Workflow must be a mapping")
    trigger = document.get("on")
    if not isinstance(trigger, (str, dict, list)):
        raise ValueError("Workflow trigger is missing or invalid")
    jobs = document.get("jobs", {})
    if not isinstance(jobs, dict):
        raise ValueError("jobs must be a mapping")
    for job in jobs.values():
        if not isinstance(job, dict):
            raise ValueError("Each job must be a mapping")
        if "steps" in job and (not isinstance(job["steps"], list) or
                               not all(isinstance(step, dict) for step in job["steps"])):
            raise ValueError("steps must be a list of mappings")
        if "with" in job and not isinstance(job["with"], dict):
            raise ValueError("Job with must be a mapping")
        if "secrets" in job and job["secrets"] != "inherit" and not isinstance(job["secrets"], dict):
            raise ValueError("Job secrets must be a mapping or inherit")
    reusable = "workflow_call" in trigger if isinstance(trigger, (dict, list)) else trigger == "workflow_call"
    call = trigger.get("workflow_call") if isinstance(trigger, dict) else None
    call = call if isinstance(call, dict) else {}
    inputs = []
    for name, specification in (call.get("inputs") or {}).items():
        spec = specification if isinstance(specification, dict) else {}
        inputs.append({"name": name, "type": spec.get("type"), "required": spec.get("required") is True,
                       "description": spec.get("description"), "defaultDeclared": "default" in spec,
                       "default": spec.get("default", {"boolean": False, "number": 0, "string": ""}.get(spec.get("type")))})
    secrets = [{"name": name, "required": (spec or {}).get("required") is True,
                "description": (spec or {}).get("description")} for name, spec in (call.get("secrets") or {}).items()]
    outputs = [{"name": name, "description": (spec or {}).get("description"),
                "value": (spec or {}).get("value")} for name, spec in (call.get("outputs") or {}).items()]
    job_records = [{**job, "id": name} for name, job in jobs.items()]
    references = [{**provenance, "jobId": job["id"],
                   "reference": parse_reference(job["uses"], provenance["repository"], provenance["commitSha"]),
                   "with": job.get("with", {}), "secrets": job.get("secrets", {}),
                   "permissions": job.get("permissions"), "condition": job.get("if"),
                   "strategy": job.get("strategy")} for job in job_records if "uses" in job]
    return {**provenance, "name": document.get("name", provenance["path"]), "reusable": reusable,
            "triggers": trigger, "inputs": inputs, "secrets": secrets, "outputs": outputs,
            "permissions": document.get("permissions"), "concurrency": document.get("concurrency"),
            "defaults": document.get("defaults"), "jobs": job_records, "references": references,
            "actions": [{"jobId": job["id"], "uses": step["uses"]} for job in job_records
                        for step in job.get("steps", []) if step.get("uses")]}


def validate_call(call: dict, target: dict) -> dict:
    if not target["reusable"]:
        return {"status": "invalid", "findings": ["Target has no workflow_call"], "unknowns": []}
    supplied = call.get("with", {})
    findings = []
    unknowns = []
    for specification in target["inputs"]:
        name = specification["name"]
        if name not in supplied:
            if specification["required"]:
                findings.append(f"Missing required input: {name}")
            continue
        value = supplied[name]
        if isinstance(value, str) and "${{" in value:
            unknowns.append(f"Dynamic input: {name}")
        else:
            valid = {"boolean": type(value) is bool, "number": type(value) in (int, float),
                     "string": type(value) is str}.get(specification["type"], False)
            if not valid:
                findings.append(f"Wrong input type: {name}")
    for name in supplied:
        if not any(specification["name"] == name for specification in target["inputs"]):
            findings.append(f"Unknown input: {name}")
    secrets = call.get("secrets", {})
    if secrets == "inherit":
        unknowns.append("Inherited secret availability is not verified")
    else:
        for secret in target["secrets"]:
            if secret["required"] and secret["name"] not in secrets:
                findings.append(f"Missing secret mapping: {secret['name']}")
        for name in secrets:
            if not any(secret["name"] == name for secret in target["secrets"]):
                findings.append(f"Unknown secret mapping: {name}")
        if secrets:
            unknowns.append("Mapped secret availability is not verified")
    return {"status": "invalid" if findings else "needs_review" if unknowns else "static_checks_passed",
            "findings": findings, "unknowns": unknowns}