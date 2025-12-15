"""
Elite Hybrid Trading Agent - Code + LLM Brain
Autonomous trading with AI reasoning
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
        self.portfolio_manager = PortfolioManager(self.client, self.market_scanner)  # Connect scanner
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
        
        # Error tracking
        self._max_consecutive_errors = 5
        self._consecutive_errors = 0
        
        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._running = False
        self._cycle_lock = asyncio.Lock()
        
        logger.info("🤖 Elite Hybrid Trading Agent v4.0 initialized")
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
        
        logger.info(f"✅ Agent initialized")
        logger.info(f"   Competition: {self.competition_id}")
        logger.info(f"   Total Trades: {self.metrics.total_trades}")
        logger.info(f"   Win Rate: {self.metrics.win_rate*100:.1f}%")
    
    async def _select_competition(self) -> str:
        """Select competition to participate in"""
        if config.COMPETITION_ID:
            logger.info(f"✅ Using configured competition: {config.COMPETITION_ID}")
            return config.COMPETITION_ID
        
        try:
            user_comps = await self.client.get_user_competitions()
            competitions = user_comps.get("competitions", [])
            
            if not competitions:
                all_comps = await self.client.get_competitions()
                competitions = all_comps.get("competitions", [])
            
            if not competitions:
                raise ValueError("No competitions found")
            
            active = [c for c in competitions if c.get("status") == "active"]
            comp = active[0] if active else competitions[0]
            
            comp_id = comp.get("id")
            logger.info(f"✅ Selected competition: {comp.get('name', comp_id)}")
            return comp_id
            
        except Exception as e:
            raise ValueError(f"Failed to select competition: {e}")
    
    async def run_cycle(self):
        """Run a single autonomous trading cycle"""
        cycle_start = datetime.now(timezone.utc)
        
        logger.info(f"\n{'~' * 70}")
        logger.info(f"🔄 AUTONOMOUS TRADING CYCLE")
        logger.info(f"   Time: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        logger.info(f"{'~' * 70}")
        
        # Step 1: Scan market for opportunities
        logger.info("🔍 Step 1: Scanning market for opportunities...")
        try:
            discovered_tokens = await self.market_scanner.scan_market()
            
            if not discovered_tokens:
                logger.info("🔭 No opportunities found in market scan")
                return
            
            top_opportunities = discovered_tokens[:10]
            logger.info(f"✅ Found {len(top_opportunities)} top opportunities")
            
            for i, token in enumerate(top_opportunities[:5], 1):
                logger.info(
                    f"   {i}. {token.symbol} on {token.chain} | "
                    f"Score: {token.opportunity_score:.2f} | "
                    f"{token.change_24h_pct:+.2f}% | "
                    f"Vol: ${token.volume_24h:,.0f}"
                )
        except Exception as e:
            logger.error(f"❌ Market scan failed: {e}")
            return
        
        # Step 2: Get portfolio state
        logger.info("\n💼 Step 2: Analyzing portfolio...")
        try:
            portfolio = await self.portfolio_manager.get_portfolio_state(
                self.competition_id
            )
            
            if not portfolio or portfolio.get("total_value", 0) == 0:
                logger.error("🚫 Cannot retrieve portfolio")
                return
            
            self._log_portfolio_summary(portfolio)
            
            # ✅ Create market snapshot for decision making
            market_snapshot = MarketSnapshot.from_market_data(
                {token.symbol: {
                    "change_24h_pct": token.change_24h_pct,
                    "volume_24h": token.volume_24h,
                    "price": token.price
                } for token in discovered_tokens}
            )
            
        except Exception as e:
            logger.error(f"❌ Portfolio analysis failed: {e}")
            return
        
        # Step 3: Check risk limits
        if not self._check_risk_limits(portfolio):
            logger.warning("⛔ Risk limits exceeded, skipping trades")
            return
        
        # Step 4: Generate trade decision
        logger.info("\n🎯 Step 3: Generating trade decision...")
        try:
            decision = await self.strategy.generate_decision(
                portfolio,
                top_opportunities,
                self.metrics,
                market_snapshot
            )
            
            if decision.action == TradingAction.HOLD:
                logger.info(f"⏸️ HOLD: {decision.reason}")
                return
            
        except Exception as e:
            logger.error(f"❌ Strategy decision failed: {e}")
            return
        
        # Step 5: Execute trade
        logger.info("\n📤 Step 4: Executing trade...")
        try:
            success = await self.portfolio_manager.execute_trade(
                decision,
                portfolio,
                self.competition_id
            )
            
            if success:
                await self._record_trade(decision, portfolio)
                logger.info("✅ Trade executed successfully")
                
                # ✅ ENHANCED: Try additional trades if needed (with fresh data)
                if self.trades_today < config.MIN_TRADES_PER_DAY:
                    logger.info(f"🔄 Attempting additional trade to meet daily minimum...")
                    await asyncio.sleep(2)
                    
                    # Refresh portfolio
                    portfolio = await self.portfolio_manager.get_portfolio_state(
                        self.competition_id
                    )
                    
                    if portfolio:
                        # ✅ FIXED: Create fresh market snapshot for retry
                        remaining_tokens = top_opportunities[1:]  # Skip first
                        if remaining_tokens:
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
                                fresh_market_snapshot  # ✅ Use fresh snapshot
                            )
                            
                            if decision.action != TradingAction.HOLD:
                                success = await self.portfolio_manager.execute_trade(
                                    decision,
                                    portfolio,
                                    self.competition_id
                                )
                                if success:
                                    await self._record_trade(decision, portfolio)
            
        except Exception as e:
            logger.error(f"❌ Trade execution failed: {e}")
        
        # Log cycle duration
        cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(f"\n⏱️ Cycle completed in {cycle_duration:.2f}s")
    
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
        
        # Consecutive losses
        if self.metrics.consecutive_losses >= 3:
            logger.warning(f"⚠️ {self.metrics.consecutive_losses} consecutive losses")
        
        return True
    
    async def _record_trade(self, decision: TradeDecision, portfolio: Dict):
        """Record trade in metrics"""
        self.trades_today += 1
        self.metrics.total_trades += 1
        self.metrics.trades_today = self.trades_today
        
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
    
    async def run(self):
        """Main trading loop"""
        logger.info("=" * 80)
        logger.info("🚀 SELF-THINKING TRADING AGENT v4.0 🚀")
        logger.info("=" * 80)
        logger.info(f"   Environment: {'SANDBOX' if config.USE_SANDBOX else 'PRODUCTION'}")
        logger.info(f"   Auto Discovery: {config.AUTO_DISCOVERY_MODE}")
        logger.info(f"   Trading Interval: {config.TRADING_INTERVAL}s")
        logger.info("=" * 80)
        
        try:
            await self.initialize()
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            return
        
        self._running = True
        self._consecutive_errors = 0
        
        while self._running and not self._shutdown_event.is_set():
            if self._consecutive_errors >= self._max_consecutive_errors:
                logger.critical(f"💀 FATAL: {self._consecutive_errors} consecutive errors")
                break
            
            async with self._cycle_lock:
                try:
                    await self.run_cycle()
                    self._consecutive_errors = 0
                    
                except CircuitBreakerOpenError as e:
                    logger.warning(f"⚠️ Circuit breaker open: {e}")
                    
                except Exception as e:
                    self._consecutive_errors += 1
                    logger.error(f"❌ Cycle error ({self._consecutive_errors}/{self._max_consecutive_errors}): {e}")
                    
                    if self._consecutive_errors > 3:
                        backoff = min(60 * (2 ** (self._consecutive_errors - 3)), 300)
                        logger.warning(f"⏳ Backing off for {backoff}s")
                        try:
                            await asyncio.wait_for(
                                self._shutdown_event.wait(),
                                timeout=backoff
                            )
                        except asyncio.TimeoutError:
                            pass
            
            # Wait for next cycle
            if self._consecutive_errors <= 3:
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
    ║       🔥 ELITE HYBRID TRADING AGENT v4.0 🔥                      ║
    ║                                                                  ║
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
        logger.critical(f"💀 Fatal error: {e}")
        sys.exit(1)