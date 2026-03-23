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
    is_engine_stopped, clear_stop_flag, set_restart_callback,
    set_close_def_callback, set_close_hl_callback
)
import arb_engine
from arb_engine import ArbEngine, set_stop_check

# Global reference for restart
_engine_args = None
_engine_loop = None
_restart_requested = threading.Event()
_current_engine = None  # Reference to running engine for manual close
_engine_event_loop = None  # Reference to the asyncio event loop

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
        ui_state["entry_threshold_bps"] = data.get("entry_bps", 0)
        ui_state["exit_threshold_bps"] = data.get("exit_bps", 4)
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
        ui_state["token_expires_at"] = data.get("expires_at", 0)
        if data.get("refreshing", False):
            ui_state["mode"] = "REFRESHING_TOKEN"
            ui_state["status"] = "REFRESHING TOKEN"
        emit_update()
    
    elif event_type == "event":
        log_event(data.get("type", "INFO"), data.get("message", ""))
    
    elif event_type == "latency":
        ui_state["latency"]["def_quote_ms"] = data.get("def_quote_ms", 0)
        ui_state["latency"]["hl_ws_age_ms"] = data.get("hl_ws_age_ms", 0)
        ui_state["latency"]["price_gap_ms"] = data.get("price_gap_ms", 0)
        ui_state["latency"]["def_exec_ms"] = data.get("def_exec_ms", 0)
        ui_state["latency"]["hl_exec_ms"] = data.get("hl_exec_ms", 0)
        emit_update()
    
    elif event_type == "service_health":
        ui_state["services"]["def_api"] = data.get("def_api", "unknown")
        ui_state["services"]["def_auth"] = data.get("def_auth", "unknown")
        ui_state["services"]["hl_rest"] = data.get("hl_rest", "unknown")
        ui_state["services"]["hl_websocket"] = data.get("hl_websocket", "unknown")
        emit_update()
    
    elif event_type == "position_confirmed":
        ui_state["position_confirmed"] = data.get("confirmed", False)
        ui_state["position_mismatch"] = False
        ui_state["position_mismatch_detail"] = None
        emit_update()
    
    elif event_type == "position_mismatch":
        ui_state["position_confirmed"] = False
        ui_state["position_mismatch"] = True
        def_amt = data.get("def_amount", 0)
        hl_amt = data.get("hl_amount", 0)
        ui_state["position_mismatch_detail"] = f"DEF: {def_amt:.6f}, HL: {hl_amt:.4f}"
        emit_update()
    
    elif event_type == "cycle_bps":
        ui_state["cycle_bps"]["expected_entry"] = data.get("expected_entry", 0)
        ui_state["cycle_bps"]["actual_entry"] = data.get("actual_entry", 0)
        ui_state["cycle_bps"]["entry_slippage"] = data.get("entry_slippage", 0)
        ui_state["cycle_bps"]["expected_exit"] = data.get("expected_exit", 0)
        ui_state["cycle_bps"]["actual_exit"] = data.get("actual_exit", 0)
        ui_state["cycle_bps"]["exit_slippage"] = data.get("exit_slippage", 0)
        ui_state["cycle_bps"]["total_slippage"] = data.get("total_slippage", 0)
        emit_update()
    
    elif event_type == "token_checked":
        ui_state["token_expires_in_sec"] = data.get("expires_in_sec", 0)
        ui_state["token_expires_at"] = data.get("expires_at", 0)
        ui_state["token_last_checked"] = data.get("last_checked_at", None)
        emit_update()
    
    elif event_type == "status":
        if "status" in data:
            ui_state["status"] = data["status"]
        if "mode" in data:
            ui_state["mode"] = data["mode"]
        if "pause_reason" in data:
            ui_state["is_paused"] = True
            ui_state["pause_reason"] = data["pause_reason"]
        else:
            ui_state["is_paused"] = False
            ui_state["pause_reason"] = None
        emit_update()


def run_web_server():
    """Run Flask web server in a thread."""
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)


async def run_engine(args):
    """Run the arbitrage engine."""
    global _current_engine, _engine_event_loop
    
    # Handle size arguments - use min_size and max_size with underscores for attribute access
    min_size = getattr(args, 'min_size', None)
    max_size = getattr(args, 'max_size', None)
    
    engine = ArbEngine(size_usd=args.size, min_size=min_size, max_size=max_size)
    engine.ENTRY_THRESHOLD_BPS = args.entry
    engine.EXIT_THRESHOLD_BPS = args.exit
    engine.USE_TURBO = not args.prime
    engine.SLIPPAGE_TOLERANCE = f"{args.slip / 10000:.6f}"
    
    # Store references for manual close callbacks
    _current_engine = engine
    _engine_event_loop = asyncio.get_event_loop()
    
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
        _current_engine = None


def request_restart():
    """Called from UI to request a restart."""
    global _restart_requested
    print("[RESTART] Restart requested from UI")
    _restart_requested.set()


def close_def_weth():
    """Called from UI to manually close DEF WETH position."""
    global _current_engine, _engine_event_loop
    
    if not _current_engine:
        return {"success": False, "error": "Engine not running"}
    
    if not _engine_event_loop:
        return {"success": False, "error": "Event loop not available"}
    
    try:
        # Run async method from sync context
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            _current_engine.manual_close_def_weth(),
            _engine_event_loop
        )
        result = future.result(timeout=30)  # 30 second timeout
        return result
    except concurrent.futures.TimeoutError:
        return {"success": False, "error": "Operation timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def close_hl_short():
    """Called from UI to manually close HL short position."""
    global _current_engine, _engine_event_loop
    
    if not _current_engine:
        return {"success": False, "error": "Engine not running"}
    
    if not _engine_event_loop:
        return {"success": False, "error": "Event loop not available"}
    
    try:
        # Run async method from sync context
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            _current_engine.manual_close_hl_short(),
            _engine_event_loop
        )
        result = future.result(timeout=30)  # 30 second timeout
        return result
    except concurrent.futures.TimeoutError:
        return {"success": False, "error": "Operation timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def engine_loop(args):
    """Main engine loop with restart support. Does NOT auto-start - waits for START button."""
    global _restart_requested
    
    # DO NOT auto-start - wait for user to hit START/RESTART button
    _restart_requested.clear()
    
    # Set initial UI state to STOPPED
    ui_state["status"] = "STOPPED"
    ui_state["mode"] = "STOPPED"
    emit_update()
    
    print(f"\n[ENGINE] Engine ready but NOT started. Press START in UI to begin.")
    print(f"[ENGINE] Waiting for START command from UI...")
    
    # Wait for START button
    while not _restart_requested.is_set():
        await asyncio.sleep(0.5)
    
    # Main engine loop
    while True:
        # Clear flags and run
        _restart_requested.clear()
        clear_stop_flag()
        
        # Determine size display
        min_size = getattr(args, 'min_size', None)
        max_size = getattr(args, 'max_size', None)
        if min_size and max_size:
            size_str = f"${min_size}-${max_size} (random)"
        else:
            size_str = f"${args.size}"
        
        print(f"\n[ENGINE] Starting arbitrage engine...")
        print(f"[ENGINE] Size: {size_str}, Entry: {args.entry}bp, Exit: {args.exit}bp")
        print(f"[ENGINE] Mode: {'PRIME' if args.prime else 'TURBO'}, Slippage tolerance: {args.slip}bp\n")
        
        await run_engine(args)
        
        # Engine has stopped - update UI
        ui_state["status"] = "STOPPED"
        ui_state["mode"] = "STOPPED"
        emit_update()
        
        print("[ENGINE] Engine stopped. Waiting for RESTART command from UI...")
        
        # Block here until restart is requested
        while not _restart_requested.is_set():
            await asyncio.sleep(0.5)
        
        # Restart requested
        print("[ENGINE] Restart signal received, starting in 2 seconds...")
        await asyncio.sleep(2)


def main():
    global _engine_args
    
    parser = argparse.ArgumentParser(description="ETH Arbitrage Engine with Web UI")
    parser.add_argument("--size", type=float, default=1750, help="Order size in USD (used if no range set)")
    parser.add_argument("--min-size", type=float, default=1750, help="Minimum order size for random range")
    parser.add_argument("--max-size", type=float, default=2500, help="Maximum order size for random range")
    parser.add_argument("--cycles", type=int, default=999, help="Number of cycles to run")
    parser.add_argument("--entry", type=float, default=0.0, help="Entry threshold (bps)")
    parser.add_argument("--exit", type=float, default=4.0, help="Exit threshold (bps)")
    parser.add_argument("--prime", action="store_true", help="Use PRIME mode")
    parser.add_argument("--slip", type=float, default=7.5, help="Slippage tolerance in bps")
    
    args = parser.parse_args()
    _engine_args = args
    
    # Set UI callback
    arb_engine.set_ui_callback(handle_ui_event)
    
    # Set stop check callback
    set_stop_check(is_engine_stopped)
    
    # Set restart callback
    set_restart_callback(request_restart)
    
    # Set manual close callbacks
    set_close_def_callback(close_def_weth)
    set_close_hl_callback(close_hl_short)
    
    # Start web server in background thread
    port = int(os.environ.get('PORT', 5000))
    print(f"\n[WEB UI] Starting at http://localhost:{port}")
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Run engine loop (handles restarts)
    asyncio.run(engine_loop(args))


if __name__ == "__main__":
    main()
