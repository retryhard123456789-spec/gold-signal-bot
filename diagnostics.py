"""
Bot Diagnostics — checks everything, auto-fixes what it can, flags the rest.
Writes diagnostics_status.json to the repo so the hourly alert workflow can read it.
"""
import json, os, time, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────
BASE = Path(__file__).parent
REPO = "retryhard123456789-spec/gold-signal-bot"

_use_env = all(os.environ.get(k) for k in ["TG_TOKEN", "TG_CHAT"])
if _use_env:
    TG_TOKEN = os.environ["TG_TOKEN"]
    TG_CHAT  = os.environ["TG_CHAT"]
    GH_TOKEN = os.environ.get("GH_TOKEN", "")
else:
    _cfg     = json.loads((BASE / "config.json").read_text())
    TG_TOKEN = _cfg["telegram_token"]
    TG_CHAT  = _cfg["telegram_chat_id"]
    GH_TOKEN = ""

HDRS_GH = {
    "Authorization": f"token {GH_TOKEN}",
    "User-Agent": "GoldBot",
    "Accept": "application/vnd.github+json",
}

SIG_FILE    = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"
CFG_FILE    = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "bot_config.json"
STATUS_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "diagnostics_status.json"

DEFAULT_CFG = {"paused": False, "risk_dollars": 16, "min_rr": 1.5, "tp1_min_rr": 1.8}

# ── Telegram helpers ──────────────────────────────────────────
def tg_send(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        return r.ok, r.json()
    except Exception as e:
        return False, str(e)

# ── GitHub helpers ────────────────────────────────────────────
def gh_get(path):
    try:
        r = requests.get(f"https://api.github.com{path}", headers=HDRS_GH, timeout=15)
        return r.ok, r.json()
    except Exception as e:
        return False, str(e)

def gh_put(path, payload):
    try:
        r = requests.put(f"https://api.github.com{path}", headers=HDRS_GH,
                         data=json.dumps(payload), timeout=15)
        return r.ok, r.json()
    except Exception as e:
        return False, str(e)

def gh_dispatch(workflow):
    try:
        r = requests.post(
            f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches",
            headers=HDRS_GH, data=json.dumps({"ref": "main"}), timeout=15)
        return r.status_code in (200, 204)
    except:
        return False

def get_file_sha(filename):
    ok, d = gh_get(f"/repos/{REPO}/contents/{filename}")
    if ok and isinstance(d, dict):
        return d.get("sha")
    return None

def push_json_file(filename, content_dict, msg):
    import base64
    sha = get_file_sha(filename)
    body = {
        "message": msg,
        "content": base64.b64encode(json.dumps(content_dict, indent=2).encode()).decode()
    }
    if sha:
        body["sha"] = sha
    ok, _ = gh_put(f"/repos/{REPO}/contents/{filename}", body)
    return ok

# ── Individual checks ─────────────────────────────────────────
results = []   # list of (check_name, status, detail, fixed)

def record(name, ok, detail, fixed=False):
    results.append({"check": name, "ok": ok, "detail": detail, "fixed": fixed})
    icon = "✅" if ok else ("🔧 Fixed" if fixed else "❌")
    print(f"  {icon}  {name}: {detail}")

def check_telegram():
    ok, resp = tg_send("🔧 <b>Diagnostics running...</b> (connectivity check)")
    if ok:
        record("Telegram", True, "Connected and sending OK")
    else:
        record("Telegram", False, f"Send failed: {str(resp)[:80]}")

def check_github_api():
    ok, resp = gh_get("/repos/" + REPO)
    if ok and isinstance(resp, dict) and "name" in resp:
        record("GitHub API", True, "Token valid, repo accessible")
    else:
        code = resp.get("message", str(resp))[:80] if isinstance(resp, dict) else str(resp)[:80]
        record("GitHub API", False, f"Access failed: {code}")

def check_yfinance():
    try:
        import yfinance as yf
        t = yf.Ticker("GC=F")
        price = float(t.fast_info.last_price)
        if price and price > 0:
            record("yfinance / Market Data", True, f"XAUUSD price: {round(price, 2)}")
        else:
            record("yfinance / Market Data", False, "Got price=0 or None")
    except Exception as e:
        record("yfinance / Market Data", False, f"Error: {str(e)[:80]}")

def check_signals_json():
    ok, d = gh_get(f"/repos/{REPO}/contents/signals.json")
    if not ok:
        record("signals.json", False, "Cannot read file from GitHub")
        return
    try:
        import base64
        raw = base64.b64decode(d["content"].replace("\n", "")).decode()
        sigs = json.loads(raw)
        if not isinstance(sigs, list):
            raise ValueError("not a list")
        # Check for stale pending signals (>24h)
        now = datetime.now(timezone.utc)
        stale = [s for s in sigs if s.get("status") == "pending"
                 and (now - datetime.fromisoformat(s["signal_ts"].replace("Z","+00:00"))).total_seconds() > 172800]
        if stale:
            for s in stale:
                s["status"] = "expired"
            push_json_file("signals.json", sigs, f"diag: expire {len(stale)} stale pending signal(s)")
            record("signals.json", True,
                   f"Valid — {len(sigs)} signals. Auto-expired {len(stale)} stale pending.", fixed=True)
        else:
            record("signals.json", True, f"Valid — {len(sigs)} signals, no stale entries")
    except Exception as e:
        # Try to reset
        fixed = push_json_file("signals.json", [], "diag: reset corrupted signals.json")
        record("signals.json", False, f"Corrupted: {e}. Reset to [].", fixed=fixed)

def check_bot_config():
    ok, d = gh_get(f"/repos/{REPO}/contents/bot_config.json")
    if not ok:
        fixed = push_json_file("bot_config.json", DEFAULT_CFG, "diag: recreate missing bot_config.json")
        record("bot_config.json", False, "Missing — recreated with defaults.", fixed=fixed)
        return
    try:
        import base64
        raw = base64.b64decode(d["content"].replace("\n", "")).decode()
        cfg = json.loads(raw)
        issues = []
        for k, v in DEFAULT_CFG.items():
            if k not in cfg:
                cfg[k] = v
                issues.append(f"missing '{k}'")
        if issues:
            push_json_file("bot_config.json", cfg, f"diag: fix config keys: {', '.join(issues)}")
            record("bot_config.json", True, f"Fixed missing keys: {', '.join(issues)}", fixed=True)
        else:
            paused = cfg.get("paused", False)
            record("bot_config.json", True, f"Valid — paused={paused}, risk=${cfg.get('risk_dollars')}")
    except Exception as e:
        fixed = push_json_file("bot_config.json", DEFAULT_CFG, "diag: reset corrupted bot_config.json")
        record("bot_config.json", False, f"Corrupted: {e}. Reset to defaults.", fixed=fixed)

def check_workflows():
    for wf_name, wf_file in [("Multi-Pair Analyzer", "analyzer.yml"), ("Signal Monitor", "monitor.yml")]:
        ok, resp = gh_get(f"/repos/{REPO}/actions/workflows/{wf_file}/runs?per_page=1")
        if not ok or not isinstance(resp, dict):
            record(f"Workflow: {wf_name}", False, "Cannot query workflow runs")
            continue
        runs = resp.get("workflow_runs", [])
        if not runs:
            record(f"Workflow: {wf_name}", False, "No runs found — never ran")
            continue
        last = runs[0]
        conclusion = last.get("conclusion", "in_progress")
        created_at = last.get("created_at", "")
        status = last.get("status", "unknown")
        try:
            run_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - run_time).total_seconds() / 3600
            age_str = f"{age_h:.1f}h ago"
        except:
            age_h = 999
            age_str = "unknown"

        # Monitor should have run within 8h, analyzer within 36h
        max_age = 8 if wf_file == "monitor.yml" else 36
        if conclusion == "failure":
            # Try to re-trigger it
            triggered = gh_dispatch(wf_file)
            record(f"Workflow: {wf_name}", False,
                   f"Last run FAILED {age_str}. {'Re-triggered ✅' if triggered else 'Re-trigger failed ❌'}", fixed=triggered)
        elif age_h > max_age and status != "in_progress":
            triggered = gh_dispatch(wf_file)
            record(f"Workflow: {wf_name}", False,
                   f"Last run {age_str} (max {max_age}h). {'Re-triggered ✅' if triggered else 'Re-trigger failed ❌'}", fixed=triggered)
        else:
            record(f"Workflow: {wf_name}", True,
                   f"Last run: {conclusion} — {age_str}")

# ── Main ──────────────────────────────────────────────────────
def run_diagnostics():
    print("=== Bot Diagnostics ===")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    check_telegram()
    check_github_api()
    check_yfinance()
    check_signals_json()
    check_bot_config()
    check_workflows()

    passed  = [r for r in results if r["ok"] or r["fixed"]]
    failed  = [r for r in results if not r["ok"] and not r["fixed"]]
    fixed   = [r for r in results if r["fixed"]]

    print(f"\n  Passed: {len(passed)} / {len(results)}")
    if fixed:
        print(f"  Auto-fixed: {len(fixed)}")
    if failed:
        print(f"  Needs human: {len(failed)}")

    # Build Telegram summary
    lines = [f"🔧 <b>Diagnostics Report</b>\n{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}\n"]
    for r in results:
        if r["ok"] and not r["fixed"]:
            lines.append(f"✅ {r['check']}: {r['detail']}")
        elif r["fixed"]:
            lines.append(f"🔧 {r['check']}: {r['detail']}")
        else:
            lines.append(f"❌ {r['check']}: {r['detail']}")

    if failed:
        lines.append(f"\n⚠️ <b>{len(failed)} issue(s) need human assistance.</b>")
        lines.append("You'll receive hourly reminders until resolved.")
    else:
        lines.append("\n✅ <b>All systems operational.</b>")

    tg_send("\n".join(lines))

    # Write status file so hourly_alert.yml can read it
    status = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "all_ok": len(failed) == 0,
        "needs_human": [{"check": r["check"], "detail": r["detail"]} for r in failed],
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2))
    print(f"\nStatus written to {STATUS_FILE}")

if __name__ == "__main__":
    run_diagnostics()
