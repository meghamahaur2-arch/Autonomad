"""
Trading Strategy - Hybrid Algorithmic + LLM Decision Making
"""
from typing import List, Dict, Optional
from config import config
from models import (
    TradingAction, TradeDecision, Conviction, SignalType,
    DiscoveredToken, TradingMetrics, Position, MarketSnapshot
)
from logging_manager import get_logger
from typing import Tuple
logger = get_logger("Strategy")


class TradingStrategy:
    """
    🔥 ELITE HYBRID STRATEGY
    
    Code does:  Market scanning, indicators, execution
    LLM does:   Token ranking, signal confirmation, regime detection
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain  # Injected LLM brain
        logger.info("📊 Hybrid Trading Strategy initialized")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """
        Generate trade decision (hybrid approach)
        
        1. Code checks exits (fast reflexes)
        2. LLM ranks opportunities (brain)
        3. Code filters by rules
        4. LLM confirms final trade (vibes check)
        """
        positions = portfolio.get("positions", [])
        
        # STEP 1: Check exit signals (pure algorithmic - fast)
        for position in positions:
            exit_decision = self._check_exit_signals(position)
            if exit_decision:
                return exit_decision
        
        # STEP 2: Get LLM to rank opportunities (if enabled)
        if self.llm_brain and self.llm_brain.enabled and opportunities:
            logger.info("🧠 Getting LLM token rankings...")
            ranked_opportunities = await self.llm_brain.rank_tokens(
                opportunities,
                market_snapshot
            )
            # Use LLM scores
            opportunities_with_scores = [
                (token, llm_score, reason) 
                for token, llm_score, reason in ranked_opportunities
            ]
        else:
            # Fallback to algorithmic scores
            opportunities_with_scores = [
                (token, token.opportunity_score, "Algorithmic")
                for token in opportunities
            ]
        
        # STEP 3: Check for entry (algorithmic filtering)
        entry_candidate = self._check_entry_signals(
            portfolio, 
            opportunities_with_scores,
            metrics
        )
        
        if entry_candidate.action == TradingAction.HOLD:
            return entry_candidate
        
        # STEP 4: LLM confirmation (vibes check)
        if self.llm_brain and self.llm_brain.enabled:
            logger.info("🧠 Getting LLM trade confirmation...")
            
            # Extract token from decision
            token = None
            for opp, score, reason in opportunities_with_scores:
                if opp.symbol == entry_candidate.to_token.split('_')[0]:
                    token = opp
                    break
            
            if token:
                portfolio_stats = {
                    "positions": len(positions),
                    "deployed_pct": self._get_deployed_pct(portfolio),
                    "win_rate": metrics.win_rate,
                    "consecutive_losses": metrics.consecutive_losses
                }
                
                approved, reasoning, new_conviction = await self.llm_brain.confirm_trade(
                    token,
                    entry_candidate.signal_type.value,
                    entry_candidate.conviction,
                    market_snapshot,
                    portfolio_stats
                )
                
                if not approved:
                    logger.warning(f"❌ LLM rejected trade: {reasoning}")
                    return TradeDecision(
                        action=TradingAction.HOLD,
                        reason=f"🧠 LLM Rejected: {reasoning}"
                    )
                
                # Update conviction based on LLM
                entry_candidate.conviction = new_conviction
                entry_candidate.reason = f"{entry_candidate.reason} | LLM: {reasoning}"
        
        return entry_candidate
    
    def _check_exit_signals(self, position: Position) -> Optional[TradeDecision]:
        """Check if position should be exited"""
        
        # Stop Loss
        if position.should_stop_loss(config.STOP_LOSS_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"🛑 Stop-loss: {position.pnl_pct:.1f}% loss on ${position.value:.2f}"
            )
        
        # Trailing Stop
        if config.ENABLE_TRAILING_STOP and position.should_trailing_stop(config.TRAILING_STOP_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"📉 Trailing stop triggered"
            )
        
        # Take Profit (partial)
        if position.should_take_profit(config.TAKE_PROFIT_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.5,
                conviction=Conviction.MEDIUM,
                signal_type=SignalType.TAKE_PROFIT,
                reason=f"🎯 Take-profit: {position.pnl_pct:.1f}% gain (partial 50%)"
            )
        
        return None
    
    def _check_entry_signals(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],  # [(token, score, reason)]
        metrics: TradingMetrics
    ) -> TradeDecision:
        """Check for entry opportunities"""
        
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        # Calculate deployed capital
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if sym != "USDC" and not config.TOKENS.get(sym, config.TOKENS.get("USDC")).stable
        )
        deployed_pct = deployed / total_value if total_value > 0 else 0
        
        # Check max deployment
        if deployed_pct >= config.MAX_PORTFOLIO_RISK:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"⚠️ Max risk deployed ({deployed_pct*100:.0f}%)"
            )
        
        # Check USDC availability
        usdc_value = self._get_total_usdc(holdings)
        min_required = config.BASE_POSITION_SIZE * 0.5
        
        if usdc_value < min_required:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"💰 Insufficient USDC (${usdc_value:.0f} < ${min_required:.0f})"
            )
        
        # No opportunities
        if not opportunities_with_scores:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="🔭 No opportunities meeting criteria"
            )
        
        # Find best opportunity
        existing_symbols = {pos.symbol for pos in positions}
        
        for token, score, llm_reason in opportunities_with_scores:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols:
                continue
            
            # Check max positions
            if len(existing_symbols) >= config.MAX_POSITIONS:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached"
                )
            
            # Score must be above threshold (use LLM or algorithmic score)
            if score < config.OPPORTUNITY_SCORE_THRESHOLD:
                continue
            
            # Determine signal type and conviction
            signal_type, conviction = self._classify_opportunity(token)
            
            # Calculate position size
            position_size = self._calculate_position_size(
                total_value,
                conviction,
                metrics
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                continue
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            return TradeDecision(
                action=TradingAction.BUY,
                from_token="USDC",
                to_token=symbol_key,
                amount_usd=position_size,
                conviction=conviction,
                signal_type=signal_type,
                reason=f"🎯 {signal_type.value}: {token.symbol} | Score: {score:.1f} | {llm_reason}",
                metadata={
                    "token_address": token.address,
                    "chain": token.chain,
                    "score": score,
                    "change_24h": token.change_24h_pct,
                    "volume_24h": token.volume_24h,
                    "liquidity": token.liquidity_usd,
                    "price": token.price
                }
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason="🔍 No suitable opportunities found"
        )
    
    def _classify_opportunity(self, token: DiscoveredToken) -> tuple:
        """Classify opportunity type and assign conviction"""
        
        change = token.change_24h_pct
        score = token.opportunity_score
        
        # High conviction signals
        if score > 15:
            if change > 10:
                return SignalType.BREAKOUT, Conviction.HIGH
            elif change < -10:
                return SignalType.MEAN_REVERSION, Conviction.HIGH
            elif token.volume_24h > 5_000_000:
                return SignalType.VOLUME_SPIKE, Conviction.HIGH
        
        # Medium conviction
        if score > 10:
            if change > 5:
                return SignalType.MOMENTUM, Conviction.MEDIUM
            elif -15 < change < -5:
                return SignalType.MEAN_REVERSION, Conviction.MEDIUM
            elif token.volume_24h > 2_000_000:
                return SignalType.VOLUME_SPIKE, Conviction.MEDIUM
        
        # Low conviction
        if change > 2:
            return SignalType.MOMENTUM, Conviction.LOW
        elif -10 < change < -2:
            return SignalType.MEAN_REVERSION, Conviction.LOW
        
        return SignalType.MOMENTUM, Conviction.LOW
    
    def _calculate_position_size(
        self,
        total_value: float,
        conviction: Conviction,
        metrics: TradingMetrics
    ) -> float:
        """Calculate position size based on conviction and metrics"""
        
        base_size = config.BASE_POSITION_SIZE
        
        # Conviction multiplier
        conviction_mult = {
            Conviction.HIGH: 1.5,
            Conviction.MEDIUM: 1.0,
            Conviction.LOW: 0.6
        }
        
        size = base_size * conviction_mult.get(conviction, 1.0)
        
        # Reduce size after consecutive losses
        if metrics.consecutive_losses >= 3:
            size *= 0.5
        
        # Cap at max position percentage
        max_position = total_value * config.MAX_POSITION_PCT
        size = min(size, max_position)
        
        return max(size, config.MIN_TRADE_SIZE)
    
    def _get_deployed_pct(self, portfolio: Dict) -> float:
        """Calculate percentage of capital deployed in non-stable positions"""
        total_value = portfolio.get("total_value", 0)
        if total_value <= 0:
            return 0.0
        
        holdings = portfolio.get("holdings", {})
        
        # Sum value of non-stablecoin holdings
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if sym != "USDC" and not self._is_stablecoin(sym)
        )
        
        return deployed / total_value
    
    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if symbol is a stablecoin"""
        # Check if in config tokens
        if symbol in config.TOKENS:
            return config.TOKENS[symbol].stable
        
        # Check common stablecoin patterns
        stable_patterns = ["USDC", "USDT", "DAI", "USD", "BUSD", "TUSD", "FRAX"]
        return any(pattern in symbol.upper() for pattern in stable_patterns)
    
    def _get_total_usdc(self, holdings: Dict) -> float:
        """Get total USDC across all chains"""
        total = 0.0
        for symbol, holding in holdings.items():
            if self._is_stablecoin(symbol):
                total += holding.get("value", 0)
        return total