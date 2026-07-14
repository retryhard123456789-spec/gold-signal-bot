"""
Signal monitor v7 — checks active signals across ALL pairs against live price every 5 min.
Uses yfinance for price (free, no API key needed).
"""
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf

BASE     = Path(__file__).parent
LOG_DIR  = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "monitor.log"
SIG_FILE = Path(os.environ.get("GITHUB_WORKSPACE", str(BASE))) / "signals.json"

_use_env = all(os.environ.get(k) for k in ["TG_TOKEN", "TG_CHAT"])
if _use_env:
    TG_TOKEN = os.environ["TG_TOKEN"]
    TG_CHAT  = os.environ["TG_CHAT"]
else:
    _cfg     = json.loads((BASE / "config.json").read_text())
    TG_TOKEN = _cfg["telegram_token"]
    TG_CHAT  = _cfg["telegram_chat_id"]

# Pair → yfinance ticker map
PAIR_TICKERS = {
    "XAU/USD": "GC=F",     "XAG/USD": "SI=F",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X", "NZD/USD": "NZDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "EUR/AUD": "EURAUD=X",
    "GBP/JPY": "GBPJPY=X", "GBP/CHF": "GBPCHF=X",
    "AUD/JPY": "AUDJPY=X", "CAD/JPY": "CADJPY=X",
}
PAIR_PIPS = {
    "XAU/USD": 0.10,  "XAG/USD": 0.010,
    "EUR/USD": 0.0001,"GBP/USD": 0.0001,"AUD/USD": 0.0001,"NZD/USD": 0.0001,
    "USD/JPY": 0.010, "USD/CAD": 0.0001,"USD/CHF": 0.0001,
    "EUR/GBP": 0.0001,"EUR/JPY": 0.010, "EUR/AUD": 0.0001,
    "GBP/JPY": 0.010, "GBP/CHF": 0.0001,
    "AUD/JPY": 0.010, "CAD/JPY": 0.010,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

def send_telegram(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        if not r.ok: log.warning(f"Telegram: {r.text[:120]}")
    except Exception as e: log.error(f"Telegram failed: {e}")

def get_price(symbol):
    ticker = PAIR_TICKERS.get(symbol)
    if not ticker: return None
    try:
        t = yf.Ticker(ticker)
        fast = t.fast_info
        return float(fast.last_price)
    except Exception:
        try:
            df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
            if not df.empty: return float(df["Close"].iloc[-1])
        except: pass
    return None

def load_signals():
    try: return json.loads(SIG_FILE.read_text())
    except: return []

def save_signals(sigs):
    SIG_FILE.write_text(json.dumps(sigs, indent=2, default=str))

def monitor():
    sigs    = load_signals()
    active  = [s for s in sigs if s["status"] in ("active","pending")]
    if not active:
        log.info("No active signals"); return

    # Fetch prices for all unique symbols we need
    symbols_needed = list({s["symbol"] for s in active})
    prices = {}
    for sym in symbols_needed:
        p = get_price(sym)
        if p: prices[sym] = p
        time.sleep(0.5)

    log.info(f"Prices: { {k: round(v,5) for k,v in prices.items()} }")
    changed = False

    for s in sigs:
        if s["status"] not in ("active","pending"): continue
        sym   = s["symbol"]
        price = prices.get(sym)
        if price is None:
            log.warning(f"  No price for {sym}"); continue

        d      = s["direction"]
        entry  = s["entry"]
        pip    = PAIR_PIPS.get(sym, 0.0001)
        sl_d   = abs(entry - s["sl"])

        # Pending → expired after 48h
        if s["status"] == "pending":
            signal_age = (datetime.now(timezone.utc) -
                          datetime.fromisoformat(s["signal_ts"].replace("Z", "+00:00"))).total_seconds()
            if signal_age > 172800:  # 48 hours
                s["status"] = "expired"
                log.info(f"EXPIRED {sym} {d} — pending 48h without trigger")
                send_telegram(
                    f"⌛ <b>Signal Expired</b>  {sym}\n"
                    f"{d} limit @ <b>{entry}</b> — never triggered in 48h.\n"
                    f"SL was: {s['sl']}  |  TP1 was: {s['tp1']}"
                )
                changed = True
                continue

        # Pending → active
        if s["status"] == "pending":
            triggered = (price <= entry if d == "BUY" else price >= entry)
            if triggered:
                s["status"] = "active"
                s["entry_ts"] = datetime.now(timezone.utc).isoformat()
                log.info(f"ACTIVATED {sym} {d} @ {entry}")
                send_telegram(
                    f"⚡ <b>Entry Triggered!</b>\n"
                    f"{d} <b>{sym}</b> @ <b>{entry}</b>\n"
                    f"SL: {s['sl']}  |  TP1: {s['tp1']}"
                )
                changed = True
            continue

        eff_sl  = s.get("eff_sl", s["sl"])
        tp1     = s["tp1"]; tp2 = s.get("tp2"); tp3 = s.get("tp3")
        tp1_hit = s.get("tp1_hit", False); tp2_hit = s.get("tp2_hit", False)

        def hit_tp(lvl): return (price >= lvl) if d == "BUY" else (price <= lvl)
        def hit_sl(lvl): return (price <= lvl) if d == "BUY" else (price >= lvl)

        # TP1
        if not tp1_hit and hit_tp(tp1):
            s["tp1_hit"] = True; s["eff_sl"] = entry
            log.info(f"{sym}: TP1 hit @ {tp1}")
            send_telegram(
                f"🥇 <b>TP1 Hit!</b>  {sym}\n"
                f"{d} → TP1: <b>{tp1}</b> ✅  (50% closed)\n"
                f"🔁 Move SL to entry <b>{entry}</b> — breakeven!"
            )
            changed = True; continue

        # TP2
        if tp1_hit and tp2 and not tp2_hit and hit_tp(tp2):
            s["tp2_hit"] = True; s["eff_sl"] = tp1
            log.info(f"{sym}: TP2 hit @ {tp2}")
            send_telegram(
                f"🥈 <b>TP2 Hit!</b>  {sym}\n"
                f"{d} → TP2: <b>{tp2}</b> ✅  (30% closed)\n"
                f"🔁 Trail SL to TP1 <b>{tp1}</b>"
            )
            changed = True; continue

        # TP3
        if tp2_hit and tp3 and hit_tp(tp3):
            r1 = abs(tp1-entry)/max(sl_d,1e-9)
            r2 = abs(tp2-entry)/max(sl_d,1e-9) if tp2 else r1
            r3 = abs(tp3-entry)/max(sl_d,1e-9) if tp3 else r2
            pnl = round(0.5*r1+0.3*r2+0.2*r3, 2)
            s["status"] = "closed"; s["outcome"] = "TP3"; s["pnl_r"] = pnl
            log.info(f"{sym}: TP3 full close +{pnl}R")
            send_telegram(
                f"🏆 <b>TP3 Hit — Full Close!</b>  {sym}\n"
                f"{d} → TP3: <b>{tp3}</b> ✅\n"
                f"💰 Total: <b>+{pnl}R</b> 🎉"
            )
            changed = True; continue

        # SL / BE / Trail stop
        if hit_sl(eff_sl):
            if eff_sl == entry:
                pnl = round(0.5*abs(tp1-entry)/max(sl_d,1e-9), 2)
                s["status"] = "closed"; s["outcome"] = "BE"
                log.info(f"{sym}: BE close +{pnl}R")
                send_telegram(
                    f"⚡ <b>Breakeven Close</b>  {sym}\n"
                    f"50% locked at TP1 → <b>+{pnl}R</b>"
                )
            elif eff_sl == tp1:
                r1 = abs(tp1-entry)/max(sl_d,1e-9)
                r2 = abs((tp2 or tp1)-entry)/max(sl_d,1e-9)
                pnl = round(0.5*r1+0.3*r2, 2)
                s["status"] = "closed"; s["outcome"] = "TRAIL"
                log.info(f"{sym}: Trail close +{pnl}R")
                send_telegram(
                    f"📉 <b>Trailing Stop Hit</b>  {sym}\n"
                    f"Closed at TP1 trail → <b>+{pnl}R</b>"
                )
            else:
                s["status"] = "closed"; s["outcome"] = "SL"; s["pnl_r"] = -1.0
                log.info(f"{sym}: SL hit -1R")
                send_telegram(
                    f"🛑 <b>Stop Loss Hit</b>  {sym}\n"
                    f"{d} @ <b>{eff_sl}</b>\n"
                    f"Loss: <b>-1R (${s.get('risk_d', 16)})</b>"
                )
            changed = True

    if changed:
        save_signals(sigs)

if __name__ == "__main__":
    log.info("=== Monitor scan ===")
    monitor()
    log.info("=== Done ===")
