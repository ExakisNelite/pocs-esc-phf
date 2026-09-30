import unittest

from lib.analyze import analyze_workflow, parse_reference, validate_call


PROVENANCE = {"repository": "acme/library", "path": ".github/workflows/build.yml", "commitSha": "a" * 40}


class AnalyzeTests(unittest.TestCase):
    def test_empty_contract_and_falsy_defaults(self):
        self.assertTrue(analyze_workflow("on:\n  workflow_call:\n", PROVENANCE)["reusable"])
        result = analyze_workflow("""on:
  workflow_call:
    inputs:
      enabled: {type: boolean, default: false}
      count: {type: number, default: 0}
      label: {type: string, default: ''}
      implicit: {type: boolean}
""", PROVENANCE)
        self.assertEqual([entry["default"] for entry in result["inputs"]], [False, 0, "", False])
        self.assertEqual([entry["defaultDeclared"] for entry in result["inputs"]], [True, True, True, False])

    def test_invalid_yaml_and_structures(self):
        for source in ["name: a\nname: b", "on: push\n---\non: push", "%YAML 1.1\n---\non: push",
                       "on: push\njobs: bad", "on: push\njobs: {test: null}",
                       "on: push\njobs: {test: {steps: bad}}", "on: true", "on: push\nvalue: .inf",
                       "on: push\nrecursive: &recursive {value: *recursive}"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                analyze_workflow(source, PROVENANCE)

    def test_job_calls_are_not_step_actions(self):
        result = analyze_workflow("""on: push
jobs:
  caller:
    if: false
    uses: Acme/Library/.github/workflows/build.yaml@release/2026
  action:
    steps:
      - uses: actions/checkout@v4
      - run: 'echo uses: acme/library/.github/workflows/ignored.yml@main'
""", PROVENANCE)
        self.assertEqual(len(result["references"]), 1)
        self.assertIs(result["references"][0]["condition"], False)
        self.assertEqual(result["references"][0]["reference"]["ref"], "release/2026")
        self.assertEqual(len(result["actions"]), 1)

    def test_references(self):
        for prefix in ("./", "$/"):
            result = parse_reference(f"{prefix}.github/workflows/build.yml", PROVENANCE["repository"], PROVENANCE["commitSha"])
            self.assertTrue(result["local"])
            self.assertEqual(result["ref"], PROVENANCE["commitSha"])
        for reference in ["acme/library/.github/workflows/${{ inputs.name }}.yml@main",
                          "acme/library/.github/workflows/build.yml@refs/heads/main"]:
            self.assertEqual(parse_reference(reference)["status"], "invalid_syntax")

    def test_contracts_and_python_boolean_number_distinction(self):
        target = analyze_workflow("""on:
  workflow_call:
    inputs:
      enabled: {type: boolean, required: true}
      count: {type: number}
    secrets:
      TOKEN: {required: true}
""", PROVENANCE)
        self.assertEqual(validate_call({"with": {"enabled": False}, "secrets": "inherit"}, target)["status"], "needs_review")
        self.assertEqual(validate_call({"with": {"enabled": "false"}, "secrets": {}}, target)["status"], "invalid")
        self.assertEqual(validate_call({"with": {"enabled": "${{ inputs.enabled }}"}, "secrets": "inherit"}, target)["status"], "needs_review")
        self.assertEqual(validate_call({"with": {"enabled": False, "count": True}, "secrets": "inherit"}, target)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()