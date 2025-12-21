"""
Trading Strategy - FIXED: Aggressive Token-to-Token Swaps
✅ Prioritizes swaps when USDC is low
✅ Doesn't require USDC for rebalancing
✅ More aggressive swap thresholds
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
    🔥 ELITE HYBRID STRATEGY - SWAP-FIRST MODE
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain
        logger.info("📊 Hybrid Trading Strategy initialized")
        logger.info("   ✅ Token-to-Token swap priority enabled")
        logger.info("   ✅ Low USDC triggers aggressive swaps")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Generate trade decision (swap-first approach)"""
        positions = portfolio.get("positions", [])
        holdings = portfolio.get("holdings", {})
        
        # Calculate available USDC
        usdc_available = self._get_total_usdc(holdings)
        total_value = portfolio.get("total_value", 0)
        
        logger.info(f"💰 Portfolio: ${total_value:,.2f} | USDC: ${usdc_available:,.2f} | Positions: {len(positions)}")
        
        # STEP 1: Check exit signals (ALWAYS FIRST)
        for position in positions:
            exit_decision = self._check_exit_signals(position, portfolio)
            if exit_decision:
                return exit_decision
        
        # STEP 2: ✅ CRITICAL - Check if USDC is low (< BASE_POSITION_SIZE)
        usdc_is_low = usdc_available < config.BASE_POSITION_SIZE
        
        if usdc_is_low and positions:
            logger.warning(f"⚠️ Low USDC (${usdc_available:.2f} < ${config.BASE_POSITION_SIZE:.2f})")
            logger.info("🔄 PRIORITIZING TOKEN SWAPS...")
            
            # Try token swap - MORE AGGRESSIVE
            swap_decision = await self._check_token_swap_opportunities_aggressive(
                portfolio,
                opportunities,
                metrics,
                market_snapshot,
                force_swap=True  # ✅ Force swap when USDC low
            )
            
            if swap_decision and swap_decision.action != TradingAction.HOLD:
                return swap_decision
            
            # If no swap found but USDC is critically low, exit worst position
            if usdc_available < config.MIN_TRADE_SIZE * 2:
                logger.warning(f"💸 USDC critically low (${usdc_available:.2f})")
                worst_exit = self._force_exit_worst_position(portfolio)
                if worst_exit:
                    return worst_exit
        
        # STEP 3: Try normal swaps (even with sufficient USDC)
        if positions and opportunities:
            swap_decision = await self._check_token_swap_opportunities_aggressive(
                portfolio,
                opportunities,
                metrics,
                market_snapshot,
                force_swap=False
            )
            if swap_decision and swap_decision.action != TradingAction.HOLD:
                return swap_decision
        
        # STEP 4: Check if we can add more positions
        if len(positions) >= config.MAX_POSITIONS:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached. Need to exit first."
            )
        
        # STEP 5: Only try USDC trades if we have enough
        if usdc_available < config.MIN_TRADE_SIZE:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"💰 Insufficient USDC (${usdc_available:.2f}). Waiting for exits or market opportunities."
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
            
            llm_count = sum(1 for _, _, reason in opportunities_with_scores if "algo" not in reason.lower())
            logger.info(f"📊 LLM ranked {llm_count} tokens, {len(opportunities_with_scores) - llm_count} using algo scores")
        else:
            opportunities_with_scores = [
                (token, token.opportunity_score, "Algorithmic")
                for token in opportunities
            ]
        
        # STEP 7: Try USDC-to-token trades
        entry_candidate = await self._check_entry_signals_exhaustive(
            portfolio, 
            opportunities_with_scores,
            metrics,
            market_snapshot
        )
        
        return entry_candidate
    
    async def _check_token_swap_opportunities_aggressive(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot,
        force_swap: bool = False
    ) -> Optional[TradeDecision]:
        """
        ✅ AGGRESSIVE: Swap tokens even with mild losses when USDC is low
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
        
        logger.info(f"🔍 Evaluating {len(opportunities_with_scores)} opportunities for swaps...")
        
        for token, score, reason in opportunities_with_scores[:15]:  # Check more tokens
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if any(pos.symbol == symbol_key or pos.symbol == token.symbol for pos in positions):
                logger.debug(f"   ⏭️ {token.symbol}: Already holding")
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
                logger.debug(f"   ❌ {token.symbol}: Invalid ({validation_reason})")
                continue
            
            # ✅ LOWERED THRESHOLD: Accept score >= 6 (was 8)
            min_score = 5.0 if force_swap else 7.0
            
            if score > best_score and score >= min_score:
                best_opportunity = (token, score, reason)
                best_score = score
                logger.info(f"   ✅ {token.symbol}: Score {score:.1f} - New best candidate")
        
        if not best_opportunity:
            logger.warning("🔍 No valid swap opportunities found")
            return None
        
        # Find position to swap out - ✅ MORE AGGRESSIVE CRITERIA
        swap_candidate = self._find_swap_candidate_aggressive(
            positions, 
            best_opportunity,
            force_swap
        )
        
        if not swap_candidate:
            logger.debug("💎 All positions look good, no swap needed")
            return None
        
        position, reason = swap_candidate
        token, score, opp_reason = best_opportunity
        
        logger.info(f"🔄 SWAP OPPORTUNITY IDENTIFIED!")
        logger.info(f"   OUT: {position.symbol} (PnL: {position.pnl_pct:+.1f}%, Reason: {reason})")
        logger.info(f"   IN:  {token.symbol} (Score: {score:.1f}, {opp_reason})")
        
        # Create sell decision (first leg of swap)
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(position.symbol, {})
        
        return TradeDecision(
            action=TradingAction.SELL,
            from_token=position.symbol,
            to_token="USDC",
            amount_usd=position.value * 0.98,  # 98% to account for slippage
            conviction=Conviction.HIGH if force_swap else Conviction.MEDIUM,
            signal_type=SignalType.REBALANCE,
            reason=f"🔄 Swap: {position.symbol}→{token.symbol} ({reason})",
            metadata={
                "token_address": position_holding.get("tokenAddress", ""),
                "chain": position_holding.get("chain", "eth"),
                "price": position.current_price,
                "liquidity": 999999,
                "volume_24h": 999999,
                "score": 10.0,
                "swap_target": token.symbol,
                "swap_target_address": token.address,
                "swap_target_chain": token.chain,
                "swap_target_score": score
            }
        )
    
    def _find_swap_candidate_aggressive(
        self,
        positions: List[Position],
        best_opportunity: Tuple,
        force_swap: bool
    ) -> Optional[Tuple[Position, str]]:
        """
        ✅ AGGRESSIVE: Find position to swap with looser criteria
        """
        token, new_score, _ = best_opportunity
        
        candidates = []
        
        for position in positions:
            # Criteria 1: Losing positions (any loss if forced)
            if force_swap and position.pnl_pct < 0:
                candidates.append((position, f"loss_{position.pnl_pct:.1f}%", position.pnl_pct))
                continue
            
            # Criteria 2: Losing > 3% (normal mode)
            if position.pnl_pct < -3:
                candidates.append((position, f"loss_{position.pnl_pct:.1f}%", position.pnl_pct))
                continue
            
            # Criteria 3: Small gains (< 5%) when new opportunity is much better
            if position.pnl_pct < 5 and new_score >= 12:
                candidates.append((position, f"small_gain_{position.pnl_pct:.1f}%_vs_score_{new_score:.1f}", position.pnl_pct))
                continue
            
            # Criteria 4: Sideways (0-2%) for a while (assume stale)
            if 0 <= position.pnl_pct < 2 and new_score >= 10:
                candidates.append((position, f"sideways_{position.pnl_pct:.1f}%", position.pnl_pct))
                continue
        
        if not candidates:
            return None
        
        # Sort by PnL (worst first)
        candidates.sort(key=lambda x: x[2])
        
        worst_position, reason, _ = candidates[0]
        return (worst_position, reason)
    
    def _force_exit_worst_position(self, portfolio: Dict) -> Optional[TradeDecision]:
        """
        ✅ NEW: Force exit worst position when USDC is critically low
        """
        positions = portfolio.get("positions", [])
        
        if not positions:
            return None
        
        # Find worst performer
        worst = min(positions, key=lambda p: p.pnl_pct)
        
        logger.warning(f"💸 FORCED EXIT: {worst.symbol} (PnL: {worst.pnl_pct:+.1f}%)")
        
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(worst.symbol, {})
        
        return TradeDecision(
            action=TradingAction.SELL,
            from_token=worst.symbol,
            to_token="USDC",
            amount_usd=worst.value * 0.98,
            conviction=Conviction.HIGH,
            signal_type=SignalType.REBALANCE,
            reason=f"💸 Emergency exit: Need USDC (PnL: {worst.pnl_pct:+.1f}%)",
            metadata={
                "token_address": position_holding.get("tokenAddress", ""),
                "chain": position_holding.get("chain", "eth"),
                "price": worst.current_price,
                "liquidity": 999999,
                "volume_24h": 999999,
                "score": 10.0
            }
        )
    
    async def _check_entry_signals_exhaustive(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """
        Validate ALL tokens until one succeeds (USDC-based trades)
        ✅ Uses flexible position sizing
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
        
        if usdc_value < config.MIN_TRADE_SIZE:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"💰 Insufficient USDC (${usdc_value:.0f})"
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
        max_to_validate = min(len(opportunities_with_scores), 20)
        
        logger.info(f"🔍 Starting exhaustive validation (up to {max_to_validate} tokens)...")
        logger.info(f"   Available USDC: ${usdc_value:,.2f}")
        
        for token, score, llm_reason in opportunities_with_scores[:max_to_validate]:
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols or token.symbol in existing_symbols:
                continue
            
            # Score threshold
            if score < 3.0:
                continue
            
            validated_count += 1
            logger.info(f"🔍 Validating token #{validated_count}: {token.symbol} (Score: {score:.1f})")
            
            # Validate address
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
                continue
            
            if token_validator.is_blacklisted(token.address):
                logger.warning(f"   🚫 Blacklisted")
                failed_tokens.append(f"{token.symbol} (blacklisted)")
                continue
            
            # Pre-validate trading requirements
            trade_validation = self._pre_validate_token_requirements(
                token, 
                portfolio, 
                positions
            )
            
            if not trade_validation["valid"]:
                logger.warning(f"   ❌ Trading validation failed: {trade_validation['reason']}")
                failed_tokens.append(f"{token.symbol} ({trade_validation['reason']})")
                continue
            
            logger.info(f"   ✅ All validations passed!")
            
            # Classify opportunity
            signal_type, conviction = self._classify_opportunity(token)
            
            # ✅ Calculate position size (flexible based on available USDC)
            position_size = self._calculate_position_size_flexible(
                usdc_value,
                total_value,
                conviction,
                metrics
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.warning(f"   ⚠️ Position too small: ${position_size:.2f}")
                failed_tokens.append(f"{token.symbol} (position_too_small)")
                continue
            
            # Create decision
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
            
            # LLM confirmation
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
                    continue
                
                decision.conviction = new_conviction
                decision.reason = f"{decision.reason} | LLM: {reasoning}"
            
            # Success!
            if failed_tokens:
                logger.info(f"✅ Found valid token after trying {validated_count} (Skipped: {len(failed_tokens)})")
            
            return decision
        
        # Exhausted all tokens
        logger.warning(f"❌ Validated {validated_count} tokens, all failed")
        
        if failed_tokens:
            failures_summary = ", ".join(failed_tokens[:5])
            if len(failed_tokens) > 5:
                failures_summary += f" and {len(failed_tokens) - 5} more"
            
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"🔍 No valid trades (tried {validated_count}): {failures_summary}"
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason=f"🔍 No suitable opportunities (checked {validated_count} tokens)"
        )
    
    def _calculate_position_size_flexible(
        self,
        usdc_available: float,
        total_value: float,
        conviction: Conviction,
        metrics: TradingMetrics
    ) -> float:
        """
        ✅ FLEXIBLE: Use available USDC, not fixed BASE_POSITION_SIZE
        """
        # Use 70% of available USDC (conservative)
        max_usable = usdc_available * 0.70
        
        # Apply conviction multiplier
        conviction_mult = {
            Conviction.HIGH: 1.0,    # 70% of available
            Conviction.MEDIUM: 0.75, # 52.5% of available
            Conviction.LOW: 0.5      # 35% of available
        }
        
        size = max_usable * conviction_mult.get(conviction, 0.75)
        
        # Reduce after losses
        if metrics.consecutive_losses >= 3:
            size *= 0.5
        
        # Cap at max position percentage of total portfolio
        if total_value > 0:
            max_position = total_value * config.MAX_POSITION_PCT
            size = min(size, max_position)
        
        # Ensure minimum
        return max(size, config.MIN_TRADE_SIZE)
    
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
        """Pre-validate token trading requirements"""
        
        if token.liquidity_usd < config.MIN_LIQUIDITY_USD:
            return {
                "valid": False,
                "reason": f"low_liquidity (${token.liquidity_usd:,.0f} < ${config.MIN_LIQUIDITY_USD:,.0f})"
            }
        
        if token.volume_24h < config.MIN_VOLUME_24H_USD:
            return {
                "valid": False,
                "reason": f"low_volume (${token.volume_24h:,.0f} < ${config.MIN_VOLUME_24H_USD:,.0f})"
            }
        
        if token.opportunity_score < 3.0:
            return {
                "valid": False,
                "reason": f"low_score ({token.opportunity_score:.1f} < 3.0)"
            }
        
        if len(positions) >= config.MAX_POSITIONS:
            return {
                "valid": False,
                "reason": f"max_positions ({len(positions)}/{config.MAX_POSITIONS})"
            }
        
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
        
        total_value = portfolio.get("total_value", 0)
        if total_value > 0:
            deployed = sum(
                h["value"] for s, h in holdings.items()
                if not self._is_stablecoin(s)
            )
            
            # Use 70% of available USDC for estimation
            estimated_position_size = min(total_usdc * 0.70, config.BASE_POSITION_SIZE)
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
            elif token.volume_24h > 2_000_000:
                return SignalType.VOLUME_SPIKE, Conviction.MEDIUM
        
        if change > 2:
            return SignalType.MOMENTUM, Conviction.LOW
        elif -10 < change < -2:
            return SignalType.MEAN_REVERSION, Conviction.LOW
        
        return SignalType.MOMENTUM, Conviction.LOW
    
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