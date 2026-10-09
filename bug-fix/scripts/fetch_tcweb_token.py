#!/usr/bin/env python3
"""Fetch bearer token from TCWEB via Playwright (headed browser / Edge profile)."""

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Install: pip install playwright && python -m playwright install chromium", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
MAPPING = json.loads((SCRIPT_DIR / "log_group_mapping.json").read_text(encoding="utf-8"))

TOKEN_PROBE_JS = """
() => {
  const trimbleServices = localStorage.getItem('trimble-services');
  if (trimbleServices) {
    try {
      const data = JSON.parse(trimbleServices);
      const access = data.accessToken || data.access_token || data.token;
      if (access && access.length > 50) return { source: 'trimble-services.accessToken', token: access };
    } catch (e) { /* ignore */ }
  }
  const keys = [];
  for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
  for (let i = 0; i < sessionStorage.length; i++) keys.push('session:' + sessionStorage.key(i));
  const candidates = [
    'access_token', 'tc_access_token', 'auth_token', 'token',
    'connect_access_token', 'trimble_access_token'
  ];
  for (const k of candidates) {
    const v = localStorage.getItem(k) || sessionStorage.getItem(k);
    if (v && v.length > 20) return { source: k, token: v };
  }
  for (const k of keys) {
    if (!k) continue;
    const lk = k.replace('session:', '');
    if (/token|auth|access/i.test(lk)) {
      const store = k.startsWith('session:') ? sessionStorage : localStorage;
      const v = store.getItem(lk);
      if (v && v.length > 20 && !v.startsWith('{')) return { source: lk, token: v };
    }
  }
  return { source: null, token: null, storage_keys: keys.slice(0, 40) };
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="int", choices=["int", "qa", "stage", "prod"])
    parser.add_argument("--wait-seconds", type=int, default=90, help="Wait for manual login if needed")
    parser.add_argument("--use-edge-profile", action="store_true", help="Use default Edge user profile")
    args = parser.parse_args()

    tcweb = MAPPING["environments"][args.env]["tcweb_url"]
    api_base = MAPPING["environments"][args.env]["api_base_url"]

    with sync_playwright() as p:
        if args.use_edge_profile:
            edge_data = Path.home() / "AppData/Local/Microsoft/Edge/User Data"
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(edge_data),
                channel="msedge",
                headless=False,
                args=["--profile-directory=Default"],
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

        print(f"Opening {tcweb} — log in if prompted...", file=sys.stderr)
        page.goto(tcweb, wait_until="domcontentloaded", timeout=120_000)

        token = None
        source = None
        for remaining in range(args.wait_seconds, 0, -5):
            result = page.evaluate(TOKEN_PROBE_JS)
            if result.get("token"):
                token = result["token"]
                source = result.get("source")
                break
            page.wait_for_timeout(5000)

        if not token:
            print(json.dumps({"ok": False, "error": "No token in storage", "probe": result}, indent=2))
            if not args.use_edge_profile:
                context.close()
            sys.exit(1)

        out = {
            "ok": True,
            "env": args.env,
            "tcweb_url": tcweb,
            "token_source": source,
            "token_preview": token[:12] + "..." + token[-8:] if len(token) > 24 else "(short)",
            "access_token": token,
            "suggested_api_test": f"{api_base}/tc/api/2.0/projects",
        }
        print(json.dumps({k: v for k, v in out.items() if k != "access_token"}, indent=2))
        print(token)

        if not args.use_edge_profile:
            context.close()


if __name__ == "__main__":
    main()
