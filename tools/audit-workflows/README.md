# Reusable Workflow Audit Toolkit

Local tooling for a static GitHub Enterprise Cloud audit. It collects default-branch workflows across the organization, extracts reusable contracts, resolves consumed versions and generates private review material. It does not execute downloaded code or change GitHub resources.

**Start here:** [Operational audit guide (French)](guide-audit.md) for the complete sequence of automated collection and manual review. This README is the technical reference; the implementation plan is background design, not the execution procedure.

## Quick Start

Requirements: Python 3.11 or newer and pip. Tested locally on Python 3.13.7 / Windows. Bash is optional; PowerShell can call the same CLI directly. The implementation uses Python's standard library and the pinned `ruamel.yaml` parser rather than the conceptual curl/jq/yq examples in the implementation plan. No Node.js, npm, yq or Azure access is required. User-session authentication additionally requires GitHub CLI and `gh auth login`; offline commands and App authentication do not.

From this directory, in PowerShell (activation is not required):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.venv\Scripts\python.exe audit.py demo --run-dir results/python-demo
```

The demo uses simulated responses only: no token, GitHub requests or enterprise data. Open `results/python-demo/docs/index.md`. It deliberately includes an archived fork, an invisible repository, malformed YAML, a legacy version, an annotated tag, a branch with `/`, a pinned SHA, nested calls and contract findings. A second demo invocation requires a new directory, or use resume:

```powershell
.venv\Scripts\python.exe audit.py resume --run-dir results/python-demo
.venv\Scripts\python.exe audit.py report --run-dir results/python-demo
```

In Linux/macOS/WSL Bash:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t . -v
bash audit.sh demo --run-dir results/python-demo
```

`bash audit.sh` is a thin wrapper over the Python CLI. It selects this directory's virtual environment on Unix or Windows/Git Bash, otherwise `python3`. Set `PYTHON` to an interpreter executable path to override it. Install the requirements in whichever interpreter you select. Do not mix a Windows virtual environment with WSL; create a separate environment there.

Existing run directories from the earlier Node implementation retain the same JSON schema, cache keys and checkpoint identities. Use Python `resume` or `report` against the original directory; no conversion or new token is needed for offline demos/report generation. Windows long evidence paths are handled internally. Other Python versions and Unix execution have not been verified in this workspace.

## Configuration

Use `config.example.json` as a starting point for your local, ignored `config.json`. Set `org` and `library` to your actual target, not the example names. `library` accepts either a repository name or `org/repository`, within the selected organization.

Optional fields:

| Field | Meaning |
| --- | --- |
| `authMode` | `app` (default, installation token) or `user` (stored GitHub CLI session by default) |
| `expectedInventory` | Path to an independently supplied organization inventory, relative to the config file |
| `tokenCommand` | Argument array for an approved token provider; overrides the default `gh` provider in user mode |
| `maxDepth` | Total workflow nesting limit, including the top-level caller; default/maximum 10 |
| `maxEdges` | Maximum graph routes; default 100000; hitting the limit makes dependency coverage partial |
| `maxFileBytes` | Per-workflow size limit; default 2 MiB, configurable up to 10 MiB |
| `apiUrl` | HTTPS GitHub API URL; defaults to `https://api.github.com` |
| `apiVersion` | REST API version header; defaults to `2026-03-10` |

Unknown fields are rejected. Do not put credentials in this file or in command arguments. Avoid putting sensitive arguments in `tokenCommand`: configure its executable to use your approved local secret store.

An independent inventory is a JSON array, supplied by an organization owner or an authoritative process **not limited by the audit token's visibility**:

```json
[
  { "id": 123456, "full_name": "your-org/your-repository" },
  { "id": 789012, "full_name": "your-org/another-repository" }
]
```

Example config extension: `"expectedInventory": "organization-inventory.json"`. Include public, private, internal, archived and fork repositories. Repository IDs, not names alone, drive reconciliation. An export made using the same restricted token cannot independently prove completeness. The toolkit preserves extra visible repositories and marks absent expected repositories `not_visible_to_token`.

## Authentication

### User Session After gh Login

Set `"authMode": "user"` in your local config, alongside your existing organization, library and inventory settings. No token value or `tokenCommand` is needed. From the same user account and terminal environment:

```powershell
gh auth login --hostname github.com --web
gh auth status --active --hostname github.com
.venv\Scripts\python.exe audit.py check --config config.json
.venv\Scripts\python.exe audit.py run --config config.json --run-dir results/my-user-audit
```

Login is unnecessary if the correct account is already authenticated. If multiple accounts are configured, select the intended active account with `gh auth switch --hostname github.com --user YOUR_LOGIN`. Do not use `--show-token`, manually print `gh auth token`, or paste a token into config.

Python captures `gh auth token --hostname github.com` privately, without a shell, and keeps its output in memory. The child process ignores `GH_TOKEN`, `GITHUB_TOKEN` and their enterprise equivalents so that unrelated environment tokens cannot override the stored session. Explicit `tokenCommand` providers are responsible for their own credential selection. `check` does not execute the provider or validate the login.

User mode validates `/user` and paginates `/orgs/<org>/repos?type=all`. A denied organization listing stops inventory collection rather than masquerading as an empty organization. `data/inventory.json` records the authenticated login and mode, but not the token. Repositories that the account cannot see still require reconciliation against an independent inventory.

For private repositories, the CLI OAuth token generally needs `repo` and `read:org` scopes, plus access through the user's organization membership/teams. SSO authorization and organization/enterprise OAuth restrictions can still block access. Obtain approval as needed. Standard CLI login can grant broader capabilities than this read-only audit needs; the toolkit itself only makes GET requests. Verify that CLI credentials use the system credential store: `gh` can fall back to a plain-text file if the store is unavailable.

Resume can reuse this mode without a local config because the default CLI provider is reconstructed from the manifest. Re-authenticate yourself if the stored session is invalid; the tool cannot perform an interactive login. Changing from App to user authentication requires a **new run directory**, not resume. Outside `api.github.com`, the CLI host defaults to the API hostname; custom host/provider behavior has not been integration-tested here.

### GitHub App Installation

Use a GitHub App installation token with `Metadata: read` and `Contents: read`. The App should be installed on **All repositories**, with no token-level repository restriction. Static analysis does not need write permissions. The first authenticated inventory endpoint is `/installation/repositories`, not `/user`.

Inject `GITHUB_TOKEN` (or `GH_TOKEN`) into the process environment using your existing approved mechanism. The tool never asks for a token interactively and never records the Authorization header.

Installation tokens normally expire after one hour. For longer collections, optionally configure a provider:

```json
"tokenCommand": ["approved-token-provider", "installation-token"]
```

This is an interface example, not an installed command. The provider must output either the token alone or JSON containing `token` and optional `expires_at`. It is invoked without a shell, initially if no token was injected, shortly before a known expiration, and once after a 401. Provider stdout/stderr and the command itself are not persisted in the run manifest. Signing an App JWT and managing its private key remain the responsibility of your approved provider.

For resume with a provider, pass the local config again. A plain environment token is sufficient without `--config`. Do not use `set -x`, PowerShell transcription or CI logging that could expose credentials.

## Commands

```powershell
# Local tools/config check only, without GitHub requests
.venv\Scripts\python.exe audit.py check --config config.json

# Real audit after gh login (user mode) or token injection (App mode)
.venv\Scripts\python.exe audit.py run --config config.json --run-dir results/my-audit

# Retry unfinished files/targets without changing saved default-branch SHAs
.venv\Scripts\python.exe audit.py resume --run-dir results/my-audit --config config.json

# Regenerate documents from the collected JSON, without credentials/network
.venv\Scripts\python.exe audit.py report --run-dir results/my-audit
```

In Bash, replace `.venv\Scripts\python.exe audit.py` with `bash audit.sh` and keep the arguments unchanged.

Do not run two collectors on the same directory. A lock prevents this. After an abrupt process termination, verify that no process is still running before removing `.audit.lock`. API and collection failures keep existing checkpoints. A collection interrupted before `data/audit.json` is written must be resumed before reports can be generated.

Resume freezes the saved repository inventory, default-branch snapshots and successful reference resolutions. It retries partial/inaccessible scans and unresolved calls. To refresh inventory or deliberately observe a newer branch/ref, start a **new run directory**. Configuration changes to targets, authentication mode or limits require a new run. Older manifests without `authMode` retain App behavior. Individual snapshots are taken at different times; this is not an atomic organization-wide snapshot.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Command completed; for real collection, coverage checks passed and no invalid call contracts were found |
| 1 | Fatal configuration, authentication, inventory, I/O or pipeline failure |
| 2 | Reports produced, but organization/dependency coverage is incomplete or call contracts have findings |

`check`, `demo` and `report` return 0 when they finish, regardless of findings in the data. A demo returning 0 is not a successful enterprise audit. `static_checks_passed` is not approval: expression evaluation, token permissions, secret availability and access policies are outside these checks.

## Deliverables

```text
results/my-audit/
  data/
    run-manifest.json
    expected-inventory.json
    inventory.json
    repositories/<repository-id>.json
    resolved/<reference-key>.json
    audit.json
    raw/api/http/<request-id>.json
    raw/api/requests.jsonl
    raw/api/cache/<url-key>.json
    raw/<repository-key>/<commit-sha>/<blob-sha>.yml
  docs/
    index.md
    workflows/<workflow-extension>-<identity-key>.md
  reports/
    coverage.md
    coverage.csv
    inaccessible-repositories.csv
    consumer-references.csv
    unresolved-references.csv
    validation-findings.csv
    security-observations.csv
  review-notes.csv
```

The JSON preserves exact contracts, jobs, commands, input defaults, call arguments, identities and routes. Documents group each library workflow by exact commit, including consumed versions absent from the default-branch catalog. `.yml` and `.yaml` remain distinct. Each call retains its job, route, ref, resolved commit and validation result.

Reports omit shell commands, call parameter values and string input defaults. This is **not a complete secret scrubber**: names, descriptions and expressions can also contain sensitive data. Review before sharing. CSV cells are quoted and guarded against spreadsheet formula injection. Keep your decisions in `review-notes.csv`, which regeneration never overwrites. New versions discovered during resume may require adding rows to that file yourself; generated workflow documents are disposable.

## Reading Coverage

| Status | Meaning |
| --- | --- |
| `complete` | All discovered workflow files parsed at the saved commit |
| `no_workflows` | Complete Git tree traversal found no workflow YAML files |
| `empty` | GitHub explicitly confirmed an empty Git repository |
| `partial` | At least one tree, file, parser or limit check failed |
| `inaccessible` | Repository snapshot/tree request was denied or hidden |
| `not_visible_to_token` | Expected repository was absent from token-visible discovery |

For incomplete scans, `referenceCount` is `null`, never an invented zero. `referencesFound` preserves calls from readable files. A repository with no default branch remains partial unless GitHub explicitly confirms emptiness. A 404 may mean missing **or inaccessible**, not proof of absence.

Organization coverage is only `complete_against_supplied_inventory` if the independent inventory matches, the organization listing succeeded, every repository scan completed and the library workflow directory was fully read. This label trusts the supplied inventory; it cannot independently prove that inventory is authoritative. Dependency coverage is separate: unresolved calls, cycles and depth/edge limits make it partial.

## What Is Automated

- App mode merges paginated installation and organization inventories by ID; user mode uses an authenticated organization listing. Results are filtered to the actual owning organization. No visibility, archived or fork filter is applied.
- Caller default branches are pinned to commit SHAs. Non-recursive Git trees avoid silently truncated recursive listings.
- Workflow bytes are downloaded through the Git Blob API and checked against the Git SHA-1 blob identity before parsing and after reading saved evidence.
- YAML 1.2 parsing preserves `on`, empty `workflow_call`, literal false values and defaults. Duplicate keys, multi-document YAML and malformed job structures become collection errors.
- Only `jobs.*.uses` is considered a reusable workflow call. Step actions, comments and strings in `run` do not become consumers.
- Literal tags, branches and commit SHAs resolve to exact commits. Annotated tags are peeled; tags take precedence over same-name branches. Permission/rate/transport failures never trigger a branch fallback.
- Local `./.github/workflows/...` and Cloud `$/...` calls use the caller commit. Expressions in `uses` are reported as invalid syntax. Nested dependencies preserve direct/transitive routes, with cycle and resource limits.
- Required/unknown inputs, literal input types, secret mappings and the exact target's `workflow_call` declaration are checked. Expressions and inherited secret availability remain unknown.
- Heuristics flag unpinned actions, write permissions, apparently unused inputs and some missing output/step references. They are review prompts, not definitive findings.
- GET requests use bounded retries, rate-limit headers, a request journal and positive-response cache. Redirects are refused. Quota waits over five minutes stop the affected operation for later resume.

## Human Review And Optional Tools

Start with `reports/coverage.md`, then unresolved calls and invalid contracts, before concluding that a workflow has no consumers. Review each workflow's private source, purpose, external effects, inputs, outputs, runners and failure behavior. Record evidence and decisions in `review-notes.csv`.

Useful complementary tools, not automatically installed or executed here:

- **actionlint** for GitHub Actions schema/expression checks and shell integration.
- **zizmor** for GitHub Actions security rules.
- **ShellCheck** for extracted shell scripts.
- An approved **secret scanner** before sharing evidence or reports.
- Optional Actions run-history queries to verify execution, using additional `Actions: read` permissions. Keep that evidence separate from static references.

Use approved/pinned releases and consult each tool's own setup instructions. Do not execute fetched workflows, scripts or actions merely to audit them. Local/composite actions, external scripts, container images, organization policies, environments, branch protections and effective permission propagation require separate inspection; this toolkit records action/workflow dependencies but does not recursively audit action implementations or download all repository scripts.

This is not a complete GitHub Actions schema validator. Unsupported YAML constructs, file modes such as symlinks, files over configured limits, explicit YAML 1.1, trees GitHub marks truncated and unsupported/non-commit refs remain visible gaps. There is no clone fallback, all-branch/history scan, parallel collection, ETag refresh or live run-history enrichment in this version. Sequential requests favor reproducible evidence and conservative rate-limit handling.

## Confidentiality And Verification

Keep the entire run directory private, including reports. The toolkit ignores its default `results/`, local `config.json`, `.venv/` and Python bytecode in Git. If you choose a directory elsewhere or a different local config filename, add equivalent exclusions yourself. File modes are requested as 0600/0700 on Unix; Windows users must apply appropriate NTFS access controls. Never publish evidence automatically.

`python -m unittest discover -s tests -t . -v` uses Python's native test runner with local temporary directories and fake HTTP responses. It covers parsing, contracts, both authentication modes, session token handling, inventory reconciliation, retries, pagination, SHA snapshots, tag precedence, nested calls, coverage gaps, report safety and CLI demo/resume/report. When the optional earlier `results/demo` fixture is present, an additional test copies it to a temporary directory and verifies Python report/resume compatibility without modifying the original. No organization audit is performed by the tests. Real App/CLI credentials, permissions and GitHub API behavior still need validation when you run the real collection.

See [the implementation plan](plan-implementation.md) for the broader design and future enhancements.
