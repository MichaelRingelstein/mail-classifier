"""Outlook authorization, to run only once: `make auth` (device code flow)."""
import sys

from . import outlook

if __name__ == "__main__":
    a = outlook.app()
    flow = a.initiate_device_flow(scopes=outlook.SCOPES)
    if "user_code" not in flow:
        sys.exit(f"Failed: {flow}")
    print(flow["message"])  # "Open https://microsoft.com/devicelogin and enter the code ..."
    res = a.acquire_token_by_device_flow(flow)
    outlook.save_cache()
    if "access_token" not in res:
        sys.exit(f"Failed: {res.get('error_description', res)}")
    print("OK, signed in:", res.get("id_token_claims", {}).get("preferred_username", "Outlook account"))
