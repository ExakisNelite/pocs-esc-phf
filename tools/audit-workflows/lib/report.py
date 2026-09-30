import csv
import io
import json
import os
import re
from pathlib import Path

from .collect import identity
from .github import key, local_path, read_json, write_text


def display(value) -> str:
    if value is None:
        return "unknown"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)


def text(value) -> str:
    escaped = re.sub(r"([\\`*{}\[\]()#+.!_|<>])", r"\\\1", display(value))
    return re.sub(r"\r?\n", "<br>", escaped)


def table(headings: list, rows: list) -> str:
    return "\n".join(["| " + " | ".join(headings) + " |", "| " + " | ".join("---" for heading in headings) + " |",
                      *("| " + " | ".join(text(value) for value in row) + " |" for row in rows)])


def csv_text(rows: list) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for row in rows:
        cells = ["" if value is None else display(value) for value in row]
        writer.writerow(["'" + cell if re.match(r"^[=+\-@\t\r\n]", cell) else cell for cell in cells])
    return output.getvalue()


def observations(workflow: dict) -> list:
    findings = []
    for scope in [{"id": "workflow", "permissions": workflow.get("permissions")}, *workflow["jobs"]]:
        permissions = scope.get("permissions")
        if permissions == "write-all":
            findings.append(f"{scope['id']}: write-all permissions; review least privilege")
        if isinstance(permissions, dict):
            writes = [name for name, permission in permissions.items() if permission == "write"]
            if writes:
                findings.append(f"{scope['id']}: write permissions ({', '.join(writes)}); review justification")
    for action in workflow["actions"]:
        reference = action["uses"]
        if not isinstance(reference, str) or (not reference.startswith(("./", "docker://")) and not re.search(r"@[a-f0-9]{40}$", reference, re.IGNORECASE)):
            findings.append(f"{action['jobId']}: action is not pinned to a full commit SHA ({reference})")
    behavior = json.dumps({name: workflow.get(name) for name in ("jobs", "concurrency", "defaults", "permissions")})
    for specification in workflow["inputs"]:
        name = specification["name"]
        if not re.search(r"inputs\." + re.escape(name) + r"\b|inputs\[['\"]" + re.escape(name) + r"['\"]\]", behavior):
            findings.append(f"Input {name}: no literal use detected; indirect access still needs review")
    for output in workflow["outputs"]:
        match = re.fullmatch(r"\$\{\{\s*jobs\.([\w-]+)\.outputs\.([\w-]+)\s*\}\}", output.get("value") or "")
        if match:
            job = next((candidate for candidate in workflow["jobs"] if candidate["id"] == match[1]), None)
            if not job or (not job.get("uses") and match[2] not in job.get("outputs", {})):
                findings.append(f"Output {output['name']}: referenced job/output is absent")
    for job in workflow["jobs"]:
        for name, value in job.get("outputs", {}).items():
            match = re.fullmatch(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*\}\}", value if isinstance(value, str) else "")
            if match and not any(step.get("id") == match[1] for step in job.get("steps", [])):
                findings.append(f"{job['id']}.{name}: referenced step is absent")
    return findings


def generate_reports(directory: Path, audit: dict) -> dict:
    directory = local_path(directory)
    library = audit["config"]["library"].lower()
    roots = set(audit["rootIdentities"])
    workflows = [workflow for workflow in audit["nodes"] if workflow["repository"].lower() == library and workflow["reusable"]]
    filenames = {identity(workflow): re.sub(r"[^a-zA-Z0-9._-]", "_", workflow["path"].split("/")[-1]) + "-" + key(identity(workflow))[:16] + ".md" for workflow in workflows}
    library_edges = [edge for edge in audit["edges"] if (edge["reference"].get("repository") or "").lower() == library]
    warnings = ["Static references do not prove execution. Functional descriptions and secret availability require human review.",
                "Caller coverage is limited to default-branch snapshots. Called versions may be branches, tags or SHAs.",
                "All generated files are private working material. Raw evidence and audit JSON may contain hard-coded secrets."]
    if not audit["inventory"]["expectedInventoryProvided"]:
        warnings.append("No independent inventory: organization-wide completeness is unknown.")
    if audit["inventory"]["organizationError"]:
        warnings.append("Organization listing failed; installation-visible repositories were still scanned.")
    if not any(repo["full_name"].lower() == library and repo["discovery"] == "visible" for repo in audit["inventory"]["repositories"]):
        warnings.append("Library default branch is not visible in inventory; its current workflow catalog is unverified.")
    if audit["truncated"]:
        warnings.append("Dependency graph reached its edge limit; some routes are omitted.")
    repository_by_id = {repo["id"]: repo for repo in audit["inventory"]["repositories"]}
    coverage_rows = []
    for row in audit["coverage"]:
        repo = repository_by_id[row["id"]]
        coverage_rows.append([row["repository"], repo.get("visibility"), repo.get("archived"), repo.get("fork"), row["status"], row["files"],
                              row.get("referenceCount"), "; ".join(f"{error.get('path', '')}: {error['reason']}" for error in row["errors"])])
    write_text(directory / "reports" / "coverage.md", "\n".join(["# Audit coverage", "", f"Generated: {text(audit['generatedAt'])}", "",
               f"Organization: {text(audit['organizationCoverage'])}", "", f"Dependencies: {text(audit['dependencyCoverage'])}", "",
               *("- " + warning for warning in warnings), "", table(["Repository", "Visibility", "Archived", "Fork", "Status", "Files", "References", "Errors"], coverage_rows), ""]))
    reports = {
        "coverage.csv": [["repository", "status", "commit_sha", "files", "references", "references_found_in_readable_files", "errors"],
                         *([row["repository"], row["status"], row.get("commitSha"), row["files"], row.get("referenceCount"), row.get("referencesFound"), "; ".join(error["reason"] for error in row["errors"])] for row in audit["coverage"])],
        "inaccessible-repositories.csv": [["repository", "status", "reason"], *([row["repository"], row["status"], "; ".join(error["reason"] for error in row["errors"])] for row in audit["coverage"] if row["status"] not in ("complete", "no_workflows", "empty"))],
        "consumer-references.csv": [["caller", "job", "reference", "depth", "status", "target", "resolved_kind", "route"], *([edge["caller"], edge["jobId"], edge["raw"], edge["depth"], edge["status"], edge["target"], edge.get("resolution", {}).get("kind"), " -> ".join(edge["route"])] for edge in library_edges)],
        "unresolved-references.csv": [["caller", "job", "reference", "status", "reason", "route"], *([edge["caller"], edge["jobId"], edge["raw"], edge["status"], edge.get("error", {}).get("reason"), " -> ".join(edge["route"])] for edge in audit["edges"] if edge["status"] != "resolved")],
        "validation-findings.csv": [["caller", "job", "target", "status", "findings", "unknowns"], *([edge["caller"], edge["jobId"], edge["target"], edge["validation"]["status"], "; ".join(edge["validation"]["findings"]), "; ".join(edge["validation"].get("unknowns", []))] for edge in audit["edges"] if edge.get("validation"))],
        "security-observations.csv": [["workflow", "observation"], *([identity(workflow), finding] for workflow in workflows for finding in observations(workflow))]
    }
    for filename, rows in reports.items():
        write_text(directory / "reports" / filename, csv_text(rows))
    for workflow in workflows:
        workflow_id = identity(workflow)
        callers = [edge for edge in library_edges if edge["target"] == workflow_id]
        dependencies = [edge for edge in audit["edges"] if edge["caller"] == workflow_id]
        findings = observations(workflow)
        document = [f"# {text(workflow['name'])}", "", f"Identity: {text(workflow_id)}", "",
                    "Catalog: " + ("default branch snapshot" if workflow_id in roots else "consumed historical or alternate version"), "",
                    "## Purpose", "", "To be validated by a reviewer using the private source evidence. No purpose is inferred from the filename.", "",
                    f"Evidence: {text(workflow['evidence'])}", "", "## Inputs", "",
                    table(["Name", "Type", "Required", "Default declared", "Default", "Description"], [[spec["name"], spec["type"], spec["required"], spec["defaultDeclared"], "[string value omitted]" if isinstance(spec["default"], str) else display(spec["default"]), spec.get("description")] for spec in workflow["inputs"]]), "",
                    "## Secrets", "", table(["Name", "Required", "Description"], [[secret["name"], secret["required"], secret.get("description")] for secret in workflow["secrets"]]), "",
                    "Secret mappings and inherit do not prove availability. Environment secrets and token permissions need separate review.", "",
                    "## Outputs", "", table(["Name", "Description", "Expression"], [[output["name"], output.get("description"), output.get("value")] for output in workflow["outputs"]]), "",
                    "## Jobs", "", f"Workflow permissions: {text(workflow.get('permissions'))}", "",
                    table(["Job", "Runner", "Needs", "Condition", "Environment", "Permissions", "Steps", "Shell steps"],
                          [[job["id"], job.get("runs-on"), job.get("needs"), job.get("if"), job.get("environment", {}).get("name") if isinstance(job.get("environment"), dict) else job.get("environment"), job.get("permissions"), len(job.get("steps", [])), sum("run" in step for step in job.get("steps", []))] for job in workflow["jobs"]]), "",
                    "Commands and parameter values are intentionally omitted here. Review them in private evidence.", "",
                    "## Actions", "", table(["Job", "Action"], [[action["jobId"], action["uses"]] for action in workflow["actions"]]), "",
                    "## Nested workflows", "", table(["Job", "Reference", "Target", "Status"], [[edge["jobId"], edge["raw"], edge["target"], edge["status"]] for edge in dependencies]), "",
                    "## Consumers", "", table(["Caller", "Job", "Ref", "Depth", "Route", "Contract check"], [[edge["caller"], edge["jobId"], edge["reference"]["ref"], edge["depth"], " -> ".join(edge["route"]), edge.get("validation", {}).get("status")] for edge in callers]), "",
                    "## Observations", "", *("- " + text(finding) for finding in findings)]
        if not findings:
            document.append("No heuristic findings. This is not a security approval.")
        document.extend(["", "## Manual review", "", "- [ ] Purpose, effects, external systems and prerequisites validated.",
                         "- [ ] Commands, local/composite actions and scripts reviewed in private source.",
                         "- [ ] Permissions, environments, secrets and organization access policies verified.",
                         "- [ ] Conditions, matrices, outputs and failure handling checked.",
                         "- [ ] Coverage gaps, unresolved calls and contract findings addressed.", ""])
        write_text(directory / "docs" / "workflows" / filenames[workflow_id], "\n".join(document))
    write_text(directory / "docs" / "index.md", "\n".join(["# Reusable workflow audit", "", f"Library: {text(audit['config']['library'])}", "",
               f"Organization coverage: {text(audit['organizationCoverage'])}. Dependency coverage: {text(audit['dependencyCoverage'])}.", "",
               "[Coverage](../reports/coverage.md)", "", *("- " + warning for warning in warnings), "",
               *(f"- [{text(workflow['path'])} @ {workflow['commitSha'][:12]}](workflows/{filenames[identity(workflow)]})" for workflow in workflows), "",
               "Files are regenerated. Keep reviewer decisions separately in review-notes.csv.", ""]))
    try:
        with os.fdopen(os.open(directory / "review-notes.csv", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8", newline="") as output:
            output.write(csv_text([["workflow", "purpose", "effects", "prerequisites", "reviewer", "decision", "notes"],
                                   *([identity(workflow), "", "", "", "", "pending", ""] for workflow in workflows)]))
    except FileExistsError:
        pass
    return {"workflowDocuments": len(workflows), "libraryReferences": len(library_edges)}


def report_from_disk(directory: Path) -> dict:
    audit = read_json(Path(directory) / "data" / "audit.json")
    if audit is None:
        raise ValueError("No collected audit.json. Run or resume collection first.")
    return generate_reports(directory, audit)