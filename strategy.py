"""
Trading Strategy - WITH TOKEN VALIDATION FALLBACK
✅ Tries next-ranked token if validation fails
✅ Prevents wasted cycles
"""
from typing import List, Dict, Optional, Tuple
from config import config
from models import (
    TradingAction, TradeDecision, Conviction, SignalType,
    DiscoveredToken, TradingMetrics, Position, MarketSnapshot
)
from logging_manager import get_logger
from token_validator import token_validator

logger = get_logger("Strategy")


class TradingStrategy:
    """
    🔥 ELITE HYBRID STRATEGY WITH FALLBACK
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain
        logger.info("📊 Hybrid Trading Strategy initialized")
        logger.info("   ✅ Token validation fallback enabled")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Generate trade decision (hybrid approach with fallback)"""
        positions = portfolio.get("positions", [])
        
        # STEP 1: Check exit signals first (PRIORITY)
        for position in positions:
            exit_decision = self._check_exit_signals(position, portfolio)
            if exit_decision:
                return exit_decision
        
        # STEP 2: Check if we can add more positions
        if len(positions) >= config.MAX_POSITIONS:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached. Need to exit first."
            )
        
        # STEP 3: Get LLM to rank opportunities
        if self.llm_brain and self.llm_brain.enabled and opportunities:
            logger.info("🧠 Getting LLM token rankings...")
            ranked_opportunities = await self.llm_brain.rank_tokens(
                opportunities,
                market_snapshot
            )
            opportunities_with_scores = [
                (token, llm_score, reason) 
                for token, llm_score, reason in ranked_opportunities
            ]
        else:
            opportunities_with_scores = [
                (token, token.opportunity_score, "Algorithmic")
                for token in opportunities
            ]
        
        # STEP 4: Check for entry WITH FALLBACK MECHANISM
        entry_candidate = await self._check_entry_signals_with_fallback(
            portfolio, 
            opportunities_with_scores,
            metrics,
            market_snapshot
        )
        
        return entry_candidate
    
    async def _check_entry_signals_with_fallback(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """
        ✅ NEW: Check entry signals with fallback to next token if validation fails
        """
        
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        # Calculate deployed capital
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if sym != "USDC" and not self._is_stablecoin(sym)
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
        
        # ✅ NEW: Try opportunities in order until one passes validation
        existing_symbols = {pos.symbol for pos in positions}
        
        validated_count = 0
        failed_tokens = []
        
        for token, score, llm_reason in opportunities_with_scores:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols:
                logger.debug(f"⏭️ Skipping {token.symbol} - already holding")
                continue
            
            # Skip if base symbol is held (avoid duplicates across chains)
            if token.symbol in existing_symbols:
                logger.debug(f"⏭️ Skipping {token.symbol} - base symbol held")
                continue
            
            # Score threshold
            if score < 4.0:
                logger.debug(f"⏭️ Skipping {token.symbol} - score {score:.1f} < 4.0")
                continue
            
            # ✅ CRITICAL: Pre-validate token BEFORE creating decision
            logger.info(f"🔍 Pre-validating token #{validated_count + 1}: {token.symbol}")
            is_valid, reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            validated_count += 1
            
            if not is_valid:
                logger.warning(f"❌ Validation failed: {token.symbol} - {reason}")
                failed_tokens.append(f"{token.symbol} ({reason})")
                
                # Record failure
                token_validator.record_trade_failure(token.address, token.chain)
                
                # Continue to next token (FALLBACK)
                continue
            
            # Check if blacklisted
            if token_validator.is_blacklisted(token.address):
                logger.warning(f"🚫 Skipping blacklisted token: {token.symbol}")
                failed_tokens.append(f"{token.symbol} (blacklisted)")
                continue
            
            logger.info(f"✅ Token validation passed: {token.symbol}")
            
            # Determine signal type and conviction
            signal_type, conviction = self._classify_opportunity(token)
            
            # Calculate position size
            position_size = self._calculate_position_size(
                total_value,
                conviction,
                metrics
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.debug(f"⏭️ Skipping {token.symbol} - position too small")
                continue
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            # ✅ SUCCESS: Create decision for this token
            decision = TradeDecision(
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
                    "price": token.price,
                    "market_cap": token.market_cap
                }
            )
            
            # ✅ STEP 5: LLM confirmation (if enabled)
            if self.llm_brain and self.llm_brain.enabled:
                logger.info("🧠 Getting LLM trade confirmation...")
                
                portfolio_stats = {
                    "positions": len(positions),
                    "deployed_pct": deployed_pct,
                    "win_rate": metrics.win_rate,
                    "consecutive_losses": metrics.consecutive_losses
                }
                
                approved, reasoning, new_conviction = await self.llm_brain.confirm_trade(
                    token,
                    signal_type.value,
                    conviction,
                    market_snapshot,
                    portfolio_stats
                )
                
                if not approved:
                    logger.warning(f"❌ LLM rejected: {token.symbol} - {reasoning}")
                    failed_tokens.append(f"{token.symbol} (LLM rejected)")
                    # Continue to next token (FALLBACK)
                    continue
                
                decision.conviction = new_conviction
                decision.reason = f"{decision.reason} | LLM: {reasoning}"
            
            # ✅ FOUND VALID TOKEN - RETURN IT
            if failed_tokens:
                logger.info(f"ℹ️ Tried {validated_count} tokens, skipped: {', '.join(failed_tokens)}")
            
            return decision
        
        # ✅ NO VALID TOKEN FOUND
        if failed_tokens:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"🔍 Validated {validated_count} tokens, all failed: {', '.join(failed_tokens[:3])}"
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason="🔍 No suitable opportunities found"
        )
    
    def _check_exit_signals(self, position: Position, portfolio: Dict) -> Optional[TradeDecision]:
        """Check exit signals"""
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(position.symbol, {})
        
        metadata = {
            "token_address": position_holding.get("tokenAddress", ""),
            "chain": position_holding.get("chain", "eth"),
            "price": position.current_price,
            "liquidity": 999999,
            "volume_24h": 999999,
            "score": 10.0
        }
        
        # Stop Loss
        if position.should_stop_loss(config.STOP_LOSS_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"🛑 Stop-loss: {position.pnl_pct:.1f}% loss on ${position.value:.2f}",
                metadata=metadata
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
                reason=f"📉 Trailing stop triggered",
                metadata=metadata
            )
        
        # Take Profit
        if position.should_take_profit(config.TAKE_PROFIT_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.5,
                conviction=Conviction.MEDIUM,
                signal_type=SignalType.TAKE_PROFIT,
                reason=f"🎯 Take-profit: {position.pnl_pct:.1f}% gain (partial 50%)",
                metadata=metadata
            )
        
        return None
    
    def _classify_opportunity(self, token: DiscoveredToken) -> tuple:
        """Classify opportunity type"""
        change = token.change_24h_pct
        score = token.opportunity_score
        
        # High conviction
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
        """Calculate position size"""
        base_size = config.BASE_POSITION_SIZE
        
        conviction_mult = {
            Conviction.HIGH: 1.5,
            Conviction.MEDIUM: 1.0,
            Conviction.LOW: 0.6
        }
        
        size = base_size * conviction_mult.get(conviction, 1.0)
        
        # Reduce after losses
        if metrics.consecutive_losses >= 3:
            size *= 0.5
        
        # Cap at max position
        max_position = total_value * config.MAX_POSITION_PCT
        size = min(size, max_position)
        
        return max(size, config.MIN_TRADE_SIZE)
    
    def _get_deployed_pct(self, portfolio: Dict) -> float:
        """Calculate deployed percentage"""
        total_value = portfolio.get("total_value", 0)
        if total_value <= 0:
            return 0.0
        
        holdings = portfolio.get("holdings", {})
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if not self._is_stablecoin(sym)
        )
        
        return deployed / total_value
    
    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if stablecoin (case-insensitive)"""
        symbol_upper = symbol.upper()
        
        if symbol_upper in config.TOKENS:
            return config.TOKENS[symbol_upper].stable
        
        # Check base symbol
        base_symbol = symbol_upper.split('_')[0]
        if base_symbol in config.TOKENS:
            return config.TOKENS[base_symbol].stable
        
        stable_patterns = ["USDC", "USDT", "DAI", "USD", "BUSD", "TUSD", "FRAX"]
        return any(pattern in symbol_upper for pattern in stable_patterns)
    
    def _get_total_usdc(self, holdings: Dict) -> float:
        """Get total USDC (case-insensitive)"""
        return sum(
            holding.get("value", 0)
            for symbol, holding in holdings.items()
            if self._is_stablecoin(symbol)
        )