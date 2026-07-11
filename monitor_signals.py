"""
Signal monitor — checks active signals against live price every 5 min.
Handles TP1/TP2/TP3 hits, SL, BE move, trailing stop.
"""
import json, logging, time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE     = Path(__file__).parent
import os
_use_env = all(os.environ.get(k) for k in ["TD_KEY","TG_TOKEN","TG_CHAT"])
if _use_env:
    CFG = {"twelve_data_api_key": os.environ["TD_KEY"],
           "telegram_token": os.environ["TG_TOKEN"],
           "telegram_chat_id": os.environ["TG_CHAT"]}
else:
    CFG = json.loads((BASE / "config.json").read_text())
SIG_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"
LOG_FILE = BASE / "logs" / "monitor.log"
os.makedirs(BASE / "logs", exist_ok=True)

TG_TOKEN = CFG["telegram_token"]
TG_CHAT  = CFG["telegram_chat_id"]
TD_KEY   = CFG["twelve_data_api_key"]
SYMBOL   = "XAU/USD"
PIP      = 0.1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler()]
)
log = logging.getLogger(__name__)

def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
    except Exception as e:
        log.error(f"Telegram failed: {e}")

def get_price():
    try:
        r = requests.get("https://api.twelvedata.com/price",
            params=dict(symbol=SYMBOL, apikey=TD_KEY), timeout=15)
        d = r.json()
        return float(d["price"]) if "price" in d else None
    except: return None

def load_signals():
    try: return json.loads(SIG_FILE.read_text())
    except: return []

def save_signals(sigs):
    SIG_FILE.write_text(json.dumps(sigs, indent=2, default=str))

def monitor():
    sigs = load_signals()
    price = get_price()
    if price is None:
        log.warning("Could not fetch price"); return

    log.info(f"Price: {price}  Active signals: {sum(1 for s in sigs if s['status'] in ('active','pending'))}")
    changed = False

    for s in sigs:
        if s["status"] not in ("active", "pending"): continue
        d = s["direction"]; entry = s["entry"]

        # Pending → activate when price reaches entry zone
        if s["status"] == "pending":
            triggered = (price <= entry if d == "BUY" else price >= entry)
            if triggered:
                s["status"] = "active"
                s["entry_ts"] = datetime.now(timezone.utc).isoformat()
                log.info(f"Signal ACTIVATED: {d} @ {entry}")
                send_telegram(f"⚡ <b>Entry Triggered!</b>\n{d} {SYMBOL} @ <b>{entry}</b>\nSL: {s['sl']} | TP1: {s['tp1']}")
                changed = True
            continue

        eff_sl = s.get("eff_sl", s["sl"])
        tp1 = s["tp1"]; tp2 = s.get("tp2"); tp3 = s.get("tp3")
        tp1_hit = s.get("tp1_hit", False); tp2_hit = s.get("tp2_hit", False)
        sl_dist = abs(entry - s["sl"])

        def hit_tp(lvl): return price <= lvl if d == "SELL" else price >= lvl
        def hit_sl(lvl): return price >= lvl if d == "SELL" else price <= lvl

        # TP1
        if not tp1_hit and hit_tp(tp1):
            s["tp1_hit"] = True; s["eff_sl"] = entry
            log.info(f"TP1 hit @ {tp1}")
            send_telegram(
                f"🥇 <b>TP1 Hit!</b>\n{d} {SYMBOL}\n"
                f"TP1: <b>{tp1}</b> ✅ (50% closed)\n"
                f"🔁 Move SL to entry <b>{entry}</b> — now at breakeven"
            )
            changed = True; continue

        # TP2
        if tp1_hit and tp2 and not tp2_hit and hit_tp(tp2):
            s["tp2_hit"] = True; s["eff_sl"] = tp1
            log.info(f"TP2 hit @ {tp2}")
            send_telegram(
                f"🥈 <b>TP2 Hit!</b>\n{d} {SYMBOL}\n"
                f"TP2: <b>{tp2}</b> ✅ (30% closed)\n"
                f"🔁 Trail SL to TP1 <b>{tp1}</b>"
            )
            changed = True; continue

        # TP3
        if tp2_hit and tp3 and hit_tp(tp3):
            pnl = round(0.5*abs(tp1-entry)/sl_dist + 0.3*abs(tp2-entry)/sl_dist + 0.2*abs(tp3-entry)/sl_dist, 2)
            s["status"] = "closed"; s["outcome"] = "TP3"; s["pnl_r"] = pnl
            log.info(f"TP3 hit — full close +{pnl}R")
            send_telegram(
                f"🏆 <b>TP3 Hit — Full Close!</b>\n{d} {SYMBOL}\n"
                f"TP3: <b>{tp3}</b> ✅\n"
                f"💰 Total: <b>+{pnl}R</b> 🎉"
            )
            # Import and record win cooldown
            try:
                import sys; sys.path.insert(0, str(BASE))
                from trading_analyzer import record_win
                record_win(SYMBOL, d)
            except: pass
            changed = True; continue

        # SL / BE / Trail
        if hit_sl(eff_sl):
            if eff_sl == entry:
                s["status"] = "closed"; s["outcome"] = "BE"
                pnl = round(0.5*abs(tp1-entry)/sl_dist, 2)
                log.info(f"BE close +{pnl}R")
                send_telegram(f"⚡ <b>Breakeven Close</b>\n{d} {SYMBOL}\n50% locked at TP1 → <b>+{pnl}R</b>")
                try:
                    from trading_analyzer import record_win; record_win(SYMBOL, d)
                except: pass
            elif eff_sl == tp1:
                pnl = round(0.5*abs(tp1-entry)/sl_dist + 0.3*abs(tp1-entry)/sl_dist, 2)
                s["status"] = "closed"; s["outcome"] = "TRAIL"
                log.info(f"Trail close +{pnl}R")
                send_telegram(f"📉 <b>Trailing Stop Hit</b>\n{d} {SYMBOL}\nClosed at TP1 trail → <b>+{pnl}R</b>")
                try:
                    from trading_analyzer import record_win; record_win(SYMBOL, d)
                except: pass
            else:
                s["status"] = "closed"; s["outcome"] = "SL"; s["pnl_r"] = -1.0
                log.info(f"SL hit — -1R")
                send_telegram(f"🛑 <b>Stop Loss Hit</b>\n{d} {SYMBOL} @ <b>{eff_sl}</b>\nLoss: <b>-1R (${s.get('risk_d',16)})</b>")
                try:
                    from trading_analyzer import record_sl; record_sl(SYMBOL, d)
                except: pass
            changed = True

    if changed:
        save_signals(sigs)

if __name__ == "__main__":
    log.info("=== Monitor scan ===")
    monitor()
