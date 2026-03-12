"""
Run ETH Arbitrage Engine with Web UI
"""

import sys
sys.stdout.reconfigure(line_buffering=True)

import asyncio
import threading
import argparse
import os
from web.app import (
    app, socketio, ui_state, emit_update, log_event,
    is_engine_stopped, clear_stop_flag, set_restart_callback
)
import arb_engine
from arb_engine import ArbEngine, set_stop_check

# Global reference for restart
_engine_args = None
_engine_loop = None
_restart_requested = threading.Event()

def handle_ui_event(event_type: str, data: dict):
    """Handle UI events from arb engine."""
    if event_type == "spread":
        ui_state["hl_price"] = data.get("hl_price", 0)
        ui_state["def_price"] = data.get("def_price", 0)
        ui_state["current_spread_bps"] = data.get("spread_bps", 0)
        status = data.get("status", "IDLE")
        ui_state["status"] = status
        # Set mode based on status
        if status == "WAITING_ENTRY":
            ui_state["mode"] = "ENTRY"
        elif status == "IN_POSITION":
            ui_state["mode"] = "EXIT"
        emit_update()
    
    elif event_type == "position":
        from datetime import datetime
        in_position = data.get("in_position", False)
        ui_state["in_position"] = in_position
        ui_state["entry_spread_bps"] = data.get("entry_spread_bps", 0)
        ui_state["unrealized_pnl"] = data.get("unrealized_pnl", 0)
        ui_state["status"] = data.get("status", "IDLE")
        if not in_position:
            ui_state["entry_time"] = None
            ui_state["mode"] = "ENTRY"
        else:
            # Set entry_time when entering position
            if not ui_state.get("entry_time"):
                ui_state["entry_time"] = datetime.now().strftime("%H:%M:%S")
            ui_state["mode"] = "EXIT"
        emit_update()
    
    elif event_type == "balances":
        ui_state["balances"]["def_usdc"] = data.get("def_usdc", 0)
        ui_state["balances"]["hl_usdc"] = data.get("hl_usdc", 0)
        ui_state["balances"]["total"] = data.get("def_usdc", 0) + data.get("hl_usdc", 0)
        emit_update()
    
    elif event_type == "cycle_complete":
        ui_state["cycles_completed"] += 1
        ui_state["total_realized_pnl"] += data.get("realized_pnl", 0)
        # Store detailed cycle summary
        ui_state["last_cycle"] = {
            "entry_spread": data.get("entry_spread", 0),
            "exit_spread": data.get("exit_spread", 0),
            "realized_pnl": data.get("realized_pnl", 0),
            "def_pnl": data.get("def_pnl", 0),
            "hl_pnl": data.get("hl_pnl", 0),
            "fees": data.get("fees", 0),
            "def_latency_ms": data.get("def_latency_ms", 0),
            "hl_latency_ms": data.get("hl_latency_ms", 0)
        }
        emit_update()
    
    elif event_type == "thresholds":
        ui_state["entry_threshold_bps"] = data.get("entry_bps", -3)
        ui_state["exit_threshold_bps"] = data.get("exit_bps", 15)
        emit_update()
    
    elif event_type == "warmup":
        remaining = data.get("remaining_sec", 0)
        ui_state["warmup_remaining_sec"] = remaining
        if remaining > 0:
            ui_state["mode"] = "WARMUP"
            ui_state["status"] = f"WARMUP ({remaining:.0f}s)"
        else:
            ui_state["mode"] = "STARTING"
            ui_state["status"] = "Checking balances..."
        emit_update()
    
    elif event_type == "token_status":
        ui_state["token_expires_in_sec"] = data.get("expires_in_sec", 0)
        if data.get("refreshing", False):
            ui_state["mode"] = "REFRESHING_TOKEN"
            ui_state["status"] = "REFRESHING TOKEN"
        emit_update()
    
    elif event_type == "event":
        log_event(data.get("type", "INFO"), data.get("message", ""))


def run_web_server():
    """Run Flask web server in a thread."""
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)


async def run_engine(args):
    """Run the arbitrage engine."""
    engine = ArbEngine(size_usd=args.size)
    engine.ENTRY_THRESHOLD_BPS = args.entry
    engine.EXIT_THRESHOLD_BPS = args.exit
    engine.USE_TURBO = not args.prime
    engine.SLIPPAGE_TOLERANCE = f"{args.slip / 10000:.6f}"
    
    try:
        await engine.run(num_cycles=args.cycles)
    except Exception as e:
        print(f"[ENGINE] Error: {e}")
        log_event("ERROR", f"Engine crashed: {e}")
    finally:
        # Update UI to show stopped
        if is_engine_stopped():
            ui_state["status"] = "STOPPED"
            ui_state["mode"] = "STOPPED"
            emit_update()
            log_event("WARNING", "Engine stopped")
        else:
            ui_state["status"] = "IDLE"
            emit_update()


def request_restart():
    """Called from UI to request a restart."""
    global _restart_requested
    print("[RESTART] Restart requested from UI")
    _restart_requested.set()


async def engine_loop(args):
    """Main engine loop with restart support."""
    global _restart_requested
    
    while True:
        # Clear any previous stop flag
        clear_stop_flag()
        _restart_requested.clear()
        
        # Run the engine
        print(f"\n[ENGINE] Starting arbitrage engine...")
        print(f"[ENGINE] Size: ${args.size}, Entry: {args.entry}bp, Exit: {args.exit}bp")
        print(f"[ENGINE] Mode: {'PRIME' if args.prime else 'TURBO'}, Slippage tolerance: {args.slip}bp\n")
        
        await run_engine(args)
        
        # Check if we should restart
        if _restart_requested.is_set():
            print("[ENGINE] Restarting in 2 seconds...")
            log_event("INFO", "Engine restarting...")
            await asyncio.sleep(2)
            continue
        else:
            # Engine stopped without restart request - wait for restart
            print("[ENGINE] Engine stopped. Waiting for restart command...")
            
            # Wait for restart signal
            while not _restart_requested.is_set():
                await asyncio.sleep(0.5)
            
            print("[ENGINE] Restart signal received")
            continue


def main():
    global _engine_args
    
    parser = argparse.ArgumentParser(description="ETH Arbitrage Engine with Web UI")
    parser.add_argument("--size", type=float, default=100, help="Order size in USD")
    parser.add_argument("--cycles", type=int, default=999, help="Number of cycles to run")
    parser.add_argument("--entry", type=float, default=5.0, help="Entry threshold (bps)")
    parser.add_argument("--exit", type=float, default=15.0, help="Exit threshold (bps)")
    parser.add_argument("--prime", action="store_true", help="Use PRIME mode")
    parser.add_argument("--slip", type=float, default=5.0, help="Slippage tolerance in bps")
    
    args = parser.parse_args()
    _engine_args = args
    
    # Set UI callback
    arb_engine.set_ui_callback(handle_ui_event)
    
    # Set stop check callback
    set_stop_check(is_engine_stopped)
    
    # Set restart callback
    set_restart_callback(request_restart)
    
    # Start web server in background thread
    port = int(os.environ.get('PORT', 5000))
    print(f"\n[WEB UI] Starting at http://localhost:{port}")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Run engine loop (handles restarts)
    asyncio.run(engine_loop(args))


if __name__ == "__main__":
    main()
