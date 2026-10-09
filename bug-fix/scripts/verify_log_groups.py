#!/usr/bin/env python3
"""Verify log groups from log_group_mapping.json exist in CloudWatch."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
spec = importlib.util.spec_from_file_location("cw", SCRIPT_DIR / "cloudwatch_query.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

STAGE_ACCOUNT_ENVS = ["int", "qa", "stage"]
PROD_ENVS = ["prod"]
SERVICES = ["monolith", "file-service", "permission-service", "tcps-trigger"]
ONLY_ENVS = None  # set via CLI e.g. stage,prod


def collect_for_env(config, env: str):
    profile = config["environments"][env]["aws_profile"]
    entries = []
    for svc in SERVICES:
        by_r = cw.get_log_groups_by_aws_region(config, svc, env)
        for aws_region, names in by_r.items():
            for name in names:
                entries.append((name, aws_region, f"{svc}/{env}"))
    by_r = cw.get_lambda_log_groups_by_aws_region(config, env)
    for aws_region, names in by_r.items():
        for name in names:
            entries.append((name, aws_region, f"lambdas/{env}"))
    return profile, entries


def list_log_groups(profile: str, aws_region: str):
    cmd = [
        "aws",
        "logs",
        "describe-log-groups",
        "--region",
        aws_region,
        "--profile",
        profile,
        "--output",
        "json",
    ]
    names = set()
    next_token = None
    while True:
        run_cmd = cmd + (["--starting-token", next_token] if next_token else [])
        result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return None, result.stderr.strip()
        data = json.loads(result.stdout)
        for group in data.get("logGroups", []):
            names.add(group["logGroupName"])
        next_token = data.get("nextToken")
        if not next_token:
            break
    return names, None


def main():
    env_filter = [e.strip().lower() for e in sys.argv[1:]] if len(sys.argv) > 1 else None
    config = cw.load_log_group_mapping()
    envs = []
    for e in STAGE_ACCOUNT_ENVS + PROD_ENVS:
        if env_filter and e not in env_filter:
            continue
        envs.append(e)

    buckets = {}
    for env in envs:
        profile, entries = collect_for_env(config, env)
        for name, aws_region, source in entries:
            key = (profile, aws_region)
            if name not in buckets.setdefault(key, {}):
                buckets[key][name] = source

    missing = []
    found = 0
    errors = []
    total = 0

    for (profile, aws_region), expected_map in sorted(buckets.items()):
        account = "connect-dev (stage MCP)" if "805451413154" in profile else "connect-prod"
        print(
            f"Listing {aws_region} — {account}, {len(expected_map)} expected...",
            file=sys.stderr,
        )
        actual, err = list_log_groups(profile, aws_region)
        if err:
            errors.append({"profile": profile, "aws_region": aws_region, "error": err})
            continue
        for name in sorted(expected_map):
            total += 1
            if name in actual:
                found += 1
            else:
                missing.append(
                    {
                        "log_group": name,
                        "aws_region": aws_region,
                        "profile": profile,
                        "source": expected_map[name],
                    }
                )

    report = {
        "environments_scoped": envs,
        "total_checked": total,
        "found": found,
        "missing_count": len(missing),
        "missing": missing,
        "list_errors": errors,
    }
    out = SCRIPT_DIR / "log_group_verify_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: report[k] for k in ("environments_scoped", "total_checked", "found", "missing_count")}, indent=2))
    print(f"Full report: {out}")
    if errors:
        print("LIST ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  {e['aws_region']}: {e['error'][:200]}", file=sys.stderr)
    if missing:
        print("MISSING LOG GROUPS:", file=sys.stderr)
        for m in missing:
            print(f"  [{m['aws_region']}] {m['log_group']} <- {m['source']}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
