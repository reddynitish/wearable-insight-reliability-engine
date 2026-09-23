# Google Health API client (Fitbit Air data)

The Fitbit Air exposes no motion data over BLE (see ../FITBIT_AIR_RESEARCH.md).
This is the supported route to your data: tracker -> Google Health app on your phone
-> Google Health API -> here.

The legacy Fitbit Web API sunsets September 2026; this uses its replacement,
`https://health.googleapis.com/v4/`.

## One-time setup

1. Go to https://developers.google.com/health/setup and click
   **"Enable the API and get an OAuth 2.0 Client ID"**.
2. Create an OAuth client:
   - Application type: **Web application**
   - Authorized redirect URI: `https://www.google.com`  (the wizard rejects http:// schemes)
   - Download the credentials JSON -> save it here as `credentials.json`
3. Google Cloud Console -> **Audience**:
   - Publishing status: **Testing**
   - User type: **External**
   - **Test users** -> **+ Add users** -> add your own Google account
4. Google Cloud Console -> **Data Access** -> **Add or remove scopes** ->
   search "Google Health API" -> select
   `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
   -> **Update** -> **Save**

Every Google Health scope is classified **Restricted**, which normally triggers a
third-party security review. Publishing status **Testing** with yourself as a test user
skips that review entirely. It only applies to apps going public or past 100 users.

## Use

    cd /Users/<user>/Projects/fitbit-air-research
    ./.venv/bin/python google_health/gh_auth.py          # one-time browser authorization
    ./.venv/bin/python google_health/gh_fetch.py --days 7
    ./.venv/bin/python google_health/gh_fetch.py --types steps,heart-rate --days 30
    ./.venv/bin/python google_health/gh_fetch.py --rollup heart-rate --window 60 --days 7

Output: `google_health/data/*.json` and matching `.csv`.

## What you can and cannot get

Available: steps, distance, calories, heart rate, resting HR, HRV, VO2 max,
respiratory rate, SpO2, active minutes, exercise/workouts, sleep with stages.

**Not available at any tier: raw accelerometer or gyroscope data.** It is absent from
both the legacy Web API and the Google Health API. Finest granularity anywhere is
1-second heart rate and 1-minute steps. Gesture recognition (typing / waving / punching /
drinking / wrist rotation / lifting) needs ~20-50 Hz 3-axis accelerometer, so it is not
achievable through this API.

rollUp window limits: 14 days max range for heart-rate and calories, 90 days for most others.
