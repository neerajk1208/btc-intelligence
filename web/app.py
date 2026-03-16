"""
Web UI for ETH Arbitrage Engine
Real-time monitoring dashboard
"""

from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
import threading
import queue
import json
import os
import sys
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'arb-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Engine control
engine_stop_flag = threading.Event()
engine_restart_callback = None  # Set by run_with_ui.py

def set_restart_callback(callback):
    """Set the callback function to restart the engine."""
    global engine_restart_callback
    engine_restart_callback = callback

def is_engine_stopped():
    """Check if the engine should stop."""
    return engine_stop_flag.is_set()

def clear_stop_flag():
    """Clear the stop flag for restart."""
    engine_stop_flag.clear()

# Global state for UI
ui_state = {
    "status": "STOPPED",
    "mode": "STOPPED",  # ENTRY, EXIT, WARMUP, REFRESHING_TOKEN, STOPPED, PAUSED
    "current_spread_bps": 0,
    "hl_price": 0,
    "def_price": 0,
    "in_position": False,
    "entry_spread_bps": 0,
    "entry_time": None,
    "unrealized_pnl": 0,
    "cycles_completed": 0,
    "total_realized_pnl": 0,
    "last_error": None,
    "events": [],  # Recent events log
    "balances": {
        "def_usdc": 0,
        "hl_usdc": 0,
        "total": 0
    },
    # Thresholds for UI display
    "entry_threshold_bps": -3,
    "exit_threshold_bps": 15,
    # Last cycle summary
    "last_cycle": {
        "entry_spread": 0,
        "exit_spread": 0,
        "realized_pnl": 0,
        "def_pnl": 0,
        "hl_pnl": 0,
        "fees": 0,
        "def_latency_ms": 0,
        "hl_latency_ms": 0,
        "duration_sec": 0
    },
    # Token status
    "token_expires_in_sec": 0,
    "token_expires_at": 0,
    "token_last_checked": None,
    "warmup_remaining_sec": 0,
    
    # Latency tracking
    "latency": {
        "def_quote_ms": 0,
        "hl_ws_age_ms": 0,
        "price_gap_ms": 0,
        "def_exec_ms": 0,
        "hl_exec_ms": 0
    },
    
    # Service health
    "services": {
        "def_api": "unknown",
        "def_auth": "unknown",
        "hl_rest": "unknown",
        "hl_websocket": "unknown"
    },
    
    # Cycle BPS breakdown
    "cycle_bps": {
        "expected_entry": 0,
        "actual_entry": 0,
        "entry_slippage": 0,
        "expected_exit": 0,
        "actual_exit": 0,
        "exit_slippage": 0,
        "total_slippage": 0
    },
    
    # Position confirmation
    "position_confirmed": False,
    "position_mismatch": False,
    "position_mismatch_detail": None,
    
    # Pause state
    "is_paused": False,
    "pause_reason": None
}

def emit_update():
    """Send current state to all connected clients."""
    socketio.emit('state_update', ui_state)

def log_event(event_type: str, message: str, details: dict = None):
    """Log an event and emit to UI."""
    event = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": event_type,
        "message": message,
        "details": details or {}
    }
    ui_state["events"].insert(0, event)
    ui_state["events"] = ui_state["events"][:50]  # Keep last 50 events
    emit_update()

def update_spread(hl_price: float, def_price: float, spread_bps: float, status: str = None):
    """Update current spread."""
    ui_state["hl_price"] = hl_price
    ui_state["def_price"] = def_price
    ui_state["current_spread_bps"] = spread_bps
    if status:
        ui_state["status"] = status
        # Set mode based on status
        if status == "WAITING_ENTRY":
            ui_state["mode"] = "ENTRY"
        elif status == "IN_POSITION":
            ui_state["mode"] = "EXIT"
    emit_update()

def update_status(status: str):
    """Update engine status."""
    ui_state["status"] = status
    emit_update()

def update_position(in_position: bool, entry_spread: float = 0, unrealized: float = 0):
    """Update position state."""
    ui_state["in_position"] = in_position
    ui_state["entry_spread_bps"] = entry_spread
    ui_state["unrealized_pnl"] = unrealized
    if in_position and not ui_state["entry_time"]:
        ui_state["entry_time"] = datetime.now().strftime("%H:%M:%S")
    elif not in_position:
        ui_state["entry_time"] = None
    emit_update()

def update_balances(def_usdc: float, hl_usdc: float):
    """Update balances."""
    ui_state["balances"]["def_usdc"] = def_usdc
    ui_state["balances"]["hl_usdc"] = hl_usdc
    ui_state["balances"]["total"] = def_usdc + hl_usdc
    emit_update()

def record_cycle_complete(data: dict):
    """Record completed cycle with full details."""
    ui_state["cycles_completed"] += 1
    realized_pnl = data.get("realized_pnl", 0)
    ui_state["total_realized_pnl"] += realized_pnl
    
    # Store detailed cycle summary
    ui_state["last_cycle"] = {
        "entry_spread": data.get("entry_spread", 0),
        "exit_spread": data.get("exit_spread", 0),
        "realized_pnl": realized_pnl,
        "def_pnl": data.get("def_pnl", 0),
        "hl_pnl": data.get("hl_pnl", 0),
        "fees": data.get("fees", 0),
        "def_latency_ms": data.get("def_latency_ms", 0),
        "hl_latency_ms": data.get("hl_latency_ms", 0),
        "duration_sec": data.get("duration_sec", 0)
    }
    
    log_event("CYCLE_COMPLETE", f"Cycle {ui_state['cycles_completed']} complete: ${realized_pnl:+.4f}", {
        "realized_pnl": realized_pnl,
        "fees": data.get("fees", 0),
        "def_pnl": data.get("def_pnl", 0),
        "hl_pnl": data.get("hl_pnl", 0)
    })

def record_error(error_message: str):
    """Record an error."""
    ui_state["last_error"] = error_message
    log_event("ERROR", error_message)

def update_warmup(remaining_sec: float):
    """Update warmup status."""
    ui_state["warmup_remaining_sec"] = remaining_sec
    if remaining_sec > 0:
        ui_state["mode"] = "WARMUP"
        ui_state["status"] = f"WARMUP ({remaining_sec:.0f}s)"
    emit_update()

def update_token_status(expires_in_sec: float, refreshing: bool = False):
    """Update token expiry status."""
    ui_state["token_expires_in_sec"] = expires_in_sec
    if refreshing:
        ui_state["mode"] = "REFRESHING_TOKEN"
        ui_state["status"] = "REFRESHING TOKEN"
    emit_update()

def update_thresholds(entry_bps: float, exit_bps: float):
    """Update threshold values for UI display."""
    ui_state["entry_threshold_bps"] = entry_bps
    ui_state["exit_threshold_bps"] = exit_bps
    emit_update()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/state')
def get_state():
    return jsonify(ui_state)

@app.route('/api/stats')
def get_stats():
    """Get today's running stats from Supabase."""
    import requests
    
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY", "")
    
    if not supabase_url or not supabase_key:
        return jsonify({
            "cycles_today": 0,
            "net_pnl_today": 0,
            "gross_pnl_today": 0,
            "avg_slippage_bps": 0,
            "volume_today": 0,
            "error": "Supabase not configured"
        })
    
    try:
        # Today's start in unix ms
        today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json"
        }
        
        # Query trades from today
        resp = requests.get(
            f"{supabase_url}/rest/v1/trades",
            headers=headers,
            params={
                "select": "net_pnl,gross_pnl,slippage_bps,order_size_usd",
                "ts": f"gte.{today_start}",
                "side": "eq.EXIT",
                "success": "eq.1"
            },
            timeout=5
        )
        
        if resp.status_code != 200:
            return jsonify({
                "cycles_today": 0,
                "net_pnl_today": 0,
                "error": f"Supabase error: {resp.status_code}"
            })
        
        trades = resp.json()
        
        cycles = len(trades)
        net_pnl = sum(t.get("net_pnl", 0) or 0 for t in trades)
        gross_pnl = sum(t.get("gross_pnl", 0) or 0 for t in trades)
        volume = sum((t.get("order_size_usd", 0) or 0) * 2 for t in trades)
        avg_slip = sum(t.get("slippage_bps", 0) or 0 for t in trades) / cycles if cycles > 0 else 0
        
        return jsonify({
            "cycles_today": cycles,
            "net_pnl_today": round(net_pnl, 4),
            "gross_pnl_today": round(gross_pnl, 4),
            "avg_slippage_bps": round(avg_slip, 2),
            "volume_today": round(volume, 2)
        })
    except Exception as e:
        print(f"[STATS] Error: {e}")
        return jsonify({
            "cycles_today": 0,
            "net_pnl_today": 0,
            "gross_pnl_today": 0,
            "avg_slippage_bps": 0,
            "volume_today": 0,
            "error": str(e)
        })

@socketio.on('connect')
def handle_connect():
    emit_update()

@socketio.on('stop_engine')
def handle_stop():
    """Handle stop request from UI."""
    print("[UI] Stop requested")
    engine_stop_flag.set()
    ui_state["status"] = "STOPPING..."
    ui_state["mode"] = "STOPPED"
    log_event("WARNING", "Engine stop requested from UI")
    emit_update()

@socketio.on('restart_engine')
def handle_restart():
    """Handle restart request from UI."""
    global engine_restart_callback
    print("[UI] Restart requested")
    
    # Clear stop flag first
    engine_stop_flag.clear()
    
    ui_state["status"] = "RESTARTING..."
    ui_state["mode"] = "WARMUP"
    log_event("INFO", "Engine restart requested from UI")
    emit_update()
    
    # Call restart callback if set
    if engine_restart_callback:
        try:
            engine_restart_callback()
        except Exception as e:
            log_event("ERROR", f"Restart failed: {e}")
    else:
        log_event("WARNING", "Restart callback not set - manual restart required")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
