"""
Lightweight Web Server for BTC Intelligence.

Serves a mobile-friendly dashboard with real-time updates via SSE.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiohttp import web
import aiohttp_cors

from adapters import HyperliquidDataAdapter
from signals import RegimeDetector, VWAPCalculator, VolatilityAnalyzer
from guards import TimeGuard, PositionGuard, LossGuard, SpikeGuard
from position import PositionTracker
from engine import RecommendationEngine, SizingConfig, Action


class BTCIntelligenceWeb:
    """Web server with real-time BTC intelligence."""
    
    def __init__(self):
        self._app = web.Application()
        self._setup_routes()
        
        # Components
        self._adapter: Optional[HyperliquidDataAdapter] = None
        self._regime: Optional[RegimeDetector] = None
        self._vwap: Optional[VWAPCalculator] = None
        self._volatility: Optional[VolatilityAnalyzer] = None
        self._time_guard: Optional[TimeGuard] = None
        self._position_guard: Optional[PositionGuard] = None
        self._loss_guard: Optional[LossGuard] = None
        self._spike_guard: Optional[SpikeGuard] = None
        self._tracker: Optional[PositionTracker] = None
        self._engine: Optional[RecommendationEngine] = None
        
        # State
        self._connected = False
        self._current_state: Dict[str, Any] = {}
        self._sse_clients: list = []
    
    def _setup_routes(self):
        """Set up web routes."""
        self._app.router.add_get('/', self._serve_index)
        self._app.router.add_get('/api/state', self._get_state)
        self._app.router.add_get('/api/stream', self._sse_stream)
        self._app.router.add_post('/api/position', self._update_position)
        
        # CORS
        cors = aiohttp_cors.setup(self._app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        })
        for route in list(self._app.router.routes()):
            cors.add(route)
    
    async def _serve_index(self, request):
        """Serve the main HTML page."""
        html_path = Path(__file__).parent / 'index.html'
        return web.FileResponse(html_path)
    
    async def _get_state(self, request):
        """Get current state as JSON."""
        return web.json_response(self._current_state)
    
    async def _sse_stream(self, request):
        """Server-Sent Events stream for real-time updates."""
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            }
        )
        await response.prepare(request)
        
        self._sse_clients.append(response)
        
        try:
            while True:
                await asyncio.sleep(1)
                if response.task.done():
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if response in self._sse_clients:
                self._sse_clients.remove(response)
        
        return response
    
    async def _broadcast_state(self):
        """Broadcast state to all SSE clients."""
        data = f"data: {json.dumps(self._current_state)}\n\n"
        
        for client in self._sse_clients[:]:
            try:
                await client.write(data.encode('utf-8'))
            except Exception:
                if client in self._sse_clients:
                    self._sse_clients.remove(client)
    
    async def _update_position(self, request):
        """Update position via API."""
        try:
            data = await request.json()
            action = data.get('action')
            
            if action == 'enter':
                self._tracker.add_entry(
                    side=data['side'],
                    size_usd=float(data['size_usd']),
                    entry_price=float(data['entry_price']),
                )
            elif action == 'close':
                self._tracker.close_position(exit_price=float(data['exit_price']))
            elif action == 'set_flat':
                self._tracker.set_position(None, 0, 0)
            
            return web.json_response({'status': 'ok'})
        except Exception as e:
            return web.json_response({'status': 'error', 'message': str(e)}, status=400)
    
    async def initialize(self):
        """Initialize all components with optimized thresholds."""
        self._adapter = HyperliquidDataAdapter(symbol="BTC")
        self._regime = RegimeDetector()          # momentum=20bps, news_shock=1.75x, min_duration=45s
        self._vwap = VWAPCalculator()            # entry=0.75σ, extended=1.25σ
        self._volatility = VolatilityAnalyzer()  # extreme=1.75x, high=1.3x
        self._time_guard = TimeGuard()
        self._position_guard = PositionGuard(max_position_usd=30000)
        self._loss_guard = LossGuard()           # $2,500 daily limit (default)
        self._spike_guard = SpikeGuard()         # 1.5% threshold, 15min pause
        self._tracker = PositionTracker()
        self._engine = RecommendationEngine(SizingConfig())  # $12.5k base, 5min validity
        
        await self._adapter.connect()
        self._connected = True
    
    async def _update_loop(self):
        """Main update loop."""
        while True:
            try:
                if self._connected:
                    await self._update_cycle()
                    await self._broadcast_state()
            except Exception as e:
                print(f"Update error: {e}")
            await asyncio.sleep(1)
    
    async def _update_cycle(self):
        """Single update cycle."""
        price = self._adapter.mid_price()
        if not price:
            return
        
        # Update signals
        self._regime.update(price)
        self._vwap.update(price)
        self._volatility.update_price(price)
        self._spike_guard.update(price)
        
        # Get states
        pos_state = self._tracker.get_state(price)
        pos_usd = pos_state.position.size_usd
        if pos_state.position.side == "short":
            pos_usd = -pos_usd
        
        regime_state = self._regime.get_state()
        vwap_state = self._vwap.get_state()
        vol_state = self._volatility.get_state()
        time_state = self._time_guard.check()
        pos_guard_state = self._position_guard.check(pos_usd)
        loss_state = self._loss_guard.check()
        spike_state = self._spike_guard.get_state()
        
        # Generate recommendation
        rec = self._engine.generate(
            current_price=price,
            regime_state=regime_state,
            vwap_state=vwap_state,
            vol_state=vol_state,
            position_state=pos_state,
            time_guard=time_state,
            position_guard=pos_guard_state,
            loss_guard=loss_state,
            spike_guard=spike_state,
        )
        
        # Build state object
        self._current_state = {
            'timestamp': datetime.utcnow().isoformat(),
            'price': round(price, 2),
            'recommendation': {
                'action': rec.action.value,
                'urgency': rec.urgency.value,
                'size_usd': rec.target_size_usd,
                'entry_low': round(rec.entry_price_low, 2),
                'entry_high': round(rec.entry_price_high, 2),
                'stop_loss': round(rec.stop_loss, 2),
                'take_profit': round(rec.take_profit, 2),
                'reason': rec.reason,
                'time_remaining': round(rec.time_remaining_seconds(), 0),
                'warnings': rec.guard_warnings,
            },
            'position': {
                'side': pos_state.position.side,
                'size_usd': round(pos_state.position.size_usd, 2),
                'size_btc': round(pos_state.position.size_btc, 6),
                'entry_price': round(pos_state.position.avg_entry_price, 2),
                'unrealized_pnl': round(pos_state.unrealized_pnl_usd, 2),
                'unrealized_pnl_pct': round(pos_state.unrealized_pnl_pct, 2),
                'is_flat': pos_state.position.is_flat,
            },
            'market': {
                'regime': regime_state.regime.value,
                'momentum_bps': round(regime_state.momentum_bps, 2),
                'direction': regime_state.momentum_direction,
                'structure': regime_state.structure_signal,
            },
            'vwap': {
                'value': round(vwap_state.vwap, 2),
                'deviation_sigma': round(vwap_state.deviation_sigma, 2),
                'zone': vwap_state.zone,
                'upper_1sigma': round(vwap_state.upper_1sigma, 2),
                'lower_1sigma': round(vwap_state.lower_1sigma, 2),
            },
            'volatility': {
                'regime': vol_state.vol_regime,
                'atr': round(vol_state.atr, 2),
                'atr_ratio': round(vol_state.atr_ratio, 2),
                'size_mult': vol_state.size_multiplier,
                'should_pause': vol_state.should_pause,
            },
            'guards': {
                'time': {
                    'paused': time_state.is_paused,
                    'reason': time_state.pause_reason,
                    'next_event': time_state.next_event_name,
                    'next_event_mins': time_state.next_event_in_minutes,
                },
                'position': {
                    'exposure_pct': round(pos_guard_state.exposure_pct * 100, 1),
                    'level': pos_guard_state.exposure_level,
                    'warning': pos_guard_state.warning,
                },
                'loss': {
                    'paused': loss_state.is_paused,
                    'daily_pnl': round(loss_state.daily_pnl_usd, 2),
                    'pct_of_limit': round(loss_state.pnl_pct_of_limit * 100, 1),
                    'level': loss_state.warning_level,
                },
                'spike': {
                    'paused': spike_state.is_paused,
                    'detected': spike_state.spike_detected,
                    'direction': spike_state.spike_direction,
                    'remaining_secs': round(spike_state.pause_remaining_seconds, 0),
                },
            },
        }
    
    async def run(self, host='0.0.0.0', port=8080):
        """Run the web server."""
        await self.initialize()
        
        # Start update loop
        asyncio.create_task(self._update_loop())
        
        # Start web server
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        
        print(f"\n✅ BTC Intelligence Web UI running at http://localhost:{port}\n")
        
        # Keep running
        while True:
            await asyncio.sleep(3600)


async def main():
    server = BTCIntelligenceWeb()
    await server.run()


if __name__ == '__main__':
    asyncio.run(main())
