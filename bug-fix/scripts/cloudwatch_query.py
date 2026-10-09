#!/usr/bin/env python3
"""
CloudWatch log query planning for Trimble Connect bug investigation.

In the /bug-fix workflow, CloudWatch must be queried via AWS MCP only
(user-aws-mcp-connect-stage or user-aws-mcp-connect-prod), after AWS SSO login.
Use this script to resolve log groups and Insights query strings — not to call AWS.

Usage (plan only — default for agents):
    python cloudwatch_query.py --plan-only --root-id "Root=1-..." --env int --service monolith

Legacy local AWS CLI execution (avoid in bug-fix; use AWS MCP instead):
    python cloudwatch_query.py --execute-cli --root-id "Root=1-..." --env int --service monolith
"""

import argparse
import json
import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional


# Load log group mapping from JSON file
SCRIPT_DIR = Path(__file__).parent
LOG_GROUP_MAPPING_FILE = SCRIPT_DIR / "log_group_mapping.json"

TC_REGION_KEYS = frozenset({"us1", "eu1", "ap1", "ap2", "uk1"})


def load_log_group_mapping() -> Dict[str, Any]:
    """Load log group mappings from JSON configuration file."""
    if not LOG_GROUP_MAPPING_FILE.exists():
        print(f"Error: Log group mapping file not found: {LOG_GROUP_MAPPING_FILE}", file=sys.stderr)
        sys.exit(1)
    
    with open(LOG_GROUP_MAPPING_FILE, "r") as f:
        return json.load(f)


def normalize_root_id(root_id: str) -> str:
    """
    Normalize the Root ID to ensure consistent format.
    
    Accepts:
        - "Root=1-6ac7fb86-58555b7f3f97b48e03f4c250"
        - "1-6ac7fb86-58555b7f3f97b48e03f4c250"
    
    Returns the ID portion without the "Root=" prefix for CloudWatch queries.
    """
    if root_id.startswith("Root="):
        return root_id[5:]  # Remove "Root=" prefix
    return root_id


def get_regions_for_environment(config: Dict[str, Any], environment: str) -> List[str]:
    """Return Trimble Connect region codes (us1, eu1, ...) configured for an environment."""
    env = environment.lower()
    return config.get("regions_by_environment", {}).get(env, ["us1"])


def _is_regional_env_block(env_logs: Any) -> bool:
    return isinstance(env_logs, dict) and bool(TC_REGION_KEYS.intersection(env_logs.keys()))


def _flatten_service_region_block(region_block: Dict[str, Any]) -> List[str]:
    """Flatten a single region's log group definition into a list of log group names."""
    if "log_groups" in region_block and isinstance(region_block["log_groups"], list):
        return list(region_block["log_groups"])

    groups: List[str] = []
    for key, value in region_block.items():
        if key == "aws_region":
            continue
        if isinstance(value, list):
            groups.extend(value)
    return groups


def get_log_groups_for_service(
    config: Dict[str, Any],
    service: str,
    environment: str,
    tc_region: Optional[str] = None,
) -> List[str]:
    """
    Get CloudWatch log group names for a service and environment (all TC regions or one).

    Args:
        config: The log group mapping configuration
        service: Service name (monolith, file-service, permission-service, tcps-trigger)
        environment: Environment (int, qa, stage, prod)
        tc_region: Optional Trimble region code (us1, eu1, ap1, ap2, uk1)

    Returns:
        List of log group names
    """
    by_aws_region = get_log_groups_by_aws_region(
        config, service, environment, tc_region=tc_region
    )
    groups: List[str] = []
    for names in by_aws_region.values():
        groups.extend(names)
    return groups


def get_log_groups_by_aws_region(
    config: Dict[str, Any],
    service: str,
    environment: str,
    tc_region: Optional[str] = None,
) -> Dict[str, List[str]]:
    """
    Map AWS region -> log group names for a service/environment.

    Log groups in different AWS regions must be queried separately.
    """
    env = environment.lower()
    result: Dict[str, List[str]] = {}

    if service not in config.get("services", {}):
        print(f"Warning: Unknown service '{service}'", file=sys.stderr)
        return result

    service_config = config["services"][service]
    env_logs = service_config.get("log_groups", {}).get(env)
    if env_logs is None:
        print(f"Warning: No log groups configured for {service} in {env}", file=sys.stderr)
        return result

    aws_regions_map = config.get("aws_regions", {})
    regions = [tc_region.lower()] if tc_region else get_regions_for_environment(config, env)

    if _is_regional_env_block(env_logs):
        for region_code in regions:
            if region_code not in env_logs:
                print(
                    f"Warning: No log groups for {service}/{env}/{region_code}",
                    file=sys.stderr,
                )
                continue
            region_block = env_logs[region_code]
            aws_region = region_block.get(
                "aws_region", aws_regions_map.get(region_code, "us-east-1")
            )
            names = _flatten_service_region_block(region_block)
            result.setdefault(aws_region, []).extend(names)
        return result

    # Legacy flat structure (api/access/scheduler or list)
    if isinstance(env_logs, dict):
        names = _flatten_service_region_block(env_logs)
    elif isinstance(env_logs, list):
        names = env_logs
    else:
        names = []

    if names:
        result.setdefault(aws_regions_map.get("us1", "us-east-1"), []).extend(names)
    return result


def get_lambda_log_groups(
    config: Dict[str, Any],
    environment: str,
    lambda_names: Optional[List[str]] = None,
    tc_region: Optional[str] = None,
) -> List[str]:
    """Get Lambda (and related) log groups for an environment, optionally scoped to one TC region."""
    by_aws = get_lambda_log_groups_by_aws_region(
        config, environment, lambda_names=lambda_names, tc_region=tc_region
    )
    groups: List[str] = []
    for names in by_aws.values():
        groups.extend(names)
    return groups


def get_lambda_log_groups_by_aws_region(
    config: Dict[str, Any],
    environment: str,
    lambda_names: Optional[List[str]] = None,
    tc_region: Optional[str] = None,
) -> Dict[str, List[str]]:
    env = environment.lower()
    lambda_config = config.get("lambdas", {})
    aws_regions_map = config.get("aws_regions", {})
    regions = [tc_region.lower()] if tc_region else get_regions_for_environment(config, env)
    result: Dict[str, List[str]] = {}

    for lambda_name, env_groups in lambda_config.items():
        if lambda_names and lambda_name not in lambda_names:
            continue
        if env not in env_groups:
            continue
        env_entry = env_groups[env]
        if isinstance(env_entry, str):
            aws_region = aws_regions_map.get("us1", "us-east-1")
            result.setdefault(aws_region, []).append(env_entry)
            continue
        if isinstance(env_entry, dict):
            for region_code in regions:
                if region_code not in env_entry:
                    continue
                aws_region = aws_regions_map.get(region_code, "us-east-1")
                result.setdefault(aws_region, []).append(env_entry[region_code])
    return result


def collect_log_groups_by_aws_region(
    config: Dict[str, Any],
    environment: str,
    service: str,
    tc_region: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Collect log groups for a service choice, grouped by AWS region."""
    if service == "all":
        merged: Dict[str, List[str]] = {}
        for svc in ["monolith", "file-service", "permission-service", "tcps-trigger"]:
            for aws_region, groups in get_log_groups_by_aws_region(
                config, svc, environment, tc_region=tc_region
            ).items():
                merged.setdefault(aws_region, []).extend(groups)
        for aws_region, groups in get_lambda_log_groups_by_aws_region(
            config, environment, tc_region=tc_region
        ).items():
            merged.setdefault(aws_region, []).extend(groups)
        return merged
    if service == "lambdas":
        return get_lambda_log_groups_by_aws_region(
            config, environment, tc_region=tc_region
        )
    return get_log_groups_by_aws_region(
        config, service, environment, tc_region=tc_region
    )


def get_aws_profile(config: Dict[str, Any], environment: str) -> str:
    """Get the AWS profile for a given environment."""
    env = environment.lower()
    return config["environments"].get(env, {}).get("aws_profile", "default")


def get_mcp_namespace(config: Dict[str, Any], environment: str) -> str:
    """AWS MCP namespace for CloudWatch queries (stage vs prod account)."""
    env = environment.lower()
    return config["environments"].get(env, {}).get(
        "mcp_namespace", "user-aws-mcp-connect-stage"
    )


def build_insights_query(root_id: str, limit: int = 1000) -> str:
    """CloudWatch Logs Insights query string for a Root / trace id."""
    search_id = normalize_root_id(root_id)
    return f"""fields @timestamp, @message, @logStream
| filter @message like /{search_id}/
| sort @timestamp desc
| limit {limit}""".strip()


def build_query_plan(
    config: Dict[str, Any],
    root_id: str,
    environment: str,
    service: str,
    hours: int,
    tc_region: Optional[str] = None,
    aws_region_override: Optional[str] = None,
    limit: int = 1000,
) -> Dict[str, Any]:
    """Build MCP-ready CloudWatch query plan (no AWS API calls)."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    query_string = build_insights_query(root_id, limit=limit)

    groups_by_aws_region = collect_log_groups_by_aws_region(
        config, environment, service, tc_region=tc_region
    )
    if aws_region_override:
        all_groups: List[str] = []
        for aws_region, groups in groups_by_aws_region.items():
            if aws_region == aws_region_override:
                all_groups.extend(groups)
        groups_by_aws_region = (
            {aws_region_override: all_groups} if all_groups else {}
        )

    queries = []
    for aws_region, log_groups in sorted(groups_by_aws_region.items()):
        if not log_groups:
            continue
        queries.append(
            {
                "aws_region": aws_region,
                "log_group_names": log_groups,
                "start_time_ms": start_ms,
                "end_time_ms": end_ms,
                "query_string": query_string,
                "search_id": normalize_root_id(root_id),
            }
        )

    return {
        "environment": environment.lower(),
        "service": service,
        "root_id": root_id,
        "aws_profile": get_aws_profile(config, environment),
        "mcp_namespace": get_mcp_namespace(config, environment),
        "sso_skill": "/aws-sso-login",
        "queries": queries,
    }


def query_cloudwatch_logs_cli(
    log_groups: List[str],
    root_id: str,
    start_time: datetime,
    end_time: datetime,
    aws_profile: str,
    region: str = "us-east-1",
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Query CloudWatch Logs using AWS CLI.
    
    This uses AWS CLI instead of boto3 to work with AWS SSO credentials.
    """
    # Normalize the root_id (remove Root= prefix if present)
    search_id = normalize_root_id(root_id)
    
    query = build_insights_query(root_id, limit=limit)
    
    # Convert times to epoch milliseconds
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    results = {
        "query": query.strip(),
        "log_groups": log_groups,
        "root_id": root_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "results": [],
        "errors": []
    }
    
    if not log_groups:
        results["errors"].append("No log groups specified")
        return results
    
    # Build the AWS CLI command
    log_group_args = " ".join([f'"{lg}"' for lg in log_groups])
    
    # Start the query
    start_cmd = [
        "aws", "logs", "start-query",
        "--log-group-names"
    ] + log_groups + [
        "--start-time", str(start_ms),
        "--end-time", str(end_ms),
        "--query-string", query.strip(),
        "--profile", aws_profile,
        "--region", region,
        "--output", "json"
    ]
    
    try:
        print(f"Starting CloudWatch Logs Insights query...", file=sys.stderr)
        print(f"Searching {len(log_groups)} log groups for: {search_id}", file=sys.stderr)
        
        # Start the query
        result = subprocess.run(
            start_cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            results["errors"].append(f"Failed to start query: {result.stderr}")
            return results
        
        query_result = json.loads(result.stdout)
        query_id = query_result.get("queryId")
        
        if not query_id:
            results["errors"].append("No query ID returned")
            return results
        
        print(f"Query ID: {query_id}", file=sys.stderr)
        
        # Poll for results
        import time
        max_attempts = 30
        for attempt in range(max_attempts):
            get_cmd = [
                "aws", "logs", "get-query-results",
                "--query-id", query_id,
                "--profile", aws_profile,
                "--region", region,
                "--output", "json"
            ]
            
            result = subprocess.run(
                get_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                results["errors"].append(f"Failed to get results: {result.stderr}")
                return results
            
            query_status = json.loads(result.stdout)
            status = query_status.get("status")
            
            if status == "Complete":
                # Parse results
                for record in query_status.get("results", []):
                    log_entry = {}
                    for field in record:
                        log_entry[field["field"]] = field["value"]
                    results["results"].append(log_entry)
                
                results["statistics"] = query_status.get("statistics", {})
                print(f"Query complete. Found {len(results['results'])} matching records.", file=sys.stderr)
                break
            elif status in ("Failed", "Cancelled"):
                results["errors"].append(f"Query {status}")
                break
            else:
                print(f"Query status: {status} (attempt {attempt + 1}/{max_attempts})", file=sys.stderr)
                time.sleep(2)
        else:
            results["errors"].append("Query timed out")
        
    except subprocess.TimeoutExpired:
        results["errors"].append("AWS CLI command timed out")
    except json.JSONDecodeError as e:
        results["errors"].append(f"Failed to parse AWS CLI output: {e}")
    except Exception as e:
        results["errors"].append(f"Error: {str(e)}")
    
    return results


def format_log_results(results: Dict[str, Any], output_format: str = "full") -> str:
    """Format query results for display."""
    if output_format == "json":
        return json.dumps(results, indent=2, default=str)
    
    lines = []
    lines.append("=" * 80)
    lines.append("CLOUDWATCH LOGS QUERY RESULTS")
    lines.append("=" * 80)
    lines.append(f"Root ID: {results['root_id']}")
    lines.append(f"Time Range: {results['start_time']} to {results['end_time']}")
    lines.append(f"Log Groups Searched: {len(results['log_groups'])}")
    for lg in results['log_groups']:
        lines.append(f"  - {lg}")
    lines.append("-" * 80)
    
    if results.get("errors"):
        lines.append("ERRORS:")
        for error in results["errors"]:
            lines.append(f"  ! {error}")
        lines.append("-" * 80)
    
    if results.get("results"):
        lines.append(f"FOUND {len(results['results'])} LOG ENTRIES:")
        lines.append("-" * 80)
        
        for i, entry in enumerate(results["results"], 1):
            timestamp = entry.get("@timestamp", "N/A")
            message = entry.get("@message", "N/A")
            log_stream = entry.get("@logStream", "N/A")
            
            lines.append(f"[{i}] {timestamp}")
            lines.append(f"    Stream: {log_stream}")
            lines.append(f"    Message: {message[:500]}{'...' if len(message) > 500 else ''}")
            lines.append("")
    else:
        lines.append("NO MATCHING LOG ENTRIES FOUND")
        lines.append("")
        lines.append("Possible reasons:")
        lines.append("  - The Root ID may not have been logged")
        lines.append("  - Logs may have been rotated/deleted")
        lines.append("  - The time range may not include the request time")
        lines.append("  - The service may not log X-Ray trace IDs")
    
    if results.get("statistics"):
        stats = results["statistics"]
        lines.append("-" * 80)
        lines.append("QUERY STATISTICS:")
        lines.append(f"  Records Scanned: {stats.get('recordsScanned', 'N/A')}")
        lines.append(f"  Records Matched: {stats.get('recordsMatched', 'N/A')}")
        lines.append(f"  Bytes Scanned: {stats.get('bytesScanned', 'N/A')}")
    
    lines.append("=" * 80)
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Query CloudWatch Logs for Trimble Connect bug investigation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query monolith logs for a Root ID
  python cloudwatch_query.py --root-id "Root=1-6ac7fb86-58555b7f3f97b48e03f4c250" --env int --service monolith
  
  # Query file service logs with extended time range
  python cloudwatch_query.py --root-id "1-6ac7fb86-58555b7f3f97b48e03f4c250" --env prod --service file-service --hours 72
  
  # Query all services
  python cloudwatch_query.py --root-id "Root=..." --env stage --service all --output json
        """
    )
    
    parser.add_argument(
        "--root-id", "-r",
        required=True,
        help="X-Ray Root ID (with or without 'Root=' prefix)"
    )
    
    parser.add_argument(
        "--env", "-e",
        required=True,
        choices=["int", "qa", "stage", "prod"],
        help="Environment"
    )
    
    parser.add_argument(
        "--service", "-s",
        required=True,
        choices=["monolith", "file-service", "permission-service", "tcps-trigger", "lambdas", "all"],
        help="Service to query logs for"
    )
    
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Number of hours to search (default: 48)"
    )
    
    parser.add_argument(
        "--tc-region",
        choices=["us1", "eu1", "ap1", "ap2", "uk1"],
        help="Trimble Connect region (default: all regions configured for the environment)",
    )

    parser.add_argument(
        "--region",
        help="AWS region override (default: query each mapped AWS region separately)",
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of results (default: 1000)"
    )
    
    parser.add_argument(
        "--output", "-o",
        choices=["full", "json", "summary"],
        default="full",
        help="Output format (default: full)"
    )

    parser.add_argument(
        "--execute-cli",
        action="store_true",
        help="Run queries via local AWS CLI (not for /bug-fix workflow; default is --plan-only JSON)",
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_log_group_mapping()
    
    plan = build_query_plan(
        config,
        args.root_id,
        args.env,
        args.service,
        args.hours,
        tc_region=args.tc_region,
        aws_region_override=args.region,
        limit=args.limit,
    )

    if not plan["queries"]:
        print(
            f"Error: No log groups found for {args.service} in {args.env}"
            + (f" (tc-region={args.tc_region})" if args.tc_region else ""),
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.execute_cli:
        print(json.dumps(plan, indent=2))
        sys.exit(0)

    aws_profile = plan["aws_profile"]
    print(f"Using AWS profile: {aws_profile}", file=sys.stderr)

    groups_by_aws_region = {
        q["aws_region"]: q["log_group_names"] for q in plan["queries"]
    }

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=args.hours)

    combined_results: Dict[str, Any] = {
        "query": None,
        "root_id": args.root_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "log_groups": [],
        "results": [],
        "errors": [],
        "statistics": {},
        "aws_region_queries": [],
    }

    for aws_region, log_groups in sorted(groups_by_aws_region.items()):
        if not log_groups:
            continue
        print(
            f"Querying {len(log_groups)} log groups in AWS region {aws_region}...",
            file=sys.stderr,
        )
        region_result = query_cloudwatch_logs_cli(
            log_groups=log_groups,
            root_id=args.root_id,
            start_time=start_time,
            end_time=end_time,
            aws_profile=aws_profile,
            region=aws_region,
            limit=args.limit,
        )
        combined_results["aws_region_queries"].append(
            {"aws_region": aws_region, "log_groups": log_groups, "result": region_result}
        )
        combined_results["log_groups"].extend(log_groups)
        combined_results["results"].extend(region_result.get("results", []))
        combined_results["errors"].extend(region_result.get("errors", []))
        if combined_results["query"] is None:
            combined_results["query"] = region_result.get("query")

    print(format_log_results(combined_results, args.output))

    if combined_results.get("errors"):
        sys.exit(2)
    if not combined_results.get("results"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
