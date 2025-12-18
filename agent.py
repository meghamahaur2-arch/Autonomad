"""
Elite Hybrid Trading Agent - Code + LLM Brain
Autonomous trading with AI reasoning
ALL CRITICAL ISSUES FIXED
"""
import asyncio
import signal
import sys
from datetime import datetime, timezone, date
from typing import Optional, Dict
from config import config
from logging_manager import get_logger, log_manager
from models import (
    TradingAction, TradeDecision, TradingMetrics,
    CircuitBreakerOpenError, MarketSnapshot
)
from api_client import RecallAPIClient
from market_scanner import MarketScanner
from strategy import TradingStrategy
from portfolio_manager import PortfolioManager
from persistence import PersistenceManager
from llm_brain import LLMBrain

logger = get_logger("TradingAgent")


class TradingAgent:
    """
    🔥 Elite Hybrid Trading Agent
    
    Code:   Market scanning, indicators, execution
    LLM:    Token ranking, trade confirmation, regime detection
    """
    
    def __init__(self):
        # Validate configuration
        errors = config.validate()
        if errors:
            for error in errors:
                logger.error(f"❌ Config error: {error}")
            raise ValueError("Invalid configuration")
        
        # Initialize components with feedback loop
        self.client = RecallAPIClient(config.RECALL_API_KEY, config.base_url)
        self.market_scanner = MarketScanner()
        self.llm_brain = LLMBrain()
        self.portfolio_manager = PortfolioManager(self.client, self.market_scanner)
        self.strategy = TradingStrategy(llm_brain=self.llm_brain)
        self.persistence = PersistenceManager(
            config.STATE_FILE,
            config.STATE_BACKUP_COUNT
        )
        
        # State
        self.competition_id: Optional[str] = None
        self.metrics = TradingMetrics()
        self.trades_today: int = 0
        self.last_trade_date: Optional[date] = None
        self.daily_start_value: float = 0
        
        # Token metadata cache
        self._token_cache: Dict[str, Dict] = {}
        
        # Error tracking with exponential backoff
        self._max_consecutive_errors = 5
        self._consecutive_errors = 0
        self._last_error_time: Optional[datetime] = None
        
        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._running = False
        self._cycle_lock = asyncio.Lock()
        
        # Health check
        self._last_successful_cycle: Optional[datetime] = None
        self._cycle_count = 0
        
        logger.info("🤖 Elite Hybrid Trading Agent v4.1 initialized")
        if self.llm_brain.enabled:
            logger.info(f"   🧠 LLM Brain: ENABLED ({self.llm_brain.model})")
        else:
            logger.info("   🤖 LLM Brain: DISABLED (pure algorithmic mode)")
    
    async def initialize(self):
        """Initialize agent state"""
        # Load persisted state
        state = await self.persistence.load_state()
        
        await self.portfolio_manager.restore_state(state)
        self.metrics = state.get("metrics", TradingMetrics())
        self.trades_today = state.get("trades_today", 0)
        self.last_trade_date = state.get("last_trade_date")
        self.daily_start_value = state.get("daily_start_value", 0)
        
        # Select competition
        self.competition_id = await self._select_competition()
        
        # Reset daily counters if new day
        await self._check_daily_reset()
        
        logger.info(f"✅ Agent initialized")
        logger.info(f"   Competition: {self.competition_id}")
        logger.info(f"   Total Trades: {self.metrics.total_trades}")
        logger.info(f"   Win Rate: {self.metrics.win_rate*100:.1f}%")
    
    async def _select_competition(self) -> str:
        """Select competition to participate in - FIXED: Proper error handling"""
        # Use configured competition if provided
        if config.COMPETITION_ID:
            logger.info(f"✅ Using configured competition: {config.COMPETITION_ID}")
            return config.COMPETITION_ID
        
        try:
            # Try to get user's competitions first
            logger.info("🔍 Fetching user competitions...")
            user_comps = await self.client.get_user_competitions()
            competitions = user_comps.get("competitions", [])
            
            # If no user competitions, get all public competitions
            if not competitions:
                logger.info("🔍 No user competitions, fetching public competitions...")
                all_comps = await self.client.get_competitions()
                competitions = all_comps.get("competitions", [])
            
            # If still no competitions found, raise error
            if not competitions:
                raise ValueError(
                    "No competitions found. Please either:\n"
                    "  1. Set COMPETITION_ID in your .env file, or\n"
                    "  2. Ensure your API key has access to competitions"
                )
            
            # Filter for active competitions
            active_comps = [c for c in competitions if c.get("status") == "active"]
            
            # Use first active competition, or first competition if none active
            selected_comp = active_comps[0] if active_comps else competitions[0]
            comp_id = selected_comp.get("id")
            comp_name = selected_comp.get("name", comp_id)
            comp_status = selected_comp.get("status", "unknown")
            
            logger.info(f"✅ Auto-selected competition:")
            logger.info(f"   Name: {comp_name}")
            logger.info(f"   ID: {comp_id}")
            logger.info(f"   Status: {comp_status}")
            
            if comp_status != "active":
                logger.warning(f"⚠️ Competition status is '{comp_status}' (not 'active')")
            
            return comp_id
            
        except Exception as e:
            error_msg = (
                f"Failed to select competition: {e}\n\n"
                f"Please set COMPETITION_ID in your .env file:\n"
                f'  COMPETITION_ID="your-competition-id-here"'
            )
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
    
    async def _check_daily_reset(self):
        """Reset daily counters if new day"""
        today = date.today()
        
        if self.last_trade_date and self.last_trade_date != today:
            logger.info(f"📅 New trading day: {today}")
            self.trades_today = 0
            self.last_trade_date = today
            
            # Get portfolio value for daily P&L tracking
            try:
                portfolio = await self.portfolio_manager.get_portfolio_state(
                    self.competition_id
                )
                self.daily_start_value = portfolio.get("total_value", 0)
                logger.info(f"   Starting value: ${self.daily_start_value:,.2f}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to get daily start value: {e}")
    
    def _cache_token_metadata(self, tokens: list):
        """Cache token metadata for later lookup"""
        for token in tokens:
            key = f"{token.symbol}_{token.chain}"
            self._token_cache[key] = {
                "token_address": token.address,
                "chain": token.chain,
                "price": token.price,
                "liquidity": token.liquidity_usd,
                "volume_24h": token.volume_24h,
                "change_24h": token.change_24h_pct,
                "score": token.opportunity_score,
                "market_cap": token.market_cap
            }
    
    async def run_cycle(self):
        """Run a single autonomous trading cycle with error boundaries"""
        cycle_start = datetime.now(timezone.utc)
        
        try:
            logger.info(f"\n{'~' * 70}")
            logger.info(f"🔄 AUTONOMOUS TRADING CYCLE #{self._cycle_count + 1}")
            logger.info(f"   Time: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            logger.info(f"{'~' * 70}")
            
            # Step 1: Scan market for opportunities
            logger.info("🔍 Step 1: Scanning market for opportunities...")
            discovered_tokens = await asyncio.wait_for(
                self.market_scanner.scan_market(),
                timeout=120  # 2 minute timeout for market scan
            )
            
            if not discovered_tokens:
                logger.info("🔭 No opportunities found in market scan")
                self._mark_cycle_success()
                return
            
            top_opportunities = discovered_tokens[:10]
            logger.info(f"✅ Found {len(top_opportunities)} top opportunities")
            
            # Cache token metadata
            self._cache_token_metadata(top_opportunities)
            
            for i, token in enumerate(top_opportunities[:5], 1):
                logger.info(
                    f"   {i}. {token.symbol} on {token.chain} | "
                    f"Score: {token.opportunity_score:.2f} | "
                    f"{token.change_24h_pct:+.2f}% | "
                    f"Vol: ${token.volume_24h:,.0f}"
                )
            
            # Step 2: Get portfolio state
            logger.info("\n💼 Step 2: Analyzing portfolio...")
            portfolio = await asyncio.wait_for(
                self.portfolio_manager.get_portfolio_state(self.competition_id),
                timeout=30
            )
            
            if not portfolio or portfolio.get("total_value", 0) == 0:
                logger.error("🚫 Cannot retrieve portfolio")
                return
            
            self._log_portfolio_summary(portfolio)
            
            # Create market snapshot
            market_snapshot = MarketSnapshot.from_market_data(
                {token.symbol: {
                    "change_24h_pct": token.change_24h_pct,
                    "volume_24h": token.volume_24h,
                    "price": token.price
                } for token in discovered_tokens}
            )
            
            # Step 3: Check risk limits
            if not self._check_risk_limits(portfolio):
                logger.warning("⛔ Risk limits exceeded, skipping trades")
                self._mark_cycle_success()
                return
            
            # Step 4: Position reconciliation (every 3 cycles)
            if self._cycle_count % 3 == 0:
                await self._reconcile_positions(portfolio)
            
            # Step 5: Generate trade decision
            logger.info("\n🎯 Step 3: Generating trade decision...")
            decision = await asyncio.wait_for(
                self.strategy.generate_decision(
                    portfolio,
                    top_opportunities,
                    self.metrics,
                    market_snapshot
                ),
                timeout=60  # 1 minute timeout for strategy
            )
            
            if decision.action == TradingAction.HOLD:
                logger.info(f"⏸️ HOLD: {decision.reason}")
                self._mark_cycle_success()
                return
            
            # Inject metadata from cache
            await self._inject_trade_metadata(decision, portfolio)
            
            # Step 6: Execute trade
            logger.info("\n📤 Step 4: Executing trade...")
            success = await asyncio.wait_for(
                self.portfolio_manager.execute_trade(
                    decision,
                    portfolio,
                    self.competition_id
                ),
                timeout=45  # 45s timeout for trade execution
            )
            
            if success:
                await self._record_trade(decision, portfolio)
                logger.info("✅ Trade executed successfully")
                
                # Try additional trades if needed
                if self.trades_today < config.MIN_TRADES_PER_DAY:
                    await self._attempt_additional_trade(top_opportunities)
            
            self._mark_cycle_success()
            
        except asyncio.TimeoutError as e:
            logger.error(f"⏰ Cycle timeout: {e}")
            self._consecutive_errors += 1
            
        except CircuitBreakerOpenError as e:
            logger.warning(f"⚠️ Circuit breaker open: {e}")
            # Don't count as error - circuit breaker is working as designed
            
        except Exception as e:
            logger.error(f"❌ Cycle error: {e}", exc_info=True)
            self._consecutive_errors += 1
            self._last_error_time = datetime.now(timezone.utc)
            
        finally:
            # Log cycle duration
            cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            logger.info(f"\n⏱️ Cycle completed in {cycle_duration:.2f}s")
            
            # Auto-save state after each cycle
            try:
                await self._save_state()
            except Exception as e:
                logger.error(f"❌ Failed to save state: {e}")
    
    async def _inject_trade_metadata(self, decision: TradeDecision, portfolio: Dict):
        """Inject metadata from cache or portfolio"""
        if decision.action == TradingAction.BUY:
            to_symbol = decision.to_token
            if to_symbol in self._token_cache:
                cached = self._token_cache[to_symbol]
                decision.metadata.update(cached)
                logger.debug(f"✅ Injected metadata for {to_symbol}")
            else:
                logger.warning(f"⚠️ No cached metadata for {to_symbol}")
        
        elif decision.action == TradingAction.SELL:
            from_symbol = decision.from_token
            holdings = portfolio.get("holdings", {})
            if from_symbol in holdings:
                holding = holdings[from_symbol]
                decision.metadata.update({
                    "token_address": holding.get("tokenAddress", ""),
                    "chain": holding.get("chain", "eth"),
                    "price": holding.get("price", 0),
                    "liquidity": 999999,  # Skip validation for sells
                    "volume_24h": 999999,
                    "score": 10.0
                })
                logger.debug(f"✅ Injected sell metadata for {from_symbol}")
    
    async def _attempt_additional_trade(self, opportunities: list):
        """Attempt additional trade to meet daily minimum"""
        logger.info(f"🔄 Attempting additional trade (daily: {self.trades_today}/{config.MIN_TRADES_PER_DAY})")
        
        try:
            await asyncio.sleep(2)
            
            # Refresh portfolio
            portfolio = await self.portfolio_manager.get_portfolio_state(
                self.competition_id
            )
            
            if not portfolio:
                return
            
            remaining_tokens = opportunities[1:]
            if not remaining_tokens:
                return
            
            # Cache metadata for remaining tokens
            self._cache_token_metadata(remaining_tokens)
            
            fresh_market_snapshot = MarketSnapshot.from_market_data(
                {token.symbol: {
                    "change_24h_pct": token.change_24h_pct,
                    "volume_24h": token.volume_24h,
                    "price": token.price
                } for token in remaining_tokens}
            )
            
            decision = await self.strategy.generate_decision(
                portfolio,
                remaining_tokens,
                self.metrics,
                fresh_market_snapshot
            )
            
            if decision.action != TradingAction.HOLD:
                # Inject metadata
                await self._inject_trade_metadata(decision, portfolio)
                
                success = await self.portfolio_manager.execute_trade(
                    decision,
                    portfolio,
                    self.competition_id
                )
                
                if success:
                    await self._record_trade(decision, portfolio)
                    
        except Exception as e:
            logger.error(f"❌ Additional trade failed: {e}")
    
    async def _reconcile_positions(self, portfolio: Dict):
        """Reconcile tracked positions with actual portfolio"""
        logger.info("🔄 Reconciling positions...")
        
        try:
            holdings = portfolio.get("holdings", {})
            tracked_symbols = set(self.portfolio_manager.tracked_positions.keys())
            
            # ✅ FIX: Only consider non-stablecoin holdings above minimum value
            actual_symbols = set()
            for sym, h in holdings.items():
                # Skip stablecoins
                if self._is_stablecoin(sym):
                    continue
                
                # Skip positions below minimum value
                if h.get("value", 0) < config.MIN_POSITION_VALUE:
                    continue
                
                actual_symbols.add(sym)
            
            # Find orphaned tracked positions (no longer in portfolio)
            orphaned = tracked_symbols - actual_symbols
            if orphaned:
                logger.warning(f"⚠️ Found {len(orphaned)} orphaned positions: {orphaned}")
                for symbol in orphaned:
                    logger.info(f"   Removing orphaned position: {symbol}")
                    del self.portfolio_manager.tracked_positions[symbol]
            
            # Find untracked positions (in portfolio but not tracked)
            untracked = actual_symbols - tracked_symbols
            if untracked:
                logger.info(f"📍 Found {len(untracked)} untracked positions: {untracked}")
                for symbol in untracked:
                    holding = holdings[symbol]
                    logger.info(f"   Creating tracker for: {symbol}")
                    
                    from models import TrackedPosition
                    self.portfolio_manager.tracked_positions[symbol] = TrackedPosition(
                        symbol=symbol,
                        entry_price=holding.get("price", 0),
                        entry_amount=holding.get("amount", 0),
                        entry_value_usd=holding.get("value", 0),
                        entry_timestamp=datetime.now(timezone.utc).isoformat(),
                        token_address=holding.get("tokenAddress", ""),
                        chain=holding.get("chain", "")
                    )
            
            if not orphaned and not untracked:
                logger.info("✅ Positions reconciled - all in sync")
                
        except Exception as e:
            logger.error(f"❌ Position reconciliation failed: {e}")
    
    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if token is a stablecoin"""
        if symbol in config.TOKENS:
            return config.TOKENS[symbol].stable
        
        stable_patterns = ["USDC", "USDT", "DAI", "USD", "BUSD", "TUSD", "FRAX"]
        return any(pattern in symbol.upper() for pattern in stable_patterns)
    
    def _mark_cycle_success(self):
        """Mark cycle as successful"""
        self._consecutive_errors = 0
        self._last_successful_cycle = datetime.now(timezone.utc)
        self._cycle_count += 1
    
    def _check_risk_limits(self, portfolio: Dict) -> bool:
        """Check if we're within risk limits"""
        total_value = portfolio.get("total_value", 0)
        
        # Daily loss limit
        if self.daily_start_value > 0:
            daily_pnl_pct = (total_value - self.daily_start_value) / self.daily_start_value
            if daily_pnl_pct <= config.MAX_DAILY_LOSS_PCT:
                logger.warning(
                    f"⛔ Daily loss limit: {daily_pnl_pct*100:.1f}% "
                    f"<= {config.MAX_DAILY_LOSS_PCT*100:.1f}%"
                )
                return False
        
        # Consecutive losses with cooldown
        if self.metrics.consecutive_losses >= 5:
            logger.warning(f"⛔ Too many consecutive losses: {self.metrics.consecutive_losses}")
            return False
        
        return True
    
    async def _record_trade(self, decision: TradeDecision, portfolio: Dict):
        """Record trade in metrics"""
        self.trades_today += 1
        self.last_trade_date = date.today()
        self.metrics.total_trades += 1
        self.metrics.trades_today = self.trades_today
        
        # Update daily start value if first trade of day
        if self.trades_today == 1:
            self.daily_start_value = portfolio.get("total_value", 0)
        
        # Save state
        await self._save_state()
    
    def _log_portfolio_summary(self, portfolio: Dict):
        """Log portfolio summary"""
        total = portfolio.get("total_value", 0)
        positions = portfolio.get("positions", [])
        
        logger.info(f"   Total Value: ${total:,.2f}")
        logger.info(f"   Active Positions: {len(positions)}/{config.MAX_POSITIONS}")
        logger.info(f"   Daily Trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
        
        if self.daily_start_value > 0:
            daily_pnl_pct = ((total - self.daily_start_value) / self.daily_start_value) * 100
            emoji = "📈" if daily_pnl_pct >= 0 else "📉"
            logger.info(f"   Daily P&L: {emoji} {daily_pnl_pct:+.2f}%")
    
    async def _save_state(self):
        """Save current state"""
        state = {
            "portfolio_state": await self.portfolio_manager.get_state(),
            "metrics": self.metrics,
            "trades_today": self.trades_today,
            "last_trade_date": self.last_trade_date,
            "daily_start_value": self.daily_start_value,
        }
        await self.persistence.save_state(state)
    
    def get_health_status(self) -> Dict:
        """Get health status for monitoring"""
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "consecutive_errors": self._consecutive_errors,
            "last_successful_cycle": self._last_successful_cycle.isoformat() if self._last_successful_cycle else None,
            "last_error_time": self._last_error_time.isoformat() if self._last_error_time else None,
            "trades_today": self.trades_today,
            "total_trades": self.metrics.total_trades,
            "win_rate": self.metrics.win_rate
        }
    
    async def run(self):
        """Main trading loop with enhanced error handling"""
        logger.info("=" * 80)
        logger.info("🚀 SELF-THINKING TRADING AGENT v4.1 🚀")
        logger.info("=" * 80)
        logger.info(f"   Environment: {'SANDBOX' if config.USE_SANDBOX else 'PRODUCTION'}")
        logger.info(f"   Auto Discovery: {config.AUTO_DISCOVERY_MODE}")
        logger.info(f"   Trading Interval: {config.TRADING_INTERVAL}s")
        logger.info("=" * 80)
        
        try:
            await self.initialize()
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}", exc_info=True)
            return
        
        self._running = True
        self._consecutive_errors = 0
        
        while self._running and not self._shutdown_event.is_set():
            # Fatal error threshold
            if self._consecutive_errors >= self._max_consecutive_errors:
                logger.critical(
                    f"💀 FATAL: {self._consecutive_errors} consecutive errors. "
                    "Stopping agent to prevent damage."
                )
                break
            
            async with self._cycle_lock:
                await self.run_cycle()
            
            # Calculate backoff delay
            if self._consecutive_errors > 0:
                # Exponential backoff: 60s, 120s, 240s, 300s (max)
                backoff = min(60 * (2 ** self._consecutive_errors), 300)
                logger.warning(f"⏳ Backing off for {backoff}s due to errors")
                
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=backoff
                    )
                except asyncio.TimeoutError:
                    pass
            else:
                # Normal interval
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=config.TRADING_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass
        
        logger.info("🛑 Trading loop stopped")
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("🛑 Initiating graceful shutdown...")
        self._running = False
        self._shutdown_event.set()
        
        # Wait for current cycle to complete
        async with self._cycle_lock:
            pass
        
        try:
            await self._save_state()
            logger.info("💾 Final state saved")
        except Exception as e:
            logger.error(f"❌ Failed to save state: {e}")
        
        try:
            await self.client.close()
            await self.market_scanner.close()
            await self.llm_brain.close()
            logger.info("🔌 Connections closed")
        except Exception as e:
            logger.error(f"❌ Error closing connections: {e}")
        
        logger.info("=" * 80)
        logger.info("📊 FINAL STATISTICS")
        logger.info("=" * 80)
        logger.info(f"   Total Cycles: {self._cycle_count}")
        logger.info(f"   Total Trades: {self.metrics.total_trades}")
        logger.info(f"   Win Rate: {self.metrics.win_rate*100:.1f}%")
        logger.info(f"   Total P&L: ${self.metrics.total_pnl_usd:+,.2f}")
        logger.info("=" * 80)
        logger.info("✅ Shutdown complete")


async def main():
    """Main entry point"""
    agent = TradingAgent()
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("📡 Received shutdown signal")
        asyncio.create_task(agent.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass  # Windows doesn't support this
    
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt")
    finally:
        if agent._running:
            await agent.shutdown()


if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║       🔥 ELITE HYBRID TRADING AGENT v4.1 🔥                  ║
    ║                                                              ║
    ║   Code (muscle) + LLM Brain (intelligence) = Elite Performance  ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"❌ Configuration error: {error}")
        sys.exit(1)
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"💀 Fatal error: {e}", exc_info=True)
        sys.exit(1)