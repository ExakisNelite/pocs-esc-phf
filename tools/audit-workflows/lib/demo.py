import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .github import GitHub


def demo_client(directory: Path, config: dict) -> tuple:
    current = "a" * 40
    legacy = "b" * 40
    repos = [{"id": 1, "full_name": "example/workflows", "owner": {"login": "example"}, "default_branch": "main", "visibility": "internal", "archived": False, "fork": False},
             {"id": 2, "full_name": "example/application", "owner": {"login": "example"}, "default_branch": "main", "visibility": "private", "archived": True, "fork": True}]
    expected = [*repos, {"id": 3, "full_name": "example/invisible", "visibility": "private"}]
    files = [(repos[0]["full_name"], current, "build.yml", """name: Build and test
on:
  workflow_call:
    inputs:
      node-version: {type: string, required: true}
      run-e2e: {type: boolean, default: false}
    secrets:
      NPM_TOKEN: {required: false}
    outputs:
      version: {value: '${{ jobs.build.outputs.version }}'}
permissions: {contents: read}
jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      version: '${{ steps.metadata.outputs.version }}'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '${{ inputs.node-version }}'
      - run: npm ci
      - run: npm test
  helper:
    uses: ./.github/workflows/build.yaml
"""), (repos[0]["full_name"], current, "build.yaml", """name: Helper
on: {workflow_call: null}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: echo helper
"""), (repos[0]["full_name"], legacy, "legacy.yml", """name: Legacy build
on:
  workflow_call:
    inputs:
      enabled: {type: boolean, required: true}
    secrets:
      TOKEN: {required: true}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo legacy
"""), (repos[1]["full_name"], current, "ci.yml", """on: push
jobs:
  build:
    uses: example/workflows/.github/workflows/build.yml@release/2026
    with: {node-version: '22'}
    secrets: inherit
  legacy:
    uses: example/workflows/.github/workflows/legacy.yml@v1
    with: {enabled: 'false'}
  pinned:
    uses: example/workflows/.github/workflows/legacy.yml@LEGACY_SHA
    with: {enabled: false}
    secrets: inherit
  invalid:
    uses: example/workflows/.github/workflows/build.yml@${{ inputs.ref }}
""".replace("LEGACY_SHA", legacy)), (repos[1]["full_name"], current, "broken.yml", "on: [")]
    responses = {f"/repos/{repo['full_name']}/commits/main": {"sha": current} for repo in repos}
    for repository, commit, name, source in files:
        prefix = f"/repos/{repository}/git"
        tree_prefix = hashlib.sha1(f"{repository}@{commit}".encode()).hexdigest()
        responses[f"{prefix}/commits/{commit}"] = {"sha": commit, "tree": {"sha": f"{tree_prefix}-root"}}
        responses[f"{prefix}/trees/{tree_prefix}-root"] = {"truncated": False, "tree": [{"path": ".github", "type": "tree", "sha": f"{tree_prefix}-github"}]}
        responses[f"{prefix}/trees/{tree_prefix}-github"] = {"truncated": False, "tree": [{"path": "workflows", "type": "tree", "sha": f"{tree_prefix}-workflows"}]}
        tree = responses.setdefault(f"{prefix}/trees/{tree_prefix}-workflows", {"truncated": False, "tree": []})
        content = source.encode()
        sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        tree["tree"].append({"path": name, "sha": sha, "mode": "100644", "type": "blob", "size": len(content)})
        responses[f"{prefix}/blobs/{sha}"] = {"sha": sha, "size": len(content), "encoding": "base64", "content": base64.b64encode(content).decode()}
    prefix = "/repos/example/workflows/git"
    responses[f"{prefix}/ref/tags/v1"] = {"object": {"type": "tag", "sha": "annotated-tag"}}
    responses[f"{prefix}/tags/annotated-tag"] = {"object": {"type": "commit", "sha": legacy}}
    responses[f"{prefix}/ref/heads/v1"] = {"object": {"type": "commit", "sha": current}}
    responses[f"{prefix}/ref/heads/release%2F2026"] = {"object": {"type": "commit", "sha": current}}
    def fetch(address, headers):
        endpoint = urlsplit(address).path
        if endpoint == "/installation/repositories":
            body, status = {"total_count": len(repos), "repositories": repos}, 200
        elif endpoint == "/orgs/example/repos":
            body, status = repos, 200
        else:
            body, status = responses.get(endpoint, {"message": "Not Found"}), 200 if endpoint in responses else 404
        return {"status": status, "headers": {}, "body": json.dumps(body).encode()}
    client = GitHub(directory=directory, base_url=config["apiUrl"], version=config.get("apiVersion", "2026-03-10"), fetch_impl=fetch)
    client.token = "offline-demo-not-a-token"
    return client, expected