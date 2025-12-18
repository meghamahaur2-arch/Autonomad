"""
Trading Strategy - FIXED: Keep validating until trade succeeds
✅ Tries all available tokens, not just LLM-ranked ones
✅ Won't waste cycles on failed validation
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
    🔥 ELITE HYBRID STRATEGY - VALIDATED UNTIL SUCCESS
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain
        logger.info("📊 Hybrid Trading Strategy initialized")
        logger.info("   ✅ Validates all tokens until one succeeds")
        logger.info("   ✅ No wasted cycles")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Generate trade decision (hybrid approach with exhaustive validation)"""
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
        
        # STEP 3: Get LLM to rank opportunities (if enabled)
        if self.llm_brain and self.llm_brain.enabled and opportunities:
            logger.info("🧠 Getting LLM token rankings...")
            ranked_opportunities = await self.llm_brain.rank_tokens(
                opportunities,
                market_snapshot
            )
            # LLM now returns ALL tokens (LLM-ranked first, then algo scores)
            opportunities_with_scores = [
                (token, llm_score, reason) 
                for token, llm_score, reason in ranked_opportunities
            ]
            
            llm_count = sum(1 for _, _, reason in opportunities_with_scores if "algo" not in reason.lower())
            logger.info(f"📊 LLM ranked {llm_count} tokens, {len(opportunities_with_scores) - llm_count} using algo scores")
        else:
            opportunities_with_scores = [
                (token, token.opportunity_score, "Algorithmic")
                for token in opportunities
            ]
        
        # STEP 4: ✅ NEW: Validate ALL opportunities until one succeeds
        entry_candidate = await self._check_entry_signals_exhaustive(
            portfolio, 
            opportunities_with_scores,
            metrics,
            market_snapshot
        )
        
        return entry_candidate
    
    async def _check_entry_signals_exhaustive(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """
        ✅ FIXED: Validate ALL tokens until one succeeds (no wasted cycles)
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
        
        # ✅ FIXED: Validate ALL opportunities until one succeeds
        existing_symbols = {pos.symbol for pos in positions}
        
        validated_count = 0
        failed_tokens = []
        max_to_validate = min(len(opportunities_with_scores), 20)  # Don't validate more than 20
        
        logger.info(f"🔍 Starting exhaustive validation (up to {max_to_validate} tokens)...")
        
        for token, score, llm_reason in opportunities_with_scores[:max_to_validate]:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols:
                logger.debug(f"⏭️ #{validated_count + 1} Skipping {token.symbol} - already holding")
                continue
            
            # Skip if base symbol is held (avoid duplicates across chains)
            if token.symbol in existing_symbols:
                logger.debug(f"⏭️ #{validated_count + 1} Skipping {token.symbol} - base symbol held")
                continue
            
            # Score threshold
            if score < 3.0:  # Lower threshold to validate more tokens
                logger.debug(f"⏭️ #{validated_count + 1} Skipping {token.symbol} - score {score:.1f} < 3.0")
                continue
            
            # ✅ Start validation for this token
            validated_count += 1
            logger.info(f"🔍 Validating token #{validated_count}: {token.symbol} (Score: {score:.1f})")
            
            # Validate address format
            is_valid, reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            if not is_valid:
                logger.warning(f"   ❌ Address validation failed: {reason}")
                failed_tokens.append(f"{token.symbol} ({reason})")
                token_validator.record_trade_failure(token.address, token.chain)
                continue  # Try next token
            
            # Check if blacklisted
            if token_validator.is_blacklisted(token.address):
                logger.warning(f"   🚫 Blacklisted (3+ failures)")
                failed_tokens.append(f"{token.symbol} (blacklisted)")
                continue  # Try next token
            
            # Pre-validate trading requirements
            trade_validation = self._pre_validate_token_requirements(
                token, 
                portfolio, 
                positions
            )
            
            if not trade_validation["valid"]:
                logger.warning(f"   ❌ Trading validation failed: {trade_validation['reason']}")
                failed_tokens.append(f"{token.symbol} ({trade_validation['reason']})")
                continue  # Try next token
            
            # ✅ ALL VALIDATIONS PASSED
            logger.info(f"   ✅ All validations passed!")
            
            # Determine signal type and conviction
            signal_type, conviction = self._classify_opportunity(token)
            
            # Calculate position size
            position_size = self._calculate_position_size(
                total_value,
                conviction,
                metrics
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.warning(f"   ⚠️ Position too small: ${position_size:.2f}")
                failed_tokens.append(f"{token.symbol} (position_too_small)")
                continue  # Try next token
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            # ✅ Create decision for this token
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
            
            # ✅ LLM confirmation (if enabled)
            if self.llm_brain and self.llm_brain.enabled:
                logger.info("   🧠 Getting LLM trade confirmation...")
                
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
                    logger.warning(f"   ❌ LLM rejected: {reasoning}")
                    failed_tokens.append(f"{token.symbol} (LLM_rejected)")
                    continue  # Try next token (CRITICAL: Don't give up!)
                
                decision.conviction = new_conviction
                decision.reason = f"{decision.reason} | LLM: {reasoning}"
            
            # ✅ SUCCESS! Found valid token
            if failed_tokens:
                logger.info(f"✅ Found valid token after trying {validated_count} (Skipped: {len(failed_tokens)})")
            
            return decision
        
        # ✅ Exhausted all tokens - none passed validation
        logger.warning(f"❌ Validated {validated_count} tokens, all failed")
        
        if failed_tokens:
            # Show first 5 failures
            failures_summary = ", ".join(failed_tokens[:5])
            if len(failed_tokens) > 5:
                failures_summary += f" and {len(failed_tokens) - 5} more"
            
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"🔍 Validated {validated_count} tokens, all failed: {failures_summary}"
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason=f"🔍 No suitable opportunities found (checked {validated_count} tokens)"
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
    
    def _pre_validate_token_requirements(
        self,
        token: DiscoveredToken,
        portfolio: Dict,
        positions: List[Position]
    ) -> Dict:
        """
        Pre-validate token trading requirements
        """
        # Check liquidity
        if token.liquidity_usd < config.MIN_LIQUIDITY_USD:
            return {
                "valid": False,
                "reason": f"low_liquidity (${token.liquidity_usd:,.0f} < ${config.MIN_LIQUIDITY_USD:,.0f})"
            }
        
        # Check volume
        if token.volume_24h < config.MIN_VOLUME_24H_USD:
            return {
                "valid": False,
                "reason": f"low_volume (${token.volume_24h:,.0f} < ${config.MIN_VOLUME_24H_USD:,.0f})"
            }
        
        # Score threshold - lowered to 3.0 so we check more tokens
        if token.opportunity_score < 3.0:
            return {
                "valid": False,
                "reason": f"low_score ({token.opportunity_score:.1f} < 3.0)"
            }
        
        # Check if would exceed max positions
        if len(positions) >= config.MAX_POSITIONS:
            return {
                "valid": False,
                "reason": f"max_positions ({len(positions)}/{config.MAX_POSITIONS})"
            }
        
        # Check available USDC
        holdings = portfolio.get("holdings", {})
        total_usdc = sum(
            h["value"] for s, h in holdings.items()
            if self._is_stablecoin(s)
        )
        
        if total_usdc < config.MIN_TRADE_SIZE:
            return {
                "valid": False,
                "reason": f"insufficient_usdc (${total_usdc:.2f})"
            }
        
        # Check portfolio risk
        total_value = portfolio.get("total_value", 0)
        if total_value > 0:
            deployed = sum(
                h["value"] for s, h in holdings.items()
                if not self._is_stablecoin(s)
            )
            
            # Estimate new deployed after this trade
            estimated_position_size = min(config.BASE_POSITION_SIZE, total_usdc * 0.95)
            new_deployed = deployed + estimated_position_size
            new_deployed_pct = new_deployed / total_value
            
            if new_deployed_pct > config.MAX_PORTFOLIO_RISK:
                return {
                    "valid": False,
                    "reason": f"max_risk ({new_deployed_pct*100:.0f}% > {config.MAX_PORTFOLIO_RISK*100:.0f}%)"
                }
        
        return {"valid": True, "reason": "All checks passed"}
    
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