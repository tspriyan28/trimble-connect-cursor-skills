#!/usr/bin/env python3
"""
Trimble Connect API Client

A generic HTTP client for reproducing API bugs in Trimble Connect services.
Supports all HTTP methods and extracts X-Ray trace IDs (Root IDs) from response headers.

Usage:
    python api_client.py --method GET --endpoint "https://app.int.connect.trimble.com/tc/api/2.0/projects" --token "your_bearer_token"
    python api_client.py --method POST --endpoint "https://app.int.connect.trimble.com/tc/api/2.0/files" --token "token" --body '{"name": "test"}'
"""

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("Error: 'requests' library not installed. Run: pip install requests")
    sys.exit(1)


# X-Ray Root ID pattern: Root=1-6ac7fb86-58555b7f3f97b48e03f4c250
ROOT_ID_PATTERN = re.compile(r"Root=([0-9a-f-]+)", re.IGNORECASE)

# Common headers for Trimble Connect APIs
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "TCJarvis-BugFix/1.0"
}


class APIResponse:
    """Container for API response data"""
    
    def __init__(
        self,
        status_code: int,
        body: Any,
        headers: Dict[str, str],
        root_id: Optional[str],
        elapsed_ms: float
    ):
        self.status_code = status_code
        self.body = body
        self.headers = headers
        self.root_id = root_id
        self.elapsed_ms = elapsed_ms
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status_code": self.status_code,
            "body": self.body,
            "headers": dict(self.headers),
            "root_id": self.root_id,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


def extract_root_id(headers: Dict[str, str]) -> Optional[str]:
    """
    Extract X-Ray Root ID from response headers.
    
    The Root ID is typically found in the 'x-amzn-trace-id' header.
    Format: Root=1-6ac7fb86-58555b7f3f97b48e03f4c250;Parent=...;Sampled=...
    
    Args:
        headers: Response headers dictionary
        
    Returns:
        Root ID string if found, None otherwise
    """
    normalized = {k.lower(): v for k, v in headers.items()}
    trace_header = (
        normalized.get("x-amzn-trace-id")
        or normalized.get("tc-request-id")
        or ""
    )

    if trace_header:
        match = ROOT_ID_PATTERN.search(trace_header)
        if match:
            return f"Root={match.group(1)}"
    
    return None


def make_request(
    method: str,
    endpoint: str,
    access_token: str,
    body: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    verify_ssl: bool = True
) -> APIResponse:
    """
    Make an HTTP request to a Trimble Connect API endpoint.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        endpoint: Full URL of the API endpoint
        access_token: Bearer token for authentication
        body: Optional JSON body for POST/PUT/PATCH requests
        extra_headers: Optional additional headers
        timeout: Request timeout in seconds
        verify_ssl: Whether to verify SSL certificates
        
    Returns:
        APIResponse object containing response data
    """
    # Build headers
    headers = DEFAULT_HEADERS.copy()
    headers["Authorization"] = f"Bearer {access_token}"
    
    if extra_headers:
        headers.update(extra_headers)
    
    # Parse body if provided
    json_body = None
    if body:
        try:
            json_body = json.loads(body)
        except json.JSONDecodeError:
            # If not valid JSON, send as raw body
            headers["Content-Type"] = "text/plain"
    
    # Make the request
    method = method.upper()
    
    try:
        response = requests.request(
            method=method,
            url=endpoint,
            headers=headers,
            json=json_body if json_body else None,
            data=body if body and not json_body else None,
            timeout=timeout,
            verify=verify_ssl
        )
        
        # Parse response body
        try:
            response_body = response.json()
        except json.JSONDecodeError:
            response_body = response.text
        
        # Extract Root ID
        root_id = extract_root_id(dict(response.headers))
        
        return APIResponse(
            status_code=response.status_code,
            body=response_body,
            headers=dict(response.headers),
            root_id=root_id,
            elapsed_ms=response.elapsed.total_seconds() * 1000
        )
        
    except requests.exceptions.Timeout:
        return APIResponse(
            status_code=0,
            body={"error": "Request timed out", "timeout_seconds": timeout},
            headers={},
            root_id=None,
            elapsed_ms=timeout * 1000
        )
    except requests.exceptions.ConnectionError as e:
        return APIResponse(
            status_code=0,
            body={"error": "Connection failed", "details": str(e)},
            headers={},
            root_id=None,
            elapsed_ms=0
        )
    except Exception as e:
        return APIResponse(
            status_code=0,
            body={"error": "Request failed", "details": str(e)},
            headers={},
            root_id=None,
            elapsed_ms=0
        )


def validate_endpoint(endpoint: str) -> Tuple[bool, str]:
    """Validate the endpoint URL format."""
    try:
        parsed = urlparse(endpoint)
        if not parsed.scheme:
            return False, "Missing URL scheme (http/https)"
        if not parsed.netloc:
            return False, "Missing domain/host"
        if parsed.scheme not in ("http", "https"):
            return False, f"Invalid scheme: {parsed.scheme}"
        return True, ""
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Trimble Connect API Client for bug reproduction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # GET request
  python api_client.py --method GET --endpoint "https://app.int.connect.trimble.com/tc/api/2.0/projects" --token "your_token"
  
  # POST request with body
  python api_client.py --method POST --endpoint "https://app.int.connect.trimble.com/tc/api/2.0/files" --token "token" --body '{"name": "test.txt"}'
  
  # With extra headers
  python api_client.py --method GET --endpoint "https://..." --token "token" --headers '{"X-Custom": "value"}'
        """
    )
    
    parser.add_argument(
        "--method", "-m",
        required=True,
        choices=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        help="HTTP method"
    )
    
    parser.add_argument(
        "--endpoint", "-e",
        required=True,
        help="Full API endpoint URL"
    )
    
    parser.add_argument(
        "--token", "-t",
        required=True,
        help="Bearer access token"
    )
    
    parser.add_argument(
        "--body", "-b",
        help="Request body (JSON string)"
    )
    
    parser.add_argument(
        "--headers", "-H",
        help="Additional headers (JSON object string)"
    )
    
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)"
    )
    
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification"
    )
    
    parser.add_argument(
        "--output", "-o",
        choices=["json", "summary", "full"],
        default="full",
        help="Output format (default: full)"
    )
    
    args = parser.parse_args()
    
    # Validate endpoint
    is_valid, error = validate_endpoint(args.endpoint)
    if not is_valid:
        print(f"Error: Invalid endpoint URL - {error}", file=sys.stderr)
        sys.exit(1)
    
    # Parse extra headers if provided
    extra_headers = None
    if args.headers:
        try:
            extra_headers = json.loads(args.headers)
        except json.JSONDecodeError:
            print("Error: Invalid JSON for --headers", file=sys.stderr)
            sys.exit(1)
    
    # Make the request
    print(f"Making {args.method} request to: {args.endpoint}", file=sys.stderr)
    
    response = make_request(
        method=args.method,
        endpoint=args.endpoint,
        access_token=args.token,
        body=args.body,
        extra_headers=extra_headers,
        timeout=args.timeout,
        verify_ssl=not args.no_verify_ssl
    )
    
    # Output results
    if args.output == "json":
        print(response.to_json())
    elif args.output == "summary":
        print(f"Status: {response.status_code}")
        print(f"Root ID: {response.root_id or 'Not found'}")
        print(f"Elapsed: {response.elapsed_ms:.2f}ms")
        if response.status_code >= 400:
            print(f"Error: {json.dumps(response.body, indent=2)}")
    else:  # full
        print("=" * 60)
        print("API RESPONSE")
        print("=" * 60)
        print(f"Status Code: {response.status_code}")
        print(f"Elapsed: {response.elapsed_ms:.2f}ms")
        print(f"Root ID: {response.root_id or 'Not found in headers'}")
        print("-" * 60)
        print("HEADERS:")
        for key, value in sorted(response.headers.items()):
            print(f"  {key}: {value}")
        print("-" * 60)
        print("BODY:")
        if isinstance(response.body, dict):
            print(json.dumps(response.body, indent=2))
        else:
            print(response.body)
        print("=" * 60)
    
    # Exit with appropriate code
    if response.status_code == 0:
        sys.exit(2)  # Connection error
    elif response.status_code >= 500:
        sys.exit(1)  # Server error
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
