"""
Signal monitor — checks active signals every 15s, sends TP/SL/BE alerts,
generates re-entry signals after TP1, and triggers reflection/RL engine.
"""
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf

try:
    from reflection import record_trade_reflection
    _reflect_available = True
except ImportError:
    _reflect_available = False

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

# Full pair map including new pairs
PAIR_TICKERS = {
    "XAU/USD": "GC=F",     "XAG/USD": "SI=F",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X",
    "AUD/USD": "AUDUSD=X", "NZD/USD": "NZDUSD=X",
    "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X", "USD/CHF": "USDCHF=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "EUR/AUD": "EURAUD=X",
    "GBP/JPY": "GBPJPY=X", "GBP/CHF": "GBPCHF=X",
    "AUD/JPY": "AUDJPY=X", "CAD/JPY": "CADJPY=X",
    "GBP/AUD": "GBPAUD=X", "EUR/CAD": "EURCAD=X",
    "NZD/CAD": "NZDCAD=X", "NZD/JPY": "NZDJPY=X",
    "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "CHF/JPY": "CHFJPY=X", "AUD/NZD": "AUDNZD=X",
    "GBP/NZD": "GBPNZD=X", "EUR/NZD": "EURNZD=X",
    "GBP/CAD": "GBPCAD=X",
    "USD/NOK": "USDNOK=X", "USD/SEK": "USDSEK=X",
    "WTI/USD": "CL=F",
    "COPPER":  "HG=F",     "XPT/USD": "PL=F",
    "NAS100":  "NQ=F",     "US30":    "YM=F",
    "SPX500":  "ES=F",     "DAX40":   "^GDAXI",  "NKY225": "^N225",
}
PAIR_PIPS = {
    "XAU/USD": 0.10,   "XAG/USD": 0.010,
    "EUR/USD": 0.0001, "GBP/USD": 0.0001, "AUD/USD": 0.0001, "NZD/USD": 0.0001,
    "USD/JPY": 0.010,  "USD/CAD": 0.0001, "USD/CHF": 0.0001,
    "EUR/GBP": 0.0001, "EUR/JPY": 0.010,  "EUR/AUD": 0.0001,
    "GBP/JPY": 0.010,  "GBP/CHF": 0.0001,
    "AUD/JPY": 0.010,  "CAD/JPY": 0.010,
    "GBP/AUD": 0.0001, "EUR/CAD": 0.0001,
    "NZD/CAD": 0.0001, "NZD/JPY": 0.010,
    "AUD/CAD": 0.0001, "AUD/CHF": 0.0001,
    "CHF/JPY": 0.010,  "AUD/NZD": 0.0001,
    "GBP/NZD": 0.0001, "EUR/NZD": 0.0001,
    "GBP/CAD": 0.0001,
    "USD/NOK": 0.0001, "USD/SEK": 0.0001,
    "WTI/USD": 0.01,
    "COPPER":  0.001,  "XPT/USD": 0.10,
    "NAS100":  1.0,    "US30":    1.0,
    "SPX500":  1.0,    "DAX40":   1.0,    "NKY225": 10.0,
}
PAIR_SPREAD = {
    "XAU/USD": 0.50,    "XAG/USD": 0.030,
    "EUR/USD": 0.00015, "GBP/USD": 0.00020, "AUD/USD": 0.00020, "NZD/USD": 0.00025,
    "USD/JPY": 0.030,   "USD/CAD": 0.00020, "USD/CHF": 0.00020,
    "EUR/GBP": 0.00020, "EUR/JPY": 0.040,   "EUR/AUD": 0.00030,
    "GBP/JPY": 0.050,   "GBP/CHF": 0.00035,
    "AUD/JPY": 0.040,   "CAD/JPY": 0.040,
    "GBP/AUD": 0.00030, "EUR/CAD": 0.00025,
    "NZD/CAD": 0.00030, "NZD/JPY": 0.040,
    "AUD/CAD": 0.00025, "AUD/CHF": 0.00030,
    "CHF/JPY": 0.050,   "AUD/NZD": 0.00030,
    "GBP/NZD": 0.00035, "EUR/NZD": 0.00030,
    "GBP/CAD": 0.00025,
    "USD/NOK": 0.00050, "USD/SEK": 0.00060,
    "WTI/USD": 0.05,
    "COPPER":  0.0020,  "XPT/USD": 0.50,
    "NAS100":  2.0,     "US30":    3.0,
    "SPX500":  0.50,    "DAX40":   1.50,   "NKY225": 30.0,
}
PAIR_MIN_SL = {
    "XAU/USD": 5.0,    "XAG/USD": 0.15,
    "EUR/USD": 0.0010, "GBP/USD": 0.0012, "AUD/USD": 0.0008, "NZD/USD": 0.0008,
    "USD/JPY": 0.100,  "USD/CAD": 0.0010, "USD/CHF": 0.0010,
    "EUR/GBP": 0.0008, "EUR/JPY": 0.130,  "EUR/AUD": 0.0012,
    "GBP/JPY": 0.150,  "GBP/CHF": 0.0015,
    "AUD/JPY": 0.100,  "CAD/JPY": 0.100,
    "GBP/AUD": 0.0015, "EUR/CAD": 0.0012,
    "NZD/CAD": 0.0010, "NZD/JPY": 0.100,
    "AUD/CAD": 0.0010, "AUD/CHF": 0.0012,
    "CHF/JPY": 0.120,  "AUD/NZD": 0.0012,
    "GBP/NZD": 0.0020,  "EUR/NZD": 0.0018,
    "GBP/CAD": 0.0015,
    "USD/NOK": 0.0030,  "USD/SEK": 0.0030,
    "WTI/USD": 0.50,
    "COPPER":  0.010,   "XPT/USD": 3.0,
    "NAS100":  50.0,    "US30":    50.0,
    "SPX500":  20.0,    "DAX40":   50.0,   "NKY225": 200.0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────
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
        return float(yf.Ticker(ticker).fast_info.last_price)
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

# ── Re-entry scanner ──────────────────────────────────────────
def check_reentry(s, sigs):
    """
    After TP1 hit: look for a fresh OB/FVG in same direction on M15.
    If found, generate a continuation signal targeting TP2 and TP3.
    Returns (message, signal_dict) or (None, None).
    """
    try:
        from trading_analyzer import (PAIRS, fetch_yf, find_ob, find_fvg,
                                      calc_atr, get_sr_levels, snap_entry)
    except ImportError:
        log.warning("trading_analyzer not importable — re-entry skipped")
        return None, None

    symbol    = s["symbol"]
    direction = s["direction"]
    tp2       = s.get("tp2")
    tp3       = s.get("tp3")

    if not tp2:
        return None, None  # No targets left to aim for

    p = PAIRS.get(symbol)
    if not p: return None, None

    pip      = p["pip"]
    pip_val  = p["pip_val"]
    digits   = p["digits"]
    spread   = p["spread"]
    min_sl   = p["min_sl"]
    ticker   = p["ticker"]

    # Don't re-enter if already a re-entry pending in same direction
    if any(sig.get("is_reentry") and sig["symbol"] == symbol
           and sig["direction"] == direction
           and sig["status"] == "pending" for sig in sigs):
        return None, None

    try:
        df_m15 = fetch_yf(ticker, "15m", "5d")
        if df_m15 is None or len(df_m15) < 30:
            return None, None

        c15 = df_m15.iloc[:-1]
        atr = calc_atr(c15)

        ob_lo, ob_hi, zone_type = find_ob(c15, direction, lookback=30)
        if not ob_lo:
            ob_lo, ob_hi, zone_type = find_fvg(c15, direction, lookback=20)
        if not ob_lo:
            return None, None

        current_price = round(df_m15.close.iloc[-1], digits)
        ob_edge = ob_hi if direction == "BUY" else ob_lo
        if abs(current_price - ob_edge) > 5.0 * atr:
            return None, None  # Too far from zone

        if direction == "BUY":
            new_entry = round(ob_hi + spread, digits)
            new_sl    = round(ob_lo - max(2 * pip, round(0.15 * atr, digits)), digits)
        else:
            new_entry = round(ob_lo - spread, digits)
            new_sl    = round(ob_hi + max(2 * pip, round(0.15 * atr, digits)), digits)

        sl_dist = abs(new_entry - new_sl)
        if sl_dist < min_sl:
            return None, None

        rr = round(abs(tp2 - new_entry) / sl_dist, 1)
        if rr < 1.3:
            return None, None

        risk_d   = s.get("risk_d", 16)
        slp      = round(sl_dist / pip)
        std_lots = round(risk_d / (slp * pip_val), 2) if slp > 0 else 0
        mini     = round(std_lots * 10, 1)
        exp_loss = round(std_lots * slp * pip_val)
        arrow    = "🟢 BUY LIMIT 📈" if direction == "BUY" else "🔴 SELL LIMIT 📉"

        msg = (
            f"👤 <b>Eng. Yasser Haggag</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔄 <b>Re-Entry Signal</b> — {symbol}\n"
            f"TP1 was hit ✅ — continuation {direction} setup found\n\n"
            f"{arrow}\n"
            f"🎯 Entry:  <b>{new_entry}</b>  ({zone_type})\n"
            f"🛑 SL:     <b>{new_sl}</b>  ({slp} pips)\n"
            f"🥈 TP2:   <b>{tp2}</b>\n"
            f"🏆 TP3:   <b>{tp3 or '—'}</b>\n"
            f"⚖️ R:R:   1:{rr}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📦 {std_lots} lots  |  💼 Risk: ${risk_d}  |  ⚠️ Max loss: -${exp_loss}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💡 Fresh {zone_type} formed after TP1 — riding the continuation to TP2/TP3."
        )

        new_sig = {
            "status": "pending", "symbol": symbol, "direction": direction,
            "entry": new_entry, "sl": new_sl, "eff_sl": new_sl,
            "tp1": tp2, "tp2": tp3, "tp3": None,
            "rr": rr, "slp": slp, "std_lots": std_lots,
            "mini_lots": mini, "exp_loss": exp_loss, "risk_d": risk_d,
            "zone_type": zone_type, "score": s.get("score", 0),
            "adx": s.get("adx", 0), "rsi": s.get("rsi", 50),
            "w1_bias": s.get("w1_bias"), "d1_bias": s.get("d1_bias"),
            "bb_squeeze": s.get("bb_squeeze", False),
            "st_h4": s.get("st_h4", 0), "st_h1": s.get("st_h1", 0),
            "tp1_hit": False, "tp2_hit": False,
            "signal_ts": datetime.now(timezone.utc).isoformat(),
            "is_reentry": True,
            "parent_signal_ts": s.get("signal_ts"),
            "session": s.get("session", ""),
        }
        return msg, new_sig

    except Exception as e:
        log.warning(f"Re-entry check failed for {symbol}: {e}")
        return None, None

# ── Main monitor ──────────────────────────────────────────────
def monitor():
    sigs   = load_signals()
    active = [s for s in sigs if s["status"] in ("active", "pending")]
    if not active:
        log.info("No active signals"); return

    symbols_needed = list({s["symbol"] for s in active})
    prices = {}
    for sym in symbols_needed:
        p = get_price(sym)
        if p: prices[sym] = p
        time.sleep(0.4)

    log.info(f"Prices: { {k: round(v, 5) for k, v in prices.items()} }")
    changed      = False
    new_signals  = []

    for s in sigs:
        if s["status"] not in ("active", "pending"): continue
        sym   = s["symbol"]
        price = prices.get(sym)
        if price is None:
            log.warning(f"  No price for {sym}"); continue

        d      = s["direction"]
        entry  = s["entry"]
        pip    = PAIR_PIPS.get(sym, 0.0001)
        sl_d   = abs(entry - s["sl"])

        # ── Pending → expired (48h) ───────────────────────────
        if s["status"] == "pending":
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(s["signal_ts"].replace("Z", "+00:00"))).total_seconds()
            if age > 172800:
                s["status"] = "expired"
                log.info(f"EXPIRED {sym} {d}")
                send_telegram(
                    f"⌛ <b>Signal Expired</b>  {sym}\n"
                    f"{d} limit @ <b>{entry}</b> — never triggered in 48h.\n"
                    f"SL was: {s['sl']}  |  TP1 was: {s['tp1']}"
                )
                changed = True; continue

        # ── Pending → active ──────────────────────────────────
        if s["status"] == "pending":
            triggered = (price <= entry if d == "BUY" else price >= entry)
            if triggered:
                s["status"]   = "active"
                s["entry_ts"] = datetime.now(timezone.utc).isoformat()
                log.info(f"ACTIVATED {sym} {d} @ {entry}")
                send_telegram(
                    f"⚡ <b>Entry Triggered!</b>  {sym}\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"{'🟢' if d=='BUY' else '🔴'} <b>{d}</b> @ <b>{entry}</b>\n"
                    f"🛑 SL: <b>{s['sl']}</b>  |  🥇 TP1: <b>{s['tp1']}</b>"
                )
                changed = True
            continue

        eff_sl  = s.get("eff_sl", s["sl"])
        tp1     = s["tp1"]; tp2 = s.get("tp2"); tp3 = s.get("tp3")
        tp1_hit = s.get("tp1_hit", False); tp2_hit = s.get("tp2_hit", False)

        def hit_tp(lvl): return (price >= lvl) if d == "BUY" else (price <= lvl)
        def hit_sl(lvl): return (price <= lvl) if d == "BUY" else (price >= lvl)

        # ── TP1 ───────────────────────────────────────────────
        if not tp1_hit and hit_tp(tp1):
            s["tp1_hit"] = True; s["eff_sl"] = entry
            pnl_tp1 = round(0.5 * abs(tp1 - entry) / max(sl_d, 1e-9), 2)
            log.info(f"{sym}: TP1 hit @ {tp1}")
            send_telegram(
                f"🥇 <b>TP1 Hit!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP1: <b>{tp1}</b> ✅  (50% closed)\n\n"
                f"⚡ <b>ACTION NOW:</b>\n"
                f"🔁 Move SL: <b>{s['sl']}</b> → <b>{entry}</b> (Breakeven)\n"
                f"   50% position secured 🔒  |  Remaining 50% runs free."
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP1", pnl_tp1)
                except Exception as e: log.warning(f"Reflection failed: {e}")

            # Re-entry scan
            re_msg, re_sig = check_reentry(s, sigs + new_signals)
            if re_sig:
                send_telegram(re_msg)
                new_signals.append(re_sig)
                log.info(f"Re-entry signal queued: {sym} {d}")

            changed = True; continue

        # ── TP2 ───────────────────────────────────────────────
        if tp1_hit and tp2 and not tp2_hit and hit_tp(tp2):
            s["tp2_hit"] = True; s["eff_sl"] = tp1
            r1 = abs(tp1 - entry) / max(sl_d, 1e-9)
            r2 = abs(tp2 - entry) / max(sl_d, 1e-9)
            pnl_tp2 = round(0.5 * r1 + 0.3 * r2, 2)
            log.info(f"{sym}: TP2 hit @ {tp2}")
            send_telegram(
                f"🥈 <b>TP2 Hit!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP2: <b>{tp2}</b> ✅  (30% closed)\n\n"
                f"⚡ <b>ACTION NOW:</b>\n"
                f"🔁 Trail SL: <b>{entry}</b> → <b>{tp1}</b>\n"
                f"   80% position closed profitably 🔒  |  Last 20% runs to TP3."
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP2", pnl_tp2)
                except Exception as e: log.warning(f"Reflection failed: {e}")
            changed = True; continue

        # ── TP3 ───────────────────────────────────────────────
        if tp2_hit and tp3 and hit_tp(tp3):
            r1  = abs(tp1 - entry) / max(sl_d, 1e-9)
            r2  = abs(tp2 - entry) / max(sl_d, 1e-9) if tp2 else r1
            r3  = abs(tp3 - entry) / max(sl_d, 1e-9)
            pnl = round(0.5 * r1 + 0.3 * r2 + 0.2 * r3, 2)
            s["status"] = "closed"; s["outcome"] = "TP3"; s["pnl_r"] = pnl
            log.info(f"{sym}: TP3 full close +{pnl}R")
            send_telegram(
                f"🏆 <b>TP3 Hit — Full Close!</b>  {sym}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{'🟢' if d=='BUY' else '🔴'} {d} → TP3: <b>{tp3}</b> ✅\n"
                f"💰 Total P&L: <b>+{pnl}R</b> 🎉"
            )
            if _reflect_available:
                try: record_trade_reflection(s, "TP3", pnl)
                except Exception as e: log.warning(f"Reflection failed: {e}")
            changed = True; continue

        # ── SL / BE / Trail ───────────────────────────────────
        if hit_sl(eff_sl):
            if eff_sl == entry:
                pnl = round(0.5 * abs(tp1 - entry) / max(sl_d, 1e-9), 2)
                s["status"] = "closed"; s["outcome"] = "BE"; s["pnl_r"] = pnl
                log.info(f"{sym}: BE close +{pnl}R")
                send_telegram(
                    f"⚡ <b>Breakeven Close</b>  {sym}\n"
                    f"SL @ entry <b>{entry}</b> hit — 50% locked at TP1\n"
                    f"P&L: <b>+{pnl}R</b> (no loss on capital)"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "BE", pnl)
                    except Exception as e: log.warning(f"Reflection failed: {e}")

            elif eff_sl == tp1:
                r1  = abs(tp1 - entry) / max(sl_d, 1e-9)
                r2  = abs((tp2 or tp1) - entry) / max(sl_d, 1e-9)
                pnl = round(0.5 * r1 + 0.3 * r2, 2)
                s["status"] = "closed"; s["outcome"] = "TRAIL"; s["pnl_r"] = pnl
                log.info(f"{sym}: Trail close +{pnl}R")
                send_telegram(
                    f"📉 <b>Trailing Stop Hit</b>  {sym}\n"
                    f"SL @ TP1 <b>{tp1}</b> hit after TP2 ✅\n"
                    f"P&L: <b>+{pnl}R</b>"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "TRAIL", pnl)
                    except Exception as e: log.warning(f"Reflection failed: {e}")

            else:
                s["status"] = "closed"; s["outcome"] = "SL"; s["pnl_r"] = -1.0
                log.info(f"{sym}: SL hit -1R")
                send_telegram(
                    f"🛑 <b>Stop Loss Hit</b>  {sym}\n"
                    f"{'🟢' if d=='BUY' else '🔴'} {d} — SL @ <b>{eff_sl}</b>\n"
                    f"Loss: <b>-1R  (-${s.get('risk_d', 16)})</b>"
                )
                if _reflect_available:
                    try: record_trade_reflection(s, "SL", -1.0)
                    except Exception as e: log.warning(f"Reflection failed: {e}")
            changed = True

    if new_signals:
        sigs.extend(new_signals)
        changed = True

    if changed:
        save_signals(sigs)

if __name__ == "__main__":
    log.info("=== Monitor scan ===")
    monitor()
    log.info("=== Done ===")
