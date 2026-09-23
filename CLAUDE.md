# Project instructions

Read `PROJECT_BRIEF.md` completely before planning or changing this repository. It is the authoritative specification for the Wearable Insight Reliability Engine.

Also read:

- `FITBIT_AIR_RESEARCH.md` before proposing any Bluetooth or raw-motion work;
- `google_health/README.md` before changing the supported Fitbit/Google Health data path.

Important constraints:

- Do not attempt pairing bypass, credential extraction, key guessing, or private Fitbit-channel access.
- Do not read, print, commit, or expose OAuth credentials, tokens, personal health exports, or other secrets.
- Do not turn the product into a generic chatbot, diagnostic tool, or another recovery dashboard.
- The engine must produce typed, claim-level reliability decisions and fail closed when evidence is insufficient.
- Use only verified, reproducible results in documentation or resume language. Never fabricate metrics.
- Preserve the completed BLE research and keep new engine code logically separate from experimental capture artifacts.
