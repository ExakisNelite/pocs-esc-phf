#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

from lib.collect import Collector, reconcile
from lib.demo import demo_client
from lib.github import GitHub, local_path, read_json, timestamp, write_json
from lib.report import generate_reports, report_from_disk


HOME = Path(__file__).resolve().parent
USAGE = """Reusable workflow audit (Python >=3.11)

  python audit.py check  --config config.json
  python audit.py run    --config config.json [--run-dir results/my-run]
  python audit.py resume --run-dir results/my-run [--config config.json]
  python audit.py report --run-dir results/my-run
  python audit.py demo   [--run-dir results/demo]

check does not contact GitHub. demo uses only simulated responses.
run/resume use authMode=app (installation token) or authMode=user (gh auth login).
Exit codes: 0 = finished; 2 = incomplete coverage or contract findings; 1 = fatal error.
"""


def options(arguments: list[str]) -> dict:
    result = {}
    for index in range(0, len(arguments), 2):
        option = arguments[index]
        if option not in ("--config", "--run-dir") or index + 1 >= len(arguments) or arguments[index + 1].startswith("--") or option in result:
            raise ValueError("Expected --config PATH or --run-dir PATH, once each")
        result[option] = arguments[index + 1]
    return result


def configuration(path: str | None) -> dict:
    if not path:
        raise ValueError("--config is required")
    source = Path(path).resolve()
    data = read_json(source)
    allowed = {"org", "library", "apiUrl", "apiVersion", "authMode", "expectedInventory", "tokenCommand", "maxDepth", "maxEdges", "maxFileBytes"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Unknown configuration field; credentials must not be in config")
    if not isinstance(data.get("org"), str) or not re.fullmatch(r"[a-zA-Z0-9-]+", data["org"]):
        raise ValueError("Set org to the organization login")
    library = data.get("library", "")
    if not isinstance(library, str):
        raise ValueError("library must be an organization repository")
    if "/" not in library:
        library = data["org"] + "/" + library
    if not re.fullmatch(r"[a-zA-Z0-9-]+/[a-zA-Z0-9._-]+", library) or library.split("/")[0].lower() != data["org"].lower():
        raise ValueError("library must belong to org")
    config = {"apiUrl": "https://api.github.com", "apiVersion": "2026-03-10", "authMode": "app", "maxDepth": 10,
              "maxEdges": 100_000, "maxFileBytes": 2 * 1024 * 1024, **data, "library": library}
    for name, maximum in (("maxDepth", 10), ("maxEdges", 1_000_000), ("maxFileBytes", 10 * 1024 * 1024)):
        if type(config[name]) is not int or not 1 <= config[name] <= maximum:
            raise ValueError(f"Invalid {name}")
    if not isinstance(config["apiVersion"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", config["apiVersion"]):
        raise ValueError("Invalid apiVersion")
    if config.get("expectedInventory"):
        config["expectedInventory"] = str((source.parent / config["expectedInventory"]).resolve())
    if not isinstance(config["apiUrl"], str):
        raise ValueError("Invalid apiUrl")
    GitHub(base_url=config["apiUrl"], token_command=config.get("tokenCommand"), auth_mode=config["authMode"], directory=Path("."))
    return config


def main(arguments: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        raise ValueError("Python >=3.11 is required")
    if os.name != "nt":
        os.umask(0o077)
    arguments = sys.argv[1:] if arguments is None else arguments
    if not arguments or arguments[0] in ("help", "--help", "-h"):
        print(USAGE)
        return 0
    command = arguments[0]
    if command not in ("check", "run", "resume", "report", "demo"):
        raise ValueError("Unknown command; use --help")
    args = options(arguments[1:])
    if command == "check":
        config = configuration(args.get("--config"))
        print(f"Python {sys.version.split()[0]}; YAML 1.2 parser loaded; config valid for {config['org']}.")
        if config["authMode"] == "user":
            print("User authentication selected; gh login/provider and permissions are not checked. No API calls performed.")
        elif not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN") and not config.get("tokenCommand"):
            print("Authentication not injected yet. No API calls performed.")
        if not config.get("expectedInventory"):
            print("Independent inventory missing: organization-wide coverage will remain unverified.")
        return 0
    if command in ("resume", "report") and "--run-dir" not in args:
        raise ValueError("--run-dir is required")
    default_name = "demo" if command == "demo" else re.sub(r"[:.]", "-", timestamp())
    directory = local_path(args.get("--run-dir", HOME / "results" / default_name))
    if command == "report":
        result = report_from_disk(directory)
        print(f"Reports generated: {result['workflowDocuments']} workflow documents. {directory}")
        return 0
    if command == "resume":
        manifest = read_json(directory / "data" / "run-manifest.json")
        if not manifest:
            raise ValueError("No manifest in run directory")
        config = dict(manifest["config"])
        if "--config" in args:
            provided = configuration(args["--config"])
            for name in ("org", "library", "apiUrl", "apiVersion", "authMode", "maxDepth", "maxEdges", "maxFileBytes"):
                if provided[name] != config.get(name, "app" if name == "authMode" else None):
                    raise ValueError(f"Resume configuration mismatch: {name}")
            config["tokenCommand"] = provided.get("tokenCommand")
        expected = read_json(directory / "data" / "expected-inventory.json")
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Run directory is not empty; use resume or a new directory")
        config = {"org": "example", "library": "example/workflows", "apiUrl": "https://api.github.com", "apiVersion": "2026-03-10", "maxDepth": 10, "maxEdges": 1000, "maxFileBytes": 2 * 1024 * 1024} if command == "demo" else configuration(args.get("--config"))
        expected = read_json(Path(config["expectedInventory"])) if config.get("expectedInventory") else None
        if config.get("expectedInventory") and not isinstance(expected, list):
            raise ValueError("Expected inventory must be an existing JSON array")
        if expected is not None:
            reconcile([], expected, config["org"])
        safe_config = {name: value for name, value in config.items() if name not in ("tokenCommand", "expectedInventory")}
        manifest = {"schemaVersion": 1, "mode": "offline_demo" if command == "demo" else "github", "startedAt": timestamp(), "config": safe_config}
    if manifest["mode"] == "offline_demo":
        client, expected = demo_client(directory / "data" / "raw" / "api", config)
    else:
        client = GitHub(directory=directory / "data" / "raw" / "api", base_url=config["apiUrl"], version=config["apiVersion"], token_command=config.get("tokenCommand"), auth_mode=config.get("authMode", "app"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / ".audit.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("Run is locked. Verify no process is running before removing .audit.lock.") from None
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as lock:
            lock.write(str(os.getpid()))
        write_json(directory / "data" / "run-manifest.json", manifest)
        write_json(directory / "data" / "expected-inventory.json", expected)
        audit = Collector(client=client, directory=directory, config=manifest["config"], progress=print).collect(expected)
        audit["mode"] = manifest["mode"]
        write_json(directory / "data" / "audit.json", audit)
        result = generate_reports(directory, audit)
        print(f"{result['workflowDocuments']} workflow documents; {result['libraryReferences']} library reference routes.")
        print(f"Coverage: {audit['organizationCoverage']}; dependencies: {audit['dependencyCoverage']}.")
        print(f"Reports: {directory / 'docs' / 'index.md'}")
        incomplete = (audit["organizationCoverage"] == "not_verified" or audit["dependencyCoverage"] != "complete" or
                      any(edge.get("validation", {}).get("status") == "invalid" for edge in audit["edges"]))
        return 2 if manifest["mode"] != "offline_demo" and incomplete else 0
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Audit interrupted; use resume with the same run directory.", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f"Audit stopped: {error}", file=sys.stderr)
        sys.exit(1)