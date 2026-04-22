"""One-shot: sync Render env vars to match our Lemon Squeezy setup.

- Reads LS_* + LEMON_SQUEEZY_* values from local .env
- PUTs the full env-var list to Render (replaces all existing vars on the service)
- Preserves all non-PayPal keys; drops PAYPAL_* keys
- Triggers a redeploy
"""
import os, sys, json
import requests
from dotenv import dotenv_values

API = "https://api.render.com/v1"
KEY = os.environ["RENDER_API_KEY"]
SVC = os.environ["RENDER_SERVICE_ID"]
H = {"Authorization": f"Bearer {KEY}", "Accept": "application/json", "Content-Type": "application/json"}

# 1. Pull current env vars on Render.
r = requests.get(f"{API}/services/{SVC}/env-vars?limit=100", headers=H, timeout=30)
r.raise_for_status()
current = {item["envVar"]["key"]: item["envVar"]["value"] for item in r.json()}
print(f"[render] {len(current)} env vars currently set")

# 2. Load our local .env to source the LS values.
local = dotenv_values(".env")

LS_KEYS = [
    "LEMON_SQUEEZY_API_KEY", "LEMON_SQUEEZY_STORE_ID", "LEMON_SQUEEZY_WEBHOOK_SECRET",
    "LS_VARIANT_GROWTH", "LS_VARIANT_PRO", "LS_VARIANT_UNLIMITED",
    "LS_VARIANT_STUDENT_PLUS", "LS_VARIANT_STUDENT_ULTIMATE",
    "LS_VARIANT_COIN_SMALL", "LS_VARIANT_COIN_MEDIUM", "LS_VARIANT_COIN_LARGE",
    "LS_VARIANT_COIN_MEGA", "LS_VARIANT_COIN_ULTRA",
]
PAYPAL_DROP = [
    "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID",
    "PAYPAL_PLAN_GROWTH", "PAYPAL_PLAN_PRO", "PAYPAL_PLAN_UNLIMITED", "PAYPAL_MODE",
]

# 3. Build the new desired set: keep current except PayPal, then upsert LS values.
desired = {k: v for k, v in current.items() if k not in PAYPAL_DROP}
added, updated = [], []
for k in LS_KEYS:
    val = (local.get(k) or "").strip()
    if not val:
        # Skip empty values so Render doesn't get blank entries.
        continue
    if k not in desired:
        added.append(k)
    elif desired[k] != val:
        updated.append(k)
    desired[k] = val

dropped = [k for k in PAYPAL_DROP if k in current]

print(f"[plan] add={added}")
print(f"[plan] update={updated}")
print(f"[plan] drop={dropped}")

# 4. PUT the full list (Render replaces atomically).
payload = [{"key": k, "value": v} for k, v in desired.items()]
r = requests.put(f"{API}/services/{SVC}/env-vars", headers=H, data=json.dumps(payload), timeout=30)
print(f"[render] PUT env-vars -> {r.status_code}")
if r.status_code >= 300:
    print(r.text)
    sys.exit(1)

# 5. Trigger redeploy.
r = requests.post(f"{API}/services/{SVC}/deploys", headers=H, data=json.dumps({"clearCache":"do_not_clear"}), timeout=30)
print(f"[render] deploy trigger -> {r.status_code}")
print(r.text[:300])
