# Update-check counter (#806)

Anonymous count of update checks, to estimate active installs and retention per version and OS without collecting
personal data or an install ID.

- The Windows app checks for updates at most once a day (`UPDATE_CHECK_INTERVAL_H = 24` in
  `tinglongwu/gui/autoupdate.py`), and only when auto-update is on. That check asks this Worker
  `GET /v1/latest?v=<app version>&os=<windows|mac|linux>`.
- The Worker returns GitHub's `releases/latest` JSON unchanged and adds 1 to a KV counter keyed only by
  `c:<UTC date>:<version>:<os>`. It never reads the IP, user agent, cookies or any other header; unknown values are
  stored as `other`. Counters expire after ~13 months. Request logs (Workers Logs) are off in `wrangler.toml`.
- A manual "Check for updates" click and the installer download go straight to GitHub and are never counted.
  Worker unreachable or a bad answer -> the app asks GitHub exactly as before. The Worker's answer is only used to
  decide "is there a newer version"; what gets installed always comes from GitHub and is SHA256-checked.
- `GET /v1/stats` returns `[{date, version, os, n}]`; `scripts/stats_dashboard.py` shows it as "Active installs"
  (checks per day ~ active installs that day; 7-day average per version).

## Test

    node --test        # in this folder, no npm packages needed (also run by tests/test_update_counter.py)

## Deploy (one time, Cloudflare account owner)

    npx wrangler kv namespace create COUNTS     # put the printed id into wrangler.toml
    npx wrangler deploy                          # prints https://tlwu-update.<account>.workers.dev
    curl -s "https://tlwu-update.<account>.workers.dev/v1/latest?v=0.3.4&os=linux" | head -c 200   # same JSON as
    curl -s https://api.github.com/repos/sunososobro-hub/tinglongwu/releases/latest | head -c 200

Then set `COUNTER_BASE` in `tinglongwu_setup/selfupdate.py` to that https URL (one-line PR). Until then
`COUNTER_BASE` is empty and every check goes straight to GitHub, as before (no request to an unowned host).
