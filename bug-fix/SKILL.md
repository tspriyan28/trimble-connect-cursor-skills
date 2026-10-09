---
name: bug-fix
description: >-
  Automate JIRA bug fixes for Trimble Connect services. Fetches ticket details,
  reproduces bugs via API, queries CloudWatch logs via AWS MCP, analyzes code, implements fixes,
  adds unit tests, and raises PRs. Usage: /bug-fix {jiraTicketUrl} [additionalContext]
  --repo {repositoryPath} --branch {targetBranch} [--env {environment}]
argument-hint: "{jiraTicketUrl} [context] --repo {path} --branch {branch} [--env int|qa|stage|prod]"
---

# Bug Fix Automation Skill

Automate the entire bug fix lifecycle for Trimble Connect services.

## Prerequisites

### 1. Playwright MCP Server (for browser automation)

Add to your Cursor MCP settings (`.cursor/mcp.json` or Cursor Settings > MCP):

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

**For headed mode with HTTP transport:**
```bash
npx @playwright/mcp@latest --port 8931
```

Then configure:
```json
{
  "mcpServers": {
    "playwright": {
      "url": "http://localhost:8931/mcp"
    }
  }
}
```

### 2. AWS SSO Login (required before CloudWatch)

CloudWatch log queries in this workflow **must** use AWS MCP (`aws___run_script` / `call_boto3`). SSO must be valid first.

| Environment | AWS profile | MCP namespace |
|-------------|-------------|---------------|
| INT, QA, STAGE | `805451413154_connect-dev_developer1` | `user-aws-mcp-connect-stage` |
| PROD | `354755235860_connect-prod_developer-readonly` | `user-aws-mcp-connect-prod` |

Before any CloudWatch step, run `/aws-sso-login` (or verify with `aws sts get-caller-identity --profile <profile>`). If AWS MCP returns auth/token errors, re-run SSO and reload MCP servers in Cursor.

**Do not** query CloudWatch via Shell/`aws logs` in the bug-fix workflow — use the MCP namespace for the target environment only.

### 3. Python with requests library

```bash
pip install requests
```

## Usage

```
/bug-fix {jiraTicketUrl} [additionalContext] --repo {repositoryPath} --branch {targetBranch} [--env {environment}]
```

### Parameters

| Parameter | Required | Description | Example |
|-----------|----------|-------------|---------|
| `jiraTicketUrl` | Yes | Full JIRA ticket URL | `https://jira.trimble.tools/browse/TCCP-12345` |
| `additionalContext` | No | Extra context about the bug | `"Only happens with folders > 100 files"` |
| `--repo` | Yes | Repository path | `C:\Work\trimble-connect-platform` |
| `--branch` | Yes | Target branch for PR | `develop` |
| `--env` | No | Environment (default: int) | `int`, `qa`, `stage`, `prod` |

### Examples

```bash
# Basic usage
/bug-fix https://jira.trimble.tools/browse/TCCP-12345 --repo C:\Work\trimble-connect-platform --branch develop

# With additional context
/bug-fix https://jira.trimble.tools/browse/TCCP-12345 "The issue only happens when folder has more than 100 files" --repo C:\Work\trimble-connect-files-service --branch main

# Specifying environment
/bug-fix https://jira.trimble.tools/browse/TCCP-12345 --repo C:\Work\trimble-connect-platform --branch develop --env stage
```

## Workflow

### Step 1: Read Architecture Context

Read the Trimble Connect architecture index to understand service boundaries:

```
Read file: c:\Work\TRIMBLE_CONNECT_ARCHITECTURE_INDEX.md
```

Key information to extract:
- Service responsibilities (TCPS, File Service, Permission Service, TCPS Trigger)
- Endpoint to repository mapping
- Database schemas
- Async flow patterns

### Step 2: Fetch JIRA Ticket Details

Use the JIRA MCP tools to get ticket information:

```
Tool: jira_get-issue (plugin-e-tools-mcp-E-Tools MCP namespace)
      OR
Tool: get_issue (user-jira namespace)

Extract:
- Summary
- Description
- Steps to reproduce
- Expected vs actual behavior
- API endpoints mentioned
- Environment affected
- Reporter and creation date
```

Parse the JIRA ticket to identify:
- Which service is affected (based on endpoint patterns)
- API endpoint to reproduce
- Request/response details if provided
- Error messages or stack traces

### Step 3: Fetch Bearer Token from TCWEB

Use Playwright MCP to get an access token:

**TCWEB URLs by Environment:**
| Environment | URL |
|-------------|-----|
| INT | `https://web.int.connect.trimble.com` |
| QA | `https://web.qa.connect.trimble.com` |
| STAGE | `https://web.stage.connect.trimble.com` |
| PROD | `https://web.connect.trimble.com` |

**Playwright MCP Workflow:**

```javascript
// 1. Navigate to TCWEB
browser_navigate({ url: "https://web.int.connect.trimble.com" })

// 2. Wait for login (if needed, prompt user to complete login)
browser_wait_for({ text: "Projects" })  // or appropriate logged-in indicator

// 3. Extract bearer token from localStorage/sessionStorage
browser_evaluate({
  function: `() => {
    const raw = localStorage.getItem('trimble-services');
    if (raw) {
      try {
        const data = JSON.parse(raw);
        if (data.accessToken) return data.accessToken;
      } catch (e) { /* ignore */ }
    }
    return localStorage.getItem('access_token')
      || sessionStorage.getItem('access_token')
      || localStorage.getItem('tc_access_token');
  }`
})
```

If user needs to login manually:
```
Prompt user: "Please login to TCWEB in the browser window, then confirm when done."
```

**Alternative (no Playwright MCP):** use the helper script (requires `pip install playwright` and `python -m playwright install chromium`):

```bash
# Fresh browser — log in when the window opens; script polls storage for up to 90s
python scripts/fetch_tcweb_token.py --env int --wait-seconds 90

# Reuse Edge login (close Edge first if the script hangs)
python scripts/fetch_tcweb_token.py --env int --use-edge-profile --wait-seconds 60
```

The script prints the bearer token on the last line; pass it to `api_client.py`. Trimble ID SSO does not populate tokens until login completes (a clean Chromium session only has theme keys until then).

### Step 4: Reproduce the Bug via API

Use the Python API client script:

```bash
python scripts/api_client.py \
  --method {HTTP_METHOD} \
  --endpoint "{API_ENDPOINT}" \
  --token "{BEARER_TOKEN}" \
  --body '{REQUEST_BODY}' \
  --output full
```

**Script Location:** `c:\Users\shanmut\.cursor\skills\bug-fix\scripts\api_client.py`

**Expected Output:**
- HTTP status code
- Response body
- Response headers
- **Root ID** (extracted from `x-amzn-trace-id` header)

### Step 5: Extract Root ID

From the API response headers, extract the X-Ray Root ID:

```
Header: x-amzn-trace-id: Root=1-6ac7fb86-58555b7f3f97b48e03f4c250;Parent=...;Sampled=...
  or: tc-request-id: Root=1-6ac7fb86-58555b7f3f97b48e03f4c250  (common on TC API responses)
Extract: Root=1-6ac7fb86-58555b7f3f97b48e03f4c250
```

`api_client.py` checks both headers automatically.

### Step 6: AWS SSO Login (mandatory)

1. Run `/aws-sso-login` for the account that matches `{ENVIRONMENT}` (dev profile for int/qa/stage, prod read-only for prod).
2. Confirm `aws sts get-caller-identity` succeeds for that profile.
3. Only then call AWS MCP tools in the matching namespace (`mcp_namespace` in `log_group_mapping.json`).

If Step 7 fails with credentials errors, stop and repeat Step 6 before retrying MCP.

### Step 7: Query CloudWatch Logs (AWS MCP only)

**Rules:**
- Use **`aws___run_script`** on the environment’s MCP namespace (`user-aws-mcp-connect-stage` or `user-aws-mcp-connect-prod`).
- Use **`call_boto3`** with `logs` → `StartQuery` and `GetQueryResults` (poll until `Complete` or `Failed`).
- **Do not** run `aws logs …` from Shell and **do not** use `cloudwatch_query.py --execute-cli` in this workflow.

#### 7a. Build the query plan (local Python, no AWS calls)

```bash
python scripts/cloudwatch_query.py \
  --root-id "Root=1-6ac7fb86-58555b7f3f97b48e03f4c250" \
  --env {ENVIRONMENT} \
  --service {SERVICE} \
  --tc-region {TC_REGION} \
  --hours 48
```

Default output is JSON: `mcp_namespace`, `aws_profile`, and one or more `queries[]` with `aws_region`, `log_group_names`, `start_time_ms`, `end_time_ms`, and `query_string`.

#### 7b. Run each query via AWS MCP

For each entry in `plan.queries`, call `aws___run_script` on `plan.mcp_namespace` with code like:

```python
import asyncio

async def run_query():
    q = {  # paste one object from plan["queries"]
        "aws_region": "us-east-1",
        "log_group_names": ["/tc/int/int-us1-app-01-server.log"],
        "start_time_ms": 0,
        "end_time_ms": 0,
        "query_string": "fields @timestamp, @message, @logStream | filter @message like /SEARCH_ID/ | sort @timestamp desc | limit 1000",
    }
    start = await call_boto3(
        service_name="logs",
        operation_name="StartQuery",
        region_name=q["aws_region"],
        params={
            "logGroupNames": q["log_group_names"],
            "startTime": q["start_time_ms"],
            "endTime": q["end_time_ms"],
            "queryString": q["query_string"],
        },
    )
    query_id = start["queryId"]
    for _ in range(45):
        await asyncio.sleep(2)
        out = await call_boto3(
            service_name="logs",
            operation_name="GetQueryResults",
            region_name=q["aws_region"],
            params={"queryId": query_id},
        )
        if out["status"] in ("Complete", "Failed", "Cancelled"):
            return {
                "query_id": query_id,
                "status": out["status"],
                "statistics": out.get("statistics"),
                "results": out.get("results", []),
            }
    return {"query_id": query_id, "status": "Timeout"}

result = await run_query()
result
```

If `aws___run_script` returns a `task_id` with status `working`, poll with **`aws___get_tasks`** until finished.

#### 7c. Interpret results

Merge `results` from all regions/services. Log excerpts go into the bug summary (Step 14).

**Trimble Connect regions per environment** (see `scripts/log_group_mapping.json` → `regions_by_environment`):

| Environment | Regions |
|-------------|---------|
| INT | us1 |
| QA | us1, eu1 |
| STAGE | us1, eu1, ap1 |
| PROD | us1, eu1, ap1, uk1 (see `deployment_notes`; ap2 omitted until deployed) |

Omit `--tc-region` in the plan command to include all configured TC regions for that environment. Set `--tc-region eu1` when the ticket specifies a region.

`log_group_mapping.json` includes `deployment_notes` for APAC naming. Re-validate log group names with `python scripts/verify_log_groups.py int qa stage prod` (uses `--execute-cli` only for verification maintenance, not during bug investigation).

**Plan helper:** `c:\Users\shanmut\.cursor\skills\bug-fix\scripts\cloudwatch_query.py` (default: JSON plan only)

**Service to Log Group Mapping:**
| API Pattern | Service | Log Groups |
|-------------|---------|------------|
| `/tc/api/2.0/*`, `/tc/api/2.1/*` | monolith | Server logs, access logs |
| `/v2/fs/*` | file-service | ECS task logs |
| `/v1/permissions/*` | permission-service | ECS task logs |
| Lambda invocations | lambdas | Lambda log groups |

**If No Logs Found:**
- Highlight "No logs found for Root ID: {ROOT_ID}"
- Proceed with code analysis using JIRA ticket details only
- Note: Some requests may not generate logs if they fail at ALB/network level

### Step 8: Analyze Codebase and Identify Root Cause

Based on the JIRA ticket, API response, and CloudWatch logs:

1. **Identify the affected code area:**
   - Use endpoint patterns to locate the controller/resource
   - Trace the code flow from controller → service → repository

2. **Repository Mapping:**
   | Service | Repository |
   |---------|------------|
   | Monolith (TCPS) | `C:\Work\trimble-connect-platform` |
   | File Service | `C:\Work\trimble-connect-files-service` |
   | Permission Service | `C:\Work\trimble-connect-permissions-service` |
   | TCPS Trigger | `C:\Work\fileservice-tcps-trigger` |

3. **Key entry points to check:**
   - Platform: `gteamapp/src/main/java/com/gehrytech/gteam/api/resource/v2/`
   - File Service: `tc-files-service/src/main/java/.../controller/`
   - Permission Service: `tc-permissions-service/src/main/java/.../controller/`

4. **Document the root cause:**
   - What code is causing the issue
   - Why it's failing (edge case, null check, race condition, etc.)
   - What the expected behavior should be

### Step 9: Implement the Fix

1. **Create the fix branch:**
   ```bash
   cd {REPOSITORY_PATH}
   git fetch origin
   git checkout -b feature/TCJARVIS-{TICKET_NUMBER}_BUGFIX origin/{TARGET_BRANCH}
   ```

2. **Implement the code fix:**
   - Make minimal, focused changes
   - Follow existing code patterns
   - Add appropriate comments

3. **Ensure no regressions:**
   - Run existing unit tests
   - Check for compilation errors

### Step 10: Add Unit Tests

Add tests that verify:
1. The bug scenario (should now pass)
2. Related edge cases
3. No regression in existing functionality

**Test file naming conventions:**
- Platform: `*Test.java` in `src/test/java/...`
- File Service: `*Test.java` in `tc-files-service/src/test/java/...`
- Permission Service: `*Test.java` in `tc-permissions-service/src/test/java/...`
- Lambdas (Python): `test_*.py` in `tests/`

### Step 11: Commit Changes

```bash
git add .
git commit -m "fix(TCJARVIS-{TICKET_NUMBER}): {SHORT_DESCRIPTION}

{DETAILED_DESCRIPTION}

Fixes: {JIRA_TICKET_URL}"
```

### Step 12: Push Branch

```bash
git push -u origin feature/TCJARVIS-{TICKET_NUMBER}_BUGFIX
```

### Step 13: Raise PR

Use the `/raise-pr` skill to create the pull request:

```
/raise-pr {TARGET_BRANCH}
```

This will:
- Analyze commits to fill PR template
- Extract JIRA ticket number
- Determine change type
- Create PR with proper description

### Step 14: Generate Bug Summary

Output a comprehensive bug summary using the template at:
`c:\Users\shanmut\.cursor\skills\bug-fix\templates\bug_summary.md`

Include:
- Root cause analysis
- Problematic code (with file path and line numbers)
- Applied fix (with file path and line numbers)
- Justification for how fix resolves the bug
- Tests added
- PR details

## Error Handling

| Scenario | Action |
|----------|--------|
| JIRA ticket not found | Error: "JIRA ticket not found. Verify the URL: {url}" |
| TCWEB login required | Prompt: "Please login to TCWEB in the browser window" |
| API call fails | Log error, continue with JIRA details for analysis |
| No CloudWatch logs | Note: "No logs found for Root ID", proceed with code analysis |
| AWS SSO expired | Run `/aws-sso-login`, verify profile, reload MCP, retry Step 7 |
| Used Shell `aws logs` | Stop; use AWS MCP `aws___run_script` only for log queries |
| Tests fail after fix | Analyze failures, adjust fix, re-run tests |
| PR already exists | Display existing PR URL |
| Branch conflict | Prompt user for resolution strategy |

## Service Detection

Automatically detect which service is affected based on API endpoint:

```python
def detect_service(endpoint: str) -> str:
    if "/v2/fs/" in endpoint or "/tc/api/2.0/files/fs" in endpoint:
        return "file-service"
    elif "/v1/permissions" in endpoint or "/v1/projects" in endpoint and "permissions" in endpoint:
        return "permission-service"
    elif "/tc/api/2.0/" in endpoint or "/tc/api/2.1/" in endpoint:
        return "monolith"
    else:
        return "unknown"
```

## Files in This Skill

| File | Purpose |
|------|---------|
| `SKILL.md` | This documentation |
| `scripts/api_client.py` | Generic HTTP client for API reproduction |
| `scripts/fetch_tcweb_token.py` | Playwright helper to read bearer token from TCWEB |
| `scripts/cloudwatch_query.py` | Builds JSON CloudWatch query plans (MCP execution); `--execute-cli` only for maintenance |
| `scripts/log_group_mapping.json` | Log group configuration |
| `templates/bug_summary.md` | Bug summary output template |

## Dependencies

### Required Skills
- `/raise-pr` - For creating pull requests
- `/aws-sso-login` - For AWS authentication

### Required MCP Servers
- `playwright` - For browser automation (TCWEB token)
- `user-aws-mcp-connect-stage` - For INT/QA/STAGE CloudWatch
- `user-aws-mcp-connect-prod` - For PROD CloudWatch
- `plugin-e-tools-mcp-E-Tools MCP` or `user-jira` - For JIRA integration

### Required Tools
- Python 3.x with `requests` library (API client + query plan helper)
- AWS CLI with SSO configured (SSO login / identity check only — not for `aws logs` in this workflow)
- Git

## Tips

1. **Log Time Range**: Default plan uses 48 hours. For older bugs, pass `--hours 72` (or more) to `cloudwatch_query.py`, then run the updated plan via MCP.

2. **Multiple Services**: Generate a plan per service (`monolith`, `file-service`, etc.) or use `--service all`, then run each MCP query block.

3. **Lambda Logs**: For async bugs, plan with `--service lambdas` and query via the same MCP pattern.

4. **SSO first**: Never call `user-aws-mcp-connect-*` for logs until `/aws-sso-login` (or equivalent) succeeds for the profile in the plan JSON.

5. **No Root ID**: If the API doesn't return a Root ID, search logs by:
   - Timestamp of the request
   - User ID or project ID from the request
   - Error message patterns

6. **Production Fixes**: For PROD bugs, use `user-aws-mcp-connect-prod` (read-only), and test fixes in INT/QA first.
