import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from lib.collect import Collector, reconcile
from lib.github import ApiError, GitHub


COMMIT = "a" * 40
CONFIG = {"org": "acme", "library": "acme/library", "maxFileBytes": 2 * 1024 * 1024, "maxDepth": 10, "maxEdges": 1000}


def response(body, status=200, headers=None):
    return {"status": status, "headers": headers or {}, "body": json.dumps(body).encode()}


def trees(source, mode="100644"):
    content = source.encode()
    sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
    return {
        f"/repos/acme/library/git/commits/{COMMIT}": {"sha": COMMIT, "tree": {"sha": "root"}},
        "/repos/acme/library/git/trees/root": {"truncated": False, "tree": [{"path": ".github", "type": "tree", "sha": "github"}]},
        "/repos/acme/library/git/trees/github": {"truncated": False, "tree": [{"path": "workflows", "type": "tree", "sha": "workflows"}]},
        "/repos/acme/library/git/trees/workflows": {"truncated": False, "tree": [{"path": "build.yml", "type": "blob", "mode": mode, "sha": sha, "size": len(content)}]},
        f"/repos/acme/library/git/blobs/{sha}": {"sha": sha, "encoding": "base64", "size": len(content), "content": base64.b64encode(content).decode()}
    }


class CollectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def collector(self, responses):
        def fetch(address, headers):
            endpoint = urlsplit(address).path
            return response(responses[endpoint]) if endpoint in responses else response({}, 404)
        client = GitHub(directory=self.directory / "api", fetch_impl=fetch, wait=lambda delay: None)
        client.token = "test-not-a-token"
        return Collector(client=client, directory=self.directory, config=CONFIG)

    def test_inventory_retains_archives_forks_and_invisible_repos(self):
        result = reconcile([{"id": 1, "full_name": "acme/one", "owner": {"login": "Acme"}, "archived": True, "fork": True},
                            {"id": 3, "full_name": "other/three", "owner": {"login": "other"}}],
                           [{"id": 1, "full_name": "acme/one"}, {"id": 2, "full_name": "acme/private"}], "acme")
        self.assertEqual(len(result), 2)
        self.assertEqual(next(repo for repo in result if repo["id"] == 2)["discovery"], "not_visible_to_token")
        self.assertTrue(next(repo for repo in result if repo["id"] == 1)["fork"])

    def test_annotated_tag_wins_over_branch(self):
        responses = {"/repos/acme/library/git/ref/tags/release%2F2026": {"object": {"type": "tag", "sha": "tag"}},
                     "/repos/acme/library/git/tags/tag": {"object": {"type": "commit", "sha": COMMIT}},
                     f"/repos/acme/library/git/commits/{COMMIT}": {"sha": COMMIT},
                     "/repos/acme/library/git/ref/heads/release%2F2026": {"object": {"type": "commit", "sha": "b" * 40}}}
        result = self.collector(responses).resolve({"repository": "acme/library", "ref": "release/2026"})
        self.assertEqual(result["kind"], "tag")
        self.assertEqual(result["commitSha"], COMMIT)

    def test_permission_failure_does_not_fall_back(self):
        collector = self.collector({})
        calls = []
        def body(endpoint):
            calls.append(endpoint)
            raise ApiError(403, endpoint, "access_denied")
        collector.body = body
        with self.assertRaises(ApiError):
            collector.resolve({"repository": "acme/library", "ref": "v1"})
        self.assertEqual(len(calls), 1)

    def test_resume_snapshot_and_cycle(self):
        responses = trees("on: workflow_call\njobs:\n  loop:\n    uses: ./.github/workflows/build.yml\n")
        responses["/repos/acme/library/commits/main"] = {"sha": COMMIT}
        collector = self.collector(responses)
        repo = {"id": 1, "full_name": "acme/library", "default_branch": "main", "discovery": "visible"}
        first = collector.scan(repo)
        responses["/repos/acme/library/commits/main"] = {"sha": "b" * 40}
        second = collector.scan(repo)
        self.assertEqual(first["snapshot"]["commitSha"], second["snapshot"]["commitSha"])
        self.assertEqual(collector.graph(first["workflows"])["edges"][0]["status"], "cycle")

    def test_bad_yaml_is_partial_not_zero(self):
        responses = trees("on: [")
        responses["/repos/acme/library/commits/main"] = {"sha": COMMIT}
        coverage = self.collector(responses).scan({"id": 1, "full_name": "acme/library", "default_branch": "main"})["coverage"]
        self.assertEqual(coverage["status"], "partial")
        self.assertIsNone(coverage["referenceCount"])

    def test_truncated_tree_and_symlink_are_gaps(self):
        responses = trees("on: workflow_call", "120000")
        responses["/repos/acme/library/commits/main"] = {"sha": COMMIT}
        repo = {"id": 1, "full_name": "acme/library", "default_branch": "main"}
        self.assertEqual(self.collector(responses).scan(repo)["coverage"]["status"], "partial")
        responses["/repos/acme/library/git/trees/root"]["truncated"] = True
        self.assertIsNone(self.collector(responses).scan(repo)["coverage"]["referenceCount"])


class GitHubTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.delays = []

    def client(self, fetch):
        client = GitHub(directory=self.directory, fetch_impl=fetch, wait=self.delays.append)
        client.token = "test-secret"
        return client

    def test_pagination(self):
        count = 0
        def fetch(address, headers):
            nonlocal count
            count += 1
            return response({"total_count": 2, "repositories": [{"id": count, "archived": True, "fork": True}]},
                            headers={"Link": '<https://api.github.com/installation/repositories?page=2>; rel="next"'} if count == 1 else {})
        self.assertEqual(len(self.client(fetch).paginate("/installation/repositories", "repositories")), 2)

    def test_rate_limits_evidence_and_permanent_denial(self):
        calls = []
        def fetch(address, headers):
            calls.append(address)
            return response({"message": "secondary rate limit"}, 403, {"Retry-After": "1"}) if len(calls) == 1 else response({"temp_clone_token": "hidden"})
        client = self.client(fetch)
        client.get("/repos/acme/repo")
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(self.delays[0], 2)
        for path in (self.directory / "http").glob("*.json"):
            self.assertNotIn("test-secret", path.read_text())
            self.assertNotIn("hidden", path.read_text())
        client.fetch = lambda address, headers: response({}, 403)
        with self.assertRaises(ApiError) as caught:
            client.get("/repos/acme/other")
        self.assertEqual(caught.exception.reason, "access_denied")

    def test_cached_get_and_untrusted_host(self):
        calls = []
        def fetch(address, headers):
            calls.append(address)
            return response({"sha": COMMIT})
        client = self.client(fetch)
        client.get("/repos/acme/repo/git/commits/abc")
        client.get("/repos/acme/repo/git/commits/abc")
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError):
            client.get("https://evil.example/repos")

    def test_redirect_missing_and_incomplete_inventory(self):
        client = self.client(lambda address, headers: response({}, 404))
        with self.assertRaises(ApiError) as caught:
            client.get("/repos/acme/private")
        self.assertEqual(caught.exception.reason, "not_found_or_inaccessible")
        client.fetch = lambda address, headers: response({}, 301, {"location": "https://elsewhere.example"})
        with self.assertRaises(ApiError) as caught:
            client.get("/repos/acme/moved")
        self.assertEqual(caught.exception.reason, "redirect_requires_review")
        client.fetch = lambda address, headers: response({"total_count": 2, "repositories": [{"id": 1}]})
        with self.assertRaises(ValueError):
            client.paginate("/installation/repositories", "repositories")

    def test_bad_success_body_is_retried(self):
        calls = []
        def fetch(address, headers):
            calls.append(address)
            return {"status": 200, "headers": {}, "body": b"{broken"} if len(calls) == 1 else response({"sha": COMMIT})
        self.assertEqual(self.client(fetch).get("/repos/acme/repo/git/commits/abc")["body"]["sha"], COMMIT)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()