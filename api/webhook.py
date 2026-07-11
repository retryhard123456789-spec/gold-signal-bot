"""
Telegram webhook handler — deployed on Vercel (free, serverless, always-on).
Handles bot commands and delegates to GitHub Actions / GitHub repo API.
"""
import json, os, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
REPO     = "retryhard123456789-spec/gold-signal-bot"
GH_API   = "https://api.github.com"

HELP = (
    "\U0001f916 <b>Bot Commands</b>\n\n"
    "<b>now</b> — scan all 16 pairs immediately\n"
    "<b>risk 20</b> — set risk per trade to $20\n"
    "<b>pause</b> — pause signal generation\n"
    "<b>resume</b> — resume signal generation\n"
    "<b>status</b> — show open signals\n"
    "<b>cmd</b> — show this list"
)


# ── GitHub helpers ────────────────────────────────────────────
def gh_get(path):
    req = urllib.request.Request(
        f"{GH_API}{path}",
        headers={"Authorization": f"token {GH_TOKEN}",
                 "User-Agent": "GoldBot", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def gh_put(path, body: dict):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method="PUT",
        headers={"Authorization": f"token {GH_TOKEN}",
                 "User-Agent": "GoldBot", "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def gh_post(path, body: dict):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{GH_API}{path}", data=data, method="POST",
        headers={"Authorization": f"token {GH_TOKEN}",
                 "User-Agent": "GoldBot", "Content-Type": "application/json",
                 "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def get_file(filename):
    import base64
    d = gh_get(f"/repos/{REPO}/contents/{filename}")
    content = json.loads(base64.b64decode(d["content"]).decode())
    return content, d["sha"]

def put_file(filename, content_dict, sha, msg="update"):
    import base64
    encoded = base64.b64encode(json.dumps(content_dict, indent=2).encode()).decode()
    gh_put(f"/repos/{REPO}/contents/{filename}",
           {"message": msg, "content": encoded, "sha": sha})

def dispatch_workflow(workflow="analyzer.yml"):
    return gh_post(f"/repos/{REPO}/actions/workflows/{workflow}/dispatches",
                   {"ref": "main"})


# ── Telegram helper ───────────────────────────────────────────
def tg_send(chat_id, text):
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10): pass
    except Exception:
        pass


# ── Command logic ─────────────────────────────────────────────
def handle(text: str, chat_id: str):
    text = text.strip().lower().lstrip("/")

    if text in ("cmd", "help", "commands", "start"):
        tg_send(chat_id, HELP)

    elif text == "now":
        tg_send(chat_id, "Triggering scan on all 16 pairs...")
        code = dispatch_workflow("analyzer.yml")
        if code in (200, 204):
            tg_send(chat_id, "Scan started! You will get signals in ~2 min if setups are found.")
        else:
            tg_send(chat_id, f"Dispatch failed (HTTP {code}). Check GitHub Actions.")

    elif text.startswith("risk"):
        parts = text.split()
        if len(parts) == 2 and parts[1].replace(".", "").isdigit():
            val = float(parts[1])
            try:
                cfg, sha = get_file("bot_config.json")
                old = cfg.get("risk_dollars", 16)
                cfg["risk_dollars"] = val
                put_file("bot_config.json", cfg, sha, f"risk set to {val}")
                tg_send(chat_id, f"Risk updated: ${old} to <b>${val}</b> per trade")
            except Exception as e:
                tg_send(chat_id, f"Could not update risk: {e}")
        else:
            tg_send(chat_id, "Usage: <b>risk 20</b>")

    elif text == "pause":
        try:
            cfg, sha = get_file("bot_config.json")
            cfg["paused"] = True
            put_file("bot_config.json", cfg, sha, "bot paused")
            tg_send(chat_id, "Bot <b>paused</b>. Send <b>resume</b> to restart.")
        except Exception as e:
            tg_send(chat_id, f"Error: {e}")

    elif text == "resume":
        try:
            cfg, sha = get_file("bot_config.json")
            cfg["paused"] = False
            put_file("bot_config.json", cfg, sha, "bot resumed")
            tg_send(chat_id, "Bot <b>resumed</b>. Scanning on schedule.")
        except Exception as e:
            tg_send(chat_id, f"Error: {e}")

    elif text == "status":
        try:
            import base64
            d = gh_get(f"/repos/{REPO}/contents/signals.json")
            sigs = json.loads(base64.b64decode(d["content"]).decode())
            active = [s for s in sigs if s.get("status") in ("active", "pending")]
            if not active:
                tg_send(chat_id, "No active or pending signals right now.")
                return
            lines = [f"<b>{len(active)} signal(s) open:</b>"]
            for s in active:
                lines.append(
                    f"- {s['symbol']} <b>{s['direction']}</b> [{s['status']}]\n"
                    f"  Entry:{s['entry']} SL:{s['sl']} TP1:{s['tp1']} R:R 1:{s['rr']}"
                )
            tg_send(chat_id, "\n".join(lines))
        except Exception as e:
            tg_send(chat_id, f"Could not fetch signals: {e}")

    else:
        tg_send(chat_id, f"Unknown command: <b>{text}</b>\nSend <b>cmd</b> for the list.")


# ── Vercel entrypoint (new Python runtime) ────────────────────
def handler(request):
    """Vercel Python handler — receives HTTP Request object."""
    if request.method == "GET":
        return Response("Gold Bot webhook is running.", status=200)

    try:
        body   = request.body
        update = json.loads(body) if isinstance(body, (str, bytes)) else body
        msg     = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()
        if chat_id and text and chat_id in str(TG_CHAT):
            handle(text, chat_id)
    except Exception:
        pass

    return Response("ok", status=200)


class Response:
    def __init__(self, body="", status=200):
        self.body   = body
        self.status = status
