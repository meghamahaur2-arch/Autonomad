"""
Trading Strategy - BIG TRADES MODE
✅ Aggressively exits positions to free USDC for $10k trades
✅ Rebalances constantly to maintain buying power
✅ Prioritizes token-to-token swaps over holding
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
    🔥 BIG TRADES MODE - Aggressive capital rotation
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain
        logger.info("📊 BIG TRADES Strategy initialized")
        logger.info(f"   💰 Target Position Size: ${config.BASE_POSITION_SIZE:,}")
        logger.info("   ⚡ Aggressive exit mode ENABLED")
        logger.info("   🔄 Continuous rebalancing ENABLED")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Generate trade decision with aggressive capital management"""
        positions = portfolio.get("positions", [])
        holdings = portfolio.get("holdings", {})
        
        # Calculate available USDC
        usdc_available = self._get_total_usdc(holdings)
        total_value = portfolio.get("total_value", 0)
        
        logger.info(f"💰 Portfolio: ${total_value:,.2f} | USDC: ${usdc_available:,.2f} | Positions: {len(positions)}")
        
        # 🔥 AGGRESSIVE CAPITAL MANAGEMENT
        target_usdc_reserve = config.BASE_POSITION_SIZE * 1.5  # Want 1.5x position size in reserve
        
        # STEP 1: Emergency exits (stop losses, etc.)
        for position in positions:
            exit_decision = self._check_exit_signals(position, portfolio)
            if exit_decision:
                logger.info("🚨 Emergency exit signal detected")
                return exit_decision
        
        # STEP 2: 🔥 AGGRESSIVE - Exit positions if USDC too low for target size
        if usdc_available < target_usdc_reserve and positions:
            logger.warning(f"⚡ LOW USDC: ${usdc_available:,.2f} < ${target_usdc_reserve:,.2f}")
            logger.info("🔄 INITIATING CAPITAL ROTATION...")
            
            # Calculate how much we need
            usdc_needed = target_usdc_reserve - usdc_available
            logger.info(f"   Need to free: ${usdc_needed:,.2f}")
            
            # Find positions to exit (prioritize worst performers + smallest positions)
            exit_candidates = self._find_exit_candidates_for_rebalance(
                positions, 
                usdc_needed,
                portfolio
            )
            
            if exit_candidates:
                position, reason = exit_candidates[0]
                logger.warning(f"   🎯 Exiting: {position.symbol} ({reason})")
                
                holdings = portfolio.get("holdings", {})
                position_holding = holdings.get(position.symbol, {})
                
                return TradeDecision(
                    action=TradingAction.SELL,
                    from_token=position.symbol,
                    to_token="USDC",
                    amount_usd=position.value * 0.98,
                    conviction=Conviction.HIGH,
                    signal_type=SignalType.REBALANCE,
                    reason=f"💰 Free capital for $10k trades: {reason} (Need ${usdc_needed:,.0f}, have ${usdc_available:,.0f})",
                    metadata={
                        "token_address": position_holding.get("tokenAddress", ""),
                        "chain": position_holding.get("chain", "eth"),
                        "price": position.current_price,
                        "liquidity": 999999,
                        "volume_24h": 999999,
                        "score": 10.0
                    }
                )
        
        # STEP 3: Token-to-token swaps (even if USDC is adequate)
        if positions and opportunities:
            swap_decision = await self._check_aggressive_swaps(
                portfolio,
                opportunities,
                metrics,
                market_snapshot
            )
            if swap_decision and swap_decision.action != TradingAction.HOLD:
                return swap_decision
        
        # STEP 4: Check if we can add more positions
        if len(positions) >= config.MAX_POSITIONS:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached. Rebalancing needed."
            )
        
        # STEP 5: Check if we have enough USDC for BIG trade
        min_usdc_for_trade = config.BASE_POSITION_SIZE * 0.5  # At least 50% of target
        
        if usdc_available < min_usdc_for_trade:
            logger.warning(f"💸 Cannot execute BIG trade: ${usdc_available:,.2f} < ${min_usdc_for_trade:,.2f}")
            
            if positions:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"💰 Need ${min_usdc_for_trade:,.0f} USDC for trade (have ${usdc_available:,.0f}). Waiting for exits..."
                )
            else:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"💰 Insufficient capital (${usdc_available:,.0f}) and no positions to exit"
                )
        
        # STEP 6: Get LLM rankings
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
        
        # STEP 7: Try USDC-to-token trades (BIG SIZE)
        entry_candidate = await self._check_entry_signals_big_trades(
            portfolio, 
            opportunities_with_scores,
            metrics,
            market_snapshot
        )
        
        return entry_candidate
    
    def _find_exit_candidates_for_rebalance(
        self,
        positions: List[Position],
        usdc_needed: float,
        portfolio: Dict
    ) -> List[Tuple[Position, str]]:
        """
        🔥 Find positions to exit for rebalancing
        Prioritizes: Losers > Small gains > Small positions > Sideways
        """
        candidates = []
        
        for position in positions:
            # Score this position for exit (higher = more likely to exit)
            exit_score = 0
            reason_parts = []
            
            # Factor 1: Performance (biggest weight)
            if position.pnl_pct < -3:
                exit_score += 100
                reason_parts.append(f"losing {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 0:
                exit_score += 50
                reason_parts.append(f"slight loss {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 3:
                exit_score += 30
                reason_parts.append(f"small gain {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 8:
                exit_score += 10
                reason_parts.append(f"moderate gain {position.pnl_pct:.1f}%")
            
            # Factor 2: Position size (prefer exiting smaller positions)
            if position.value < config.BASE_POSITION_SIZE * 0.3:
                exit_score += 40
                reason_parts.append(f"small position ${position.value:.0f}")
            elif position.value < config.BASE_POSITION_SIZE * 0.5:
                exit_score += 20
                reason_parts.append(f"medium position ${position.value:.0f}")
            
            # Factor 3: If this position's value covers what we need
            if position.value >= usdc_needed:
                exit_score += 20
                reason_parts.append("covers USDC need")
            
            reason = ", ".join(reason_parts)
            candidates.append((position, reason, exit_score))
        
        # Sort by exit score (highest first)
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        # Return position + reason (drop score)
        return [(pos, reason) for pos, reason, _ in candidates]
    
    async def _check_aggressive_swaps(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> Optional[TradeDecision]:
        """
        🔥 VERY AGGRESSIVE: Swap even profitable positions if new opportunity is MUCH better
        """
        positions = portfolio.get("positions", [])
        
        if not positions or not opportunities:
            return None
        
        # Rank opportunities
        if self.llm_brain and self.llm_brain.enabled:
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
        
        # Find best opportunity
        best_opportunity = None
        best_score = 0
        
        for token, score, reason in opportunities_with_scores[:10]:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if any(pos.symbol == symbol_key or pos.symbol == token.symbol for pos in positions):
                continue
            
            # Validate token
            is_valid, validation_reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            if not is_valid or token_validator.is_blacklisted(token.address):
                continue
            
            # 🔥 AGGRESSIVE: Accept score >= 8 for swaps
            if score > best_score and score >= 8.0:
                best_opportunity = (token, score, reason)
                best_score = score
        
        if not best_opportunity:
            return None
        
        # Find position to swap - 🔥 VERY AGGRESSIVE
        token, new_score, opp_reason = best_opportunity
        
        swap_candidates = []
        
        for position in positions:
            # Aggressive swap criteria
            swap_score = 0
            swap_reason_parts = []
            
            # ANY loss = candidate
            if position.pnl_pct < 0:
                swap_score += 100
                swap_reason_parts.append(f"losing {position.pnl_pct:.1f}%")
            
            # Small gain + new opportunity is MUCH better
            elif position.pnl_pct < 5 and new_score >= 15:
                swap_score += 80
                swap_reason_parts.append(f"small gain {position.pnl_pct:.1f}% vs score {new_score:.1f}")
            
            # Moderate gain but new opportunity is exceptional
            elif position.pnl_pct < 10 and new_score >= 20:
                swap_score += 60
                swap_reason_parts.append(f"moderate gain {position.pnl_pct:.1f}% vs exceptional score {new_score:.1f}")
            
            # Sideways/stale
            elif 0 <= position.pnl_pct < 2 and new_score >= 12:
                swap_score += 50
                swap_reason_parts.append(f"sideways {position.pnl_pct:.1f}%")
            
            if swap_score > 0:
                reason = ", ".join(swap_reason_parts)
                swap_candidates.append((position, reason, swap_score))
        
        if not swap_candidates:
            return None
        
        # Get best swap candidate
        swap_candidates.sort(key=lambda x: x[2], reverse=True)
        position, reason, _ = swap_candidates[0]
        
        logger.info(f"🔄 SWAP IDENTIFIED!")
        logger.info(f"   OUT: {position.symbol} ({reason})")
        logger.info(f"   IN:  {token.symbol} (Score: {new_score:.1f})")
        
        # Create sell decision
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(position.symbol, {})
        
        return TradeDecision(
            action=TradingAction.SELL,
            from_token=position.symbol,
            to_token="USDC",
            amount_usd=position.value * 0.98,
            conviction=Conviction.HIGH,
            signal_type=SignalType.REBALANCE,
            reason=f"🔄 Swap to better opportunity: {position.symbol}→{token.symbol} ({reason})",
            metadata={
                "token_address": position_holding.get("tokenAddress", ""),
                "chain": position_holding.get("chain", "eth"),
                "price": position.current_price,
                "liquidity": 999999,
                "volume_24h": 999999,
                "score": 10.0,
                "swap_target": token.symbol,
                "swap_target_score": new_score
            }
        )
    
    async def _check_entry_signals_big_trades(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """
        Entry signals with BIG TRADE sizing
        """
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        # Calculate USDC
        usdc_value = self._get_total_usdc(holdings)
        
        logger.info(f"💵 Available for trade: ${usdc_value:,.2f}")
        
        # Check deployment
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if not self._is_stablecoin(sym)
        )
        deployed_pct = deployed / total_value if total_value > 0 else 0
        
        if deployed_pct >= config.MAX_PORTFOLIO_RISK:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"⚠️ Max risk deployed ({deployed_pct*100:.0f}%)"
            )
        
        # No opportunities
        if not opportunities_with_scores:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="🔭 No opportunities meeting criteria"
            )
        
        # Validate opportunities
        existing_symbols = {pos.symbol for pos in positions}
        
        validated_count = 0
        failed_tokens = []
        
        logger.info(f"🔍 Validating opportunities for BIG trades...")
        
        for token, score, llm_reason in opportunities_with_scores[:20]:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols or token.symbol in existing_symbols:
                continue
            
            # Score threshold
            if score < 5.0:  # Higher bar for big trades
                continue
            
            validated_count += 1
            logger.info(f"🔍 #{validated_count}: {token.symbol} (Score: {score:.1f})")
            
            # Validate address
            is_valid, reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            if not is_valid:
                logger.warning(f"   ❌ Invalid: {reason}")
                failed_tokens.append(f"{token.symbol} ({reason})")
                token_validator.record_trade_failure(token.address, token.chain)
                continue
            
            if token_validator.is_blacklisted(token.address):
                logger.warning(f"   🚫 Blacklisted")
                failed_tokens.append(f"{token.symbol} (blacklisted)")
                continue
            
            # Pre-validate
            trade_validation = self._pre_validate_token_requirements(
                token, 
                portfolio, 
                positions
            )
            
            if not trade_validation["valid"]:
                logger.warning(f"   ❌ {trade_validation['reason']}")
                failed_tokens.append(f"{token.symbol} ({trade_validation['reason']})")
                continue
            
            logger.info(f"   ✅ Validated!")
            
            # Classify opportunity
            signal_type, conviction = self._classify_opportunity(token)
            
            # 🔥 BIG TRADE SIZING
            position_size = self._calculate_big_trade_size(
                usdc_value,
                total_value,
                conviction,
                metrics,
                score
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.warning(f"   ⚠️ Position too small: ${position_size:.2f}")
                failed_tokens.append(f"{token.symbol} (insufficient_capital)")
                continue
            
            logger.info(f"   💰 Trade size: ${position_size:,.2f}")
            
            # Create decision
            decision = TradeDecision(
                action=TradingAction.BUY,
                from_token="USDC",
                to_token=symbol_key,
                amount_usd=position_size,
                conviction=conviction,
                signal_type=signal_type,
                reason=f"🎯 BIG TRADE: {signal_type.value} | Score: {score:.1f} | {llm_reason}",
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
            
            # LLM confirmation
            if self.llm_brain and self.llm_brain.enabled:
                logger.info("   🧠 LLM confirmation...")
                
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
                    continue
                
                decision.conviction = new_conviction
                decision.reason = f"{decision.reason} | LLM: {reasoning}"
            
            # Success!
            return decision
        
        # Exhausted all tokens
        logger.warning(f"❌ No valid trades after checking {validated_count} tokens")
        
        if failed_tokens:
            failures_summary = ", ".join(failed_tokens[:5])
            if len(failed_tokens) > 5:
                failures_summary += f" and {len(failed_tokens) - 5} more"
            
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"🔍 No valid trades: {failures_summary}"
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason=f"🔍 No suitable opportunities (checked {validated_count})"
        )
    
    def _calculate_big_trade_size(
        self,
        usdc_available: float,
        total_value: float,
        conviction: Conviction,
        metrics: TradingMetrics,
        opportunity_score: float
    ) -> float:
        """
        🔥 BIG TRADE SIZING
        Uses as much USDC as possible (up to BASE_POSITION_SIZE)
        """
        # Base size = configured target
        base_size = config.BASE_POSITION_SIZE
        
        # But can't exceed available USDC (leave 5% buffer)
        max_available = usdc_available * 0.95
        
        # Start with smaller of the two
        size = min(base_size, max_available)
        
        # Conviction multiplier (scale up/down)
        conviction_mult = {
            Conviction.HIGH: 1.0,      # Full size
            Conviction.MEDIUM: 0.75,   # 75%
            Conviction.LOW: 0.5        # 50%
        }
        size *= conviction_mult.get(conviction, 0.75)
        
        # Opportunity score boost (if exceptional)
        if opportunity_score >= 20:
            size *= 1.2  # 20% boost for amazing opportunities
        elif opportunity_score >= 15:
            size *= 1.1  # 10% boost
        
        # Reduce after consecutive losses
        if metrics.consecutive_losses >= 3:
            size *= 0.6  # 40% reduction
            logger.warning(f"   ⚠️ Reducing size due to {metrics.consecutive_losses} losses")
        
        # Cap at portfolio limit
        if total_value > 0:
            max_position = total_value * config.MAX_POSITION_PCT
            size = min(size, max_position)
        
        # Final bounds check
        size = min(size, max_available)
        size = max(size, config.MIN_TRADE_SIZE)
        
        return size
    
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
                reason=f"🛑 Stop-loss: {position.pnl_pct:.1f}% loss",
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
                reason=f"📉 Trailing stop",
                metadata=metadata
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
                reason=f"🎯 Take-profit: {position.pnl_pct:.1f}% (partial 50%)",
                metadata=metadata
            )
        
        return None
    
    def _pre_validate_token_requirements(
        self,
        token: DiscoveredToken,
        portfolio: Dict,
        positions: List[Position]
    ) -> Dict:
        """Pre-validate token"""
        
        if token.liquidity_usd < config.MIN_LIQUIDITY_USD:
            return {"valid": False, "reason": f"low_liquidity"}
        
        if token.volume_24h < config.MIN_VOLUME_24H_USD:
            return {"valid": False, "reason": f"low_volume"}
        
        if token.opportunity_score < 5.0:  # Higher bar for big trades
            return {"valid": False, "reason": f"low_score"}
        
        if len(positions) >= config.MAX_POSITIONS:
            return {"valid": False, "reason": f"max_positions"}
        
        return {"valid": True, "reason": "OK"}
    
    def _classify_opportunity(self, token: DiscoveredToken) -> tuple:
        """Classify opportunity"""
        change = token.change_24h_pct
        score = token.opportunity_score
        
        if score > 15:
            if change > 10:
                return SignalType.BREAKOUT, Conviction.HIGH
            elif change < -10:
                return SignalType.MEAN_REVERSION, Conviction.HIGH
            elif token.volume_24h > 5_000_000:
                return SignalType.VOLUME_SPIKE, Conviction.HIGH
        
        if score > 10:
            if change > 5:
                return SignalType.MOMENTUM, Conviction.MEDIUM
            elif -15 < change < -5:
                return SignalType.MEAN_REVERSION, Conviction.MEDIUM
        
        return SignalType.MOMENTUM, Conviction.LOW
    
    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if stablecoin"""
        symbol_upper = symbol.upper()
        
        if symbol_upper in config.TOKENS:
            return config.TOKENS[symbol_upper].stable
        
        base_symbol = symbol_upper.split('_')[0]
        if base_symbol in config.TOKENS:
            return config.TOKENS[base_symbol].stable
        
        stable_patterns = ["USDC", "USDT", "DAI", "USD", "BUSD", "TUSD", "FRAX"]
        return any(pattern in symbol_upper for pattern in stable_patterns)
    
    def _get_total_usdc(self, holdings: Dict) -> float:
        """Get total USDC"""
        return sum(
            holding.get("value", 0)
            for symbol, holding in holdings.items()
            if self._is_stablecoin(symbol)
        )