import base64
import hashlib
import os
import re
from collections import deque
from pathlib import Path
from urllib.parse import quote

from .analyze import analyze_workflow, validate_call
from .github import ApiError, key, local_path, read_json, timestamp, write_json


def encode(value: str) -> str:
    return quote(value, safe="")


def repository_path(repository: str) -> str:
    return "/".join(encode(part) for part in repository.split("/"))


def identity(workflow: dict) -> str:
    return f"{workflow['repository'].lower()}/{workflow['path']}@{workflow['commitSha']}"


def failure(error: Exception) -> dict:
    return {"status": getattr(error, "status", None), "reason": getattr(error, "reason", str(error))}


def reconcile(visible: list, expected: list | None, org: str) -> list:
    selected = [repo for repo in visible if repo.get("owner", {}).get("login", "").lower() == org.lower()]
    by_id = {repo["id"]: {**repo, "discovery": "visible"} for repo in selected}
    if expected is not None:
        identifiers = set()
        for repo in expected:
            if not isinstance(repo, dict) or type(repo.get("id")) is not int or not isinstance(repo.get("full_name"), str) or len(repo["full_name"].split("/")) != 2 or repo["full_name"].split("/")[0].lower() != org.lower() or repo["id"] in identifiers:
                raise ValueError("Expected inventory requires unique integer id and org/repository full_name")
            identifiers.add(repo["id"])
            if repo["id"] not in by_id:
                by_id[repo["id"]] = {**repo, "discovery": "not_visible_to_token"}
    return sorted(by_id.values(), key=lambda repo: repo["full_name"].lower())


class Collector:
    def __init__(self, *, client, directory: Path, config: dict, progress=lambda message: None):
        self.client = client
        self.directory = local_path(directory)
        self.config = config
        self.progress = progress
        self.contracts = {}
        self.blob_lists = {}

    def body(self, endpoint: str):
        return self.client.get(endpoint)["body"]

    def inventory(self, expected: list | None) -> dict:
        destination = self.directory / "data" / "inventory.json"
        saved = read_json(destination)
        if saved is not None:
            return saved
        auth_mode = self.config.get("authMode", "app")
        user_login = None
        organization = []
        organization_error = None
        if auth_mode == "user":
            user = self.client.get("/user", cache=False)["body"]
            if not isinstance(user, dict) or not isinstance(user.get("login"), str) or not user["login"]:
                raise ValueError("Expected an authenticated GitHub user")
            user_login = user["login"]
            installation = []
            organization = self.client.paginate(f"/orgs/{encode(self.config['org'])}/repos?type=all&sort=full_name&direction=asc")
        else:
            installation = self.client.paginate("/installation/repositories", "repositories")
            try:
                organization = self.client.paginate(f"/orgs/{encode(self.config['org'])}/repos?type=all&sort=full_name&direction=asc")
            except Exception as error:
                organization_error = failure(error)
        repositories = {repo["id"]: repo for repo in [*organization, *installation]}
        result = {"collectedAt": timestamp(), "expectedInventoryProvided": expected is not None,
                  "authMode": auth_mode, "userLogin": user_login,
                  "organizationError": organization_error, "repositories": reconcile(list(repositories.values()), expected, self.config["org"]),
                  "expectedIds": [repo["id"] for repo in expected] if expected is not None else None}
        write_json(destination, result)
        return result

    def blobs(self, repository: str, commit_sha: str) -> list:
        cache_key = f"{repository.lower()}@{commit_sha}"
        if cache_key in self.blob_lists:
            return self.blob_lists[cache_key]
        prefix = f"/repos/{repository_path(repository)}/git"
        commit = self.body(f"{prefix}/commits/{encode(commit_sha)}")
        if commit.get("sha") != commit_sha or not commit.get("tree", {}).get("sha"):
            raise ValueError("Commit identity mismatch")
        tree_sha = commit["tree"]["sha"]
        for segment in (".github", "workflows"):
            tree = self.body(f"{prefix}/trees/{tree_sha}")
            if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
                raise ValueError("Incomplete Git tree")
            child = next((entry for entry in tree["tree"] if entry["path"] == segment), None)
            if child is None:
                self.blob_lists[cache_key] = []
                return []
            if child.get("type") != "tree":
                raise ValueError("Workflow directory is not a tree")
            tree_sha = child["sha"]
        tree = self.body(f"{prefix}/trees/{tree_sha}")
        if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
            raise ValueError("Incomplete workflow tree")
        blobs = [{**entry, "path": f".github/workflows/{entry['path']}"} for entry in tree["tree"] if re.search(r"\.ya?ml$", entry["path"])]
        self.blob_lists[cache_key] = blobs
        return blobs

    def workflow(self, repository: str, commit_sha: str, blob: dict) -> dict:
        if blob.get("type") != "blob" or blob.get("mode") not in ("100644", "100755"):
            raise ValueError("Unsupported workflow file mode (including symlink)")
        if blob.get("size", 0) > self.config["maxFileBytes"]:
            raise ValueError("Workflow exceeds size limit")
        if not re.fullmatch(r"[a-f0-9]{40}", blob["sha"], re.IGNORECASE):
            raise ValueError("Invalid Git blob SHA")
        destination = self.directory / "data" / "raw" / key(repository.lower()) / commit_sha / f"{blob['sha']}.yml"
        try:
            content = destination.read_bytes()
        except FileNotFoundError:
            response = self.body(f"/repos/{repository_path(repository)}/git/blobs/{blob['sha']}")
            if response.get("encoding") != "base64" or response.get("sha") != blob["sha"]:
                raise ValueError("Invalid Git blob response")
            content = base64.b64decode(response["content"])
            if len(content) > self.config["maxFileBytes"] or len(content) != response.get("size"):
                raise ValueError("Blob size mismatch or limit exceeded")
            actual = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
            if actual != blob["sha"]:
                raise ValueError("Git blob integrity check failed")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as output:
                output.write(content)
        if len(content) > self.config["maxFileBytes"] or hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest() != blob["sha"]:
            raise ValueError("Saved Git blob integrity check failed")
        return analyze_workflow(content.decode("utf-8"), {"repository": repository, "path": blob["path"], "commitSha": commit_sha,
                                "blobSha": blob["sha"], "evidence": destination.relative_to(self.directory).as_posix()})

    def scan(self, repo: dict) -> dict:
        checkpoint = self.directory / "data" / "repositories" / f"{repo['id']}.json"
        saved = read_json(checkpoint)
        if saved and saved["coverage"]["status"] in ("complete", "no_workflows", "empty"):
            return saved
        result = {"repository": repo["full_name"], "id": repo["id"], "snapshot": saved.get("snapshot") if saved else None, "workflows": [],
                  "coverage": {"repository": repo["full_name"], "id": repo["id"], "status": "partial", "referenceCount": None, "files": 0, "errors": []}}
        coverage = result["coverage"]
        if repo.get("discovery") == "not_visible_to_token":
            coverage["status"] = "not_visible_to_token"
            write_json(checkpoint, result)
            return result
        try:
            if not result["snapshot"]:
                if not repo.get("default_branch"):
                    raise ValueError("No default branch: empty or inaccessible, not confirmed empty")
                commit = self.body(f"/repos/{repository_path(repo['full_name'])}/commits/{encode(repo['default_branch'])}")
                if not re.fullmatch(r"[a-f0-9]{40}", commit.get("sha", ""), re.IGNORECASE):
                    raise ValueError("Invalid default branch commit SHA")
                result["snapshot"] = {"commitSha": commit["sha"], "branch": repo["default_branch"], "at": timestamp()}
                write_json(checkpoint, result)
            blobs = self.blobs(repo["full_name"], result["snapshot"]["commitSha"])
            coverage["files"] = len(blobs)
            for blob in blobs:
                try:
                    result["workflows"].append(self.workflow(repo["full_name"], result["snapshot"]["commitSha"], blob))
                except Exception as error:
                    coverage["errors"].append({"path": blob["path"], **failure(error)})
            coverage["referencesFound"] = sum(len(workflow["references"]) for workflow in result["workflows"])
            coverage["commitSha"] = result["snapshot"]["commitSha"]
            if not coverage["errors"]:
                coverage["status"] = "complete" if blobs else "no_workflows"
                coverage["referenceCount"] = coverage["referencesFound"]
        except Exception as error:
            if isinstance(error, ApiError) and error.reason == "confirmed_empty_repository":
                coverage.update(status="empty", referenceCount=0, referencesFound=0)
            else:
                coverage["status"] = "inaccessible" if isinstance(error, ApiError) and error.status in (401, 403, 404) else "partial"
                coverage["errors"].append(failure(error))
        write_json(checkpoint, result)
        return result

    def resolve(self, reference: dict) -> dict:
        destination = self.directory / "data" / "resolved" / f"{key(reference['repository'].lower() + '@' + reference['ref'])}.json"
        saved = read_json(destination)
        if saved is not None:
            return saved
        prefix = f"/repos/{repository_path(reference['repository'])}/git"
        if reference.get("local"):
            object_record = {"type": "commit", "sha": reference["ref"]}
            kind = "local"
        else:
            try:
                object_record = self.body(f"{prefix}/ref/tags/{encode(reference['ref'])}")["object"]
                kind = "tag"
            except ApiError as error:
                if error.status != 404:
                    raise
                try:
                    object_record = self.body(f"{prefix}/ref/heads/{encode(reference['ref'])}")["object"]
                    kind = "branch"
                except ApiError as branch_error:
                    if branch_error.status != 404 or not re.fullmatch(r"[a-f0-9]{7,40}", reference["ref"], re.IGNORECASE):
                        raise
                    commit = self.body(f"{prefix}/commits/{encode(reference['ref'])}")
                    object_record = {"type": "commit", "sha": commit["sha"]}
                    kind = "sha"
        seen = set()
        while object_record.get("type") == "tag":
            if object_record["sha"] in seen or len(seen) >= 16:
                raise ValueError("Annotated tag cycle or depth limit")
            seen.add(object_record["sha"])
            object_record = self.body(f"{prefix}/tags/{object_record['sha']}")["object"]
        if object_record.get("type") != "commit" or not re.fullmatch(r"[a-f0-9]{40}", object_record.get("sha", ""), re.IGNORECASE):
            raise ValueError("Reference does not resolve to a commit")
        commit = self.body(f"{prefix}/commits/{object_record['sha']}")
        if commit.get("sha") != object_record["sha"]:
            raise ValueError("Resolved commit identity mismatch")
        result = {"repository": reference["repository"], "ref": reference["ref"], "commitSha": object_record["sha"], "kind": kind, "resolvedAt": timestamp()}
        write_json(destination, result)
        return result

    def target(self, reference: dict) -> dict:
        resolution = self.resolve(reference)
        target_identity = f"{reference['repository'].lower()}/{reference['path']}@{resolution['commitSha']}"
        if target_identity in self.contracts:
            return {"resolution": resolution, "workflow": self.contracts[target_identity]}
        blobs = self.blobs(reference["repository"], resolution["commitSha"])
        blob = next((entry for entry in blobs if entry["path"] == reference["path"]), None)
        if blob is None:
            raise ValueError("Workflow path absent at resolved commit")
        workflow = self.workflow(reference["repository"], resolution["commitSha"], blob)
        self.contracts[target_identity] = workflow
        return {"resolution": resolution, "workflow": workflow}

    def graph(self, roots: list) -> dict:
        edges = []
        nodes = {identity(workflow): workflow for workflow in roots}
        queue = deque((workflow, [identity(workflow)], 0) for workflow in roots)
        while queue:
            workflow, route, depth = queue.popleft()
            for call in workflow["references"]:
                edge = {"caller": identity(workflow), "jobId": call["jobId"], "raw": call["reference"]["raw"], "reference": call["reference"],
                        "route": route, "depth": depth + 1, "status": "unresolved", "target": None}
                if len(edges) >= self.config["maxEdges"]:
                    edges.append({**edge, "status": "limit_reached", "error": {"reason": "Graph edge limit; remaining routes omitted"}})
                    return {"nodes": list(nodes.values()), "edges": edges, "truncated": True}
                edges.append(edge)
                if call["reference"]["status"] != "valid":
                    edge["status"] = "invalid_syntax"
                    continue
                if depth + 1 >= self.config["maxDepth"]:
                    edge["status"] = "limit_reached"
                    continue
                try:
                    target = self.target(call["reference"])
                    edge["resolution"] = target["resolution"]
                    edge["target"] = identity(target["workflow"])
                    edge["validation"] = validate_call(call, target["workflow"])
                    nodes[edge["target"]] = target["workflow"]
                    edge["status"] = "cycle" if edge["target"] in route else "resolved"
                    if edge["status"] == "resolved" and target["workflow"]["reusable"]:
                        queue.append((target["workflow"], [*route, edge["target"]], depth + 1))
                except Exception as error:
                    edge["error"] = failure(error)
        return {"nodes": list(nodes.values()), "edges": edges, "truncated": False}

    def collect(self, expected: list | None = None) -> dict:
        inventory = self.inventory(expected)
        scans = []
        for index, repo in enumerate(inventory["repositories"], start=1):
            self.progress(f"Repository {index}/{len(inventory['repositories'])}: {repo['full_name']}")
            scans.append(self.scan(repo))
        roots = [workflow for scan in scans for workflow in scan["workflows"]]
        self.progress("Resolving workflow calls and nested dependencies")
        graph = self.graph(roots)
        coverage = [scan["coverage"] for scan in scans]
        complete = (inventory["expectedInventoryProvided"] and not inventory["organizationError"] and
                    any(scan["repository"].lower() == self.config["library"].lower() and scan["coverage"]["status"] == "complete" for scan in scans) and
                    all(repo["id"] in inventory["expectedIds"] for repo in inventory["repositories"]) and
                    all(repo["status"] in ("complete", "no_workflows", "empty") for repo in coverage))
        audit = {"schemaVersion": 1, "generatedAt": timestamp(), "config": self.config, "inventory": inventory, "coverage": coverage,
                 "organizationCoverage": "complete_against_supplied_inventory" if complete else "not_verified",
                 "dependencyCoverage": "partial" if graph["truncated"] or any(edge["status"] != "resolved" for edge in graph["edges"]) else "complete",
                 "rootIdentities": [identity(workflow) for workflow in roots], **graph}
        write_json(self.directory / "data" / "audit.json", audit)
        return audit