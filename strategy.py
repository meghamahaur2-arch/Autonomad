"""
Trading Strategy - PREDICTIVE BIG TRADES MODE
✅ Integrates predictive signals from scanner
✅ Smart entry timing (momentum confirmation)
✅ Volume-price divergence detection
✅ Adaptive thresholds based on market conditions
✅ Time-based trading filters
✅ Multi-signal confirmation for entries
"""
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from config import config
from models import (
    TradingAction, TradeDecision, Conviction, SignalType,
    DiscoveredToken, TradingMetrics, Position, MarketSnapshot
)
from logging_manager import get_logger
from token_validator import token_validator

logger = get_logger("Strategy")


# ═══════════════════════════════════════════════════════════════════
# 🆕 NEW: SIGNAL STRENGTH WEIGHTS
# ═══════════════════════════════════════════════════════════════════

PREDICTIVE_SIGNAL_WEIGHTS = {
    "whale_buy": 3.0,        # Highest weight - smart money
    "buy_pressure": 2.5,     # Strong buy/sell imbalance
    "volume_spike": 2.0,     # Volume leads price
    "new_launch": 1.8,       # Fresh tokens
    "new_liquidity": 1.5,    # Fresh liquidity
    "social_buzz": 1.0,      # Social signals (less reliable)
}

ENTRY_CONFIRMATION_REQUIREMENTS = {
    Conviction.HIGH: 1,      # 1 signal needed
    Conviction.MEDIUM: 2,    # 2 signals needed
    Conviction.LOW: 3,       # 3 signals needed
}


class TradingStrategy:
    """
    🔮 PREDICTIVE BIG TRADES MODE
    - Uses predictive signals for EARLY entries
    - Confirms momentum before buying
    - Avoids "already pumped" tokens
    - Adaptive position sizing
    """
    
    def __init__(self, llm_brain=None):
        self.llm_brain = llm_brain
        
        # 🆕 NEW: Market state tracking
        self._market_state = "neutral"  # bullish, bearish, neutral
        self._last_market_update = None
        self._recent_trades: List[Dict] = []  # Track recent trade performance
        self._entry_cooldowns: Dict[str, datetime] = {}  # Prevent rapid re-entries
        
        # 🆕 NEW: Adaptive thresholds
        self._dynamic_score_threshold = 5.0
        self._dynamic_swap_threshold = 8.0
        
        logger.info("📊 PREDICTIVE BIG TRADES Strategy initialized")
        logger.info(f"   💰 Target Position Size: ${config.BASE_POSITION_SIZE:,}")
        logger.info("   🔮 Predictive signal integration: ENABLED")
        logger.info("   ⚡ Smart entry timing: ENABLED")
        logger.info("   🔄 Adaptive thresholds: ENABLED")
    
    async def generate_decision(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Generate trade decision with PREDICTIVE signals"""
        positions = portfolio.get("positions", [])
        holdings = portfolio.get("holdings", {})
        
        # Calculate available USDC
        usdc_available = self._get_total_usdc(holdings)
        total_value = portfolio.get("total_value", 0)
        
        logger.info(f"💰 Portfolio: ${total_value:,.2f} | USDC: ${usdc_available:,.2f} | Positions: {len(positions)}")
        
        # 🆕 NEW: Update market state and adaptive thresholds
        self._update_market_state(market_snapshot, metrics)
        self._update_adaptive_thresholds(metrics)
        
        # 🆕 NEW: Check if good trading time
        if not self._is_good_trading_time():
            logger.info("⏰ Outside optimal trading hours - being more conservative")
        
        target_usdc_reserve = config.BASE_POSITION_SIZE * 1.5
        
        # ═══════════════════════════════════════════════════════════
        # STEP 1: Emergency exits (stop losses, trailing stops)
        # ═══════════════════════════════════════════════════════════
        for position in positions:
            exit_decision = self._check_exit_signals(position, portfolio, market_snapshot)
            if exit_decision:
                logger.info("🚨 Emergency exit signal detected")
                return exit_decision
        
        # ═══════════════════════════════════════════════════════════
        # STEP 2: 🆕 SMART EXIT - Check for momentum reversal
        # ═══════════════════════════════════════════════════════════
        for position in positions:
            reversal_exit = self._check_momentum_reversal_exit(position, portfolio, opportunities)
            if reversal_exit:
                logger.warning("📉 Momentum reversal detected - exiting position")
                return reversal_exit
        
        # ═══════════════════════════════════════════════════════════
        # STEP 3: Capital rotation if USDC too low
        # ═══════════════════════════════════════════════════════════
        if usdc_available < target_usdc_reserve and positions:
            logger.warning(f"⚡ LOW USDC: ${usdc_available:,.2f} < ${target_usdc_reserve:,.2f}")
            
            # 🆕 NEW: Only rotate if we have good opportunities waiting
            has_good_opportunities = any(
                self._calculate_predictive_score(token) >= self._dynamic_score_threshold
                for token in opportunities[:10]
            )
            
            if has_good_opportunities:
                logger.info("🔄 Good opportunities available - initiating capital rotation...")
                
                usdc_needed = target_usdc_reserve - usdc_available
                
                exit_candidates = self._find_exit_candidates_for_rebalance(
                    positions, 
                    usdc_needed,
                    portfolio,
                    opportunities  # 🆕 Pass opportunities for comparison
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
                        reason=f"💰 Capital rotation: {reason}",
                        metadata={
                            "token_address": position_holding.get("tokenAddress", ""),
                            "chain": position_holding.get("chain", "eth"),
                            "price": position.current_price,
                            "liquidity": 999999,
                            "volume_24h": 999999,
                            "score": 10.0
                        }
                    )
            else:
                logger.info("   ⏸️ No compelling opportunities - holding positions")
        
        # ═══════════════════════════════════════════════════════════
        # STEP 4: 🆕 SMART SWAPS - Only if new opportunity is SIGNIFICANTLY better
        # ═══════════════════════════════════════════════════════════
        if positions and opportunities:
            swap_decision = await self._check_smart_swaps(
                portfolio,
                opportunities,
                metrics,
                market_snapshot
            )
            if swap_decision and swap_decision.action != TradingAction.HOLD:
                return swap_decision
        
        # ═══════════════════════════════════════════════════════════
        # STEP 5: Position limit check
        # ═══════════════════════════════════════════════════════════
        if len(positions) >= config.MAX_POSITIONS:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached"
            )
        
        # ═══════════════════════════════════════════════════════════
        # STEP 6: Capital check for new trades
        # ═══════════════════════════════════════════════════════════
        min_usdc_for_trade = config.BASE_POSITION_SIZE * 0.5
        
        if usdc_available < min_usdc_for_trade:
            if positions:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"💰 Need ${min_usdc_for_trade:,.0f} USDC (have ${usdc_available:,.0f})"
                )
            else:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"💰 Insufficient capital"
                )
        
        # ═══════════════════════════════════════════════════════════
        # STEP 7: 🆕 PREDICTIVE ENTRY SIGNALS
        # ═══════════════════════════════════════════════════════════
        
        # Score opportunities with predictive signals
        scored_opportunities = self._score_opportunities_predictive(opportunities)
        
        # Get LLM rankings if available
        if self.llm_brain and self.llm_brain.enabled and scored_opportunities:
            logger.info("🧠 Getting LLM token rankings...")
            ranked_opportunities = await self.llm_brain.rank_tokens(
                [token for token, _, _ in scored_opportunities],
                market_snapshot
            )
            
            # Merge LLM scores with predictive scores
            opportunities_with_scores = self._merge_scores(
                scored_opportunities,
                ranked_opportunities
            )
        else:
            opportunities_with_scores = scored_opportunities
        
        # ═══════════════════════════════════════════════════════════
        # STEP 8: Execute entry with confirmation
        # ═══════════════════════════════════════════════════════════
        entry_candidate = await self._check_entry_signals_predictive(
            portfolio, 
            opportunities_with_scores,
            metrics,
            market_snapshot
        )
        
        return entry_candidate
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: PREDICTIVE SCORING METHODS
    # ═══════════════════════════════════════════════════════════════
    
    def _get_chains_with_usdc(self, holdings: Dict) -> Set[str]:
        """
        🆕 NEW: Get list of chains where we have USDC
        Returns set of normalized chain names
        """
        chains_with_usdc = set()
        
        # Chain name normalization map
        chain_map = {
            "eth": "ethereum",
            "ethereum": "ethereum",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "base": "base",
            "optimism": "optimism",
            "bsc": "bsc",
            "solana": "solana",
            "svm": "solana"
        }
        
        for symbol, holding in holdings.items():
            if not holding.get("is_stablecoin", False):
                continue
            
            value = holding.get("value", 0)
            if value < 10:  # Need at least $10 to trade
                continue
            
            chain = holding.get("chain", "").lower()
            chain_normalized = chain_map.get(chain, chain)
            chains_with_usdc.add(chain_normalized)
        
        return chains_with_usdc



    def _calculate_predictive_score(self, token: DiscoveredToken) -> float:
        """
        🆕 Calculate score based on PREDICTIVE signals
        """
        base_score = token.opportunity_score
        predictive_bonus = 0.0
        
        # Check for predictive signals
        if hasattr(token, 'predictive_signals') and token.predictive_signals:
            for signal in token.predictive_signals:
                weight = PREDICTIVE_SIGNAL_WEIGHTS.get(signal.signal_type, 1.0)
                predictive_bonus += signal.strength * weight
        
        # 🆕 PENALTY for "already pumped" tokens
        if token.change_24h_pct > 50:
            base_score *= 0.3  # 70% penalty
            logger.debug(f"   ⚠️ {token.symbol}: Heavy penalty (already +{token.change_24h_pct:.0f}%)")
        elif token.change_24h_pct > 30:
            base_score *= 0.5  # 50% penalty
        elif token.change_24h_pct > 20:
            base_score *= 0.7  # 30% penalty
        elif token.change_24h_pct > 10:
            base_score *= 0.85  # 15% penalty
        
        # 🆕 BONUS for tokens that haven't moved much (early entry)
        if -5 < token.change_24h_pct < 5:
            predictive_bonus *= 1.3  # 30% bonus for flat tokens with signals
        
        return base_score + predictive_bonus
    
    def _score_opportunities_predictive(
        self, 
        opportunities: List[DiscoveredToken]
    ) -> List[Tuple[DiscoveredToken, float, str]]:
        """
        🆕 Score all opportunities with predictive weighting
        """
        scored = []
        
        for token in opportunities:
            score = self._calculate_predictive_score(token)
            
            # Build reason string
            reasons = []
            
            if hasattr(token, 'predictive_signals') and token.predictive_signals:
                signal_types = [s.signal_type for s in token.predictive_signals]
                reasons.append(f"Signals: {', '.join(signal_types)}")
            
            if token.change_24h_pct < 5:
                reasons.append("Early entry")
            elif token.change_24h_pct > 20:
                reasons.append("⚠️ Already moved")
            
            reason = " | ".join(reasons) if reasons else "Algorithmic"
            
            scored.append((token, score, reason))
        
        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)
        
        return scored
    
    def _merge_scores(
        self,
        predictive_scores: List[Tuple[DiscoveredToken, float, str]],
        llm_scores: List[Tuple[DiscoveredToken, float, str]]
    ) -> List[Tuple[DiscoveredToken, float, str]]:
        """
        🆕 Merge predictive and LLM scores
        Predictive: 60% weight, LLM: 40% weight
        """
        llm_dict = {token.address: (score, reason) for token, score, reason in llm_scores}
        
        merged = []
        for token, pred_score, pred_reason in predictive_scores:
            if token.address in llm_dict:
                llm_score, llm_reason = llm_dict[token.address]
                # Weighted average
                final_score = (pred_score * 0.6) + (llm_score * 0.4)
                final_reason = f"{pred_reason} | LLM: {llm_reason}"
            else:
                final_score = pred_score
                final_reason = pred_reason
            
            merged.append((token, final_score, final_reason))
        
        merged.sort(key=lambda x: x[1], reverse=True)
        return merged
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: SMART ENTRY LOGIC
    # ═══════════════════════════════════════════════════════════════
    
    
    async def _check_entry_signals_predictive(
        self,
        portfolio: Dict,
        opportunities_with_scores: List[Tuple],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> TradeDecision:
        """Entry signals with PREDICTIVE confirmation + CHAIN FILTERING"""
        
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        usdc_value = self._get_total_usdc(holdings)
        
        # ✅ NEW: Get chains where we have USDC
        available_chains = self._get_chains_with_usdc(holdings)
        
        logger.info(f"💰 Portfolio: ${total_value:,.2f} | USDC: ${usdc_value:,.2f} | Positions: {len(positions)}")
        logger.info(f"🔗 Chains with USDC: {', '.join(sorted(available_chains)) if available_chains else 'NONE'}")
        logger.info(f"🎯 Dynamic score threshold: {self._dynamic_score_threshold:.1f}")
        
        # ✅ Check if we have ANY USDC on ANY chain
        if not available_chains:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="❌ No USDC available on any chain"
            )
        
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
        
        if not opportunities_with_scores:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="🔭 No opportunities"
            )
        
        existing_symbols = {pos.symbol for pos in positions}
        validated_count = 0
        failed_tokens = []
        skipped_chains = 0
        
        logger.info(f"🔮 Evaluating PREDICTIVE opportunities...")
        
        for token, score, reason in opportunities_with_scores[:20]:
            # ✅ NEW: Skip if we don't have USDC on this chain
            token_chain_normalized = token.chain.lower()
            if token_chain_normalized == "eth":
                token_chain_normalized = "ethereum"
            elif token_chain_normalized == "svm":
                token_chain_normalized = "solana"
            
            if token_chain_normalized not in available_chains:
                skipped_chains += 1
                if skipped_chains <= 3:  # Only log first 3
                    logger.debug(f"   ⏭️ {token.symbol} on {token.chain}: No USDC on this chain")
                continue
            
            # Skip if already holding
            symbol_key = f"{token.symbol}_{token.chain}"
            if symbol_key in existing_symbols or token.symbol in existing_symbols:
                continue
            
            # Check cooldown (prevent rapid re-entry after exit)
            if self._is_on_cooldown(token.address):
                logger.debug(f"   ⏳ {token.symbol}: On cooldown")
                continue
            
            # Score threshold (dynamic)
            if score < self._dynamic_score_threshold:
                continue
            
            validated_count += 1
            logger.info(f"🔍 #{validated_count}: {token.symbol} on {token.chain} (Score: {score:.1f})")
            
            # Check predictive signals
            signal_count = 0
            signal_types = []
            if hasattr(token, 'predictive_signals') and token.predictive_signals:
                signal_count = len(token.predictive_signals)
                signal_types = [s.signal_type for s in token.predictive_signals]
                logger.info(f"   🔮 Predictive signals: {signal_types}")
            
            # CONFIRMATION REQUIREMENT
            required_conviction = self._determine_required_conviction(token, score, signal_count)
            
            # Validate address
            is_valid, validation_reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            if not is_valid:
                logger.warning(f"   ❌ Invalid: {validation_reason}")
                failed_tokens.append(f"{token.symbol} ({validation_reason})")
                continue
            
            if token_validator.is_blacklisted(token.address):
                logger.warning(f"   🚫 Blacklisted")
                continue
            
            # MOMENTUM CONFIRMATION
            momentum_confirmed = self._confirm_momentum(token, market_snapshot)
            if not momentum_confirmed:
                logger.warning(f"   ⚠️ Momentum not confirmed - waiting")
                failed_tokens.append(f"{token.symbol} (no_momentum)")
                continue
            
            # VOLUME-PRICE DIVERGENCE CHECK
            if self._detect_volume_price_divergence(token):
                logger.warning(f"   ⚠️ Volume-price divergence detected - skipping")
                failed_tokens.append(f"{token.symbol} (divergence)")
                continue
            
            # Pre-validate
            trade_validation = self._pre_validate_token_requirements(token, portfolio, positions)
            
            if not trade_validation["valid"]:
                logger.warning(f"   ❌ {trade_validation['reason']}")
                failed_tokens.append(f"{token.symbol} ({trade_validation['reason']})")
                continue
            
            logger.info(f"   ✅ All checks passed!")
            
            # Classify opportunity
            signal_type, conviction = self._classify_opportunity_predictive(token, signal_types)
            
            # Ensure minimum conviction
            if conviction.value < required_conviction.value:
                conviction = required_conviction
            
            # Position sizing
            position_size = self._calculate_predictive_position_size(
                usdc_value,
                total_value,
                conviction,
                metrics,
                score,
                signal_count
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.warning(f"   ⚠️ Position too small: ${position_size:.2f}")
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
                reason=f"🔮 PREDICTIVE: {reason} | Score: {score:.1f}",
                metadata={
                    "token_address": token.address,
                    "chain": token.chain,
                    "score": score,
                    "change_24h": token.change_24h_pct,
                    "volume_24h": token.volume_24h,
                    "liquidity": token.liquidity_usd,
                    "price": token.price,
                    "market_cap": token.market_cap,
                    "predictive_signals": signal_types,
                    "signal_count": signal_count
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
                    failed_tokens.append(f"{token.symbol} (LLM)")
                    continue
                
                decision.conviction = new_conviction
                decision.reason = f"{decision.reason} | LLM: {reasoning}"
            
            return decision
        
        # No valid trades
        summary_parts = []
        
        if skipped_chains > 0:
            summary_parts.append(f"{skipped_chains} wrong chain")
        
        if failed_tokens:
            failures_summary = ", ".join(failed_tokens[:3])
            summary_parts.append(failures_summary)
        
        if summary_parts:
            reason = f"🔍 No valid trades: {' | '.join(summary_parts)}"
        else:
            reason = f"🔍 No suitable opportunities"
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason=reason
        )
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: CONFIRMATION METHODS
    # ═══════════════════════════════════════════════════════════════
    
    def _confirm_momentum(self, token: DiscoveredToken, market_snapshot: MarketSnapshot) -> bool:
        """
        🆕 Confirm momentum before entry
        Returns True if momentum supports the trade
        """
        # Check 1: Price not in free-fall
        if token.change_24h_pct < -15:
            return False
        
        # Check 2: Volume supports the move
        if token.volume_24h < token.liquidity_usd * 0.1:
            # Volume less than 10% of liquidity = weak momentum
            return False
        
        # Check 3: If we have predictive signals, trust them more
        if hasattr(token, 'predictive_signals') and token.predictive_signals:
            # Has predictive signals = more lenient on momentum
            for signal in token.predictive_signals:
                if signal.signal_type in ["whale_buy", "buy_pressure", "volume_spike"]:
                    return True  # Strong signal overrides momentum check
        
        # Check 4: Market condition alignment
        if self._market_state == "bearish" and token.change_24h_pct < 0:
            return False  # Don't buy falling tokens in bear market
        
        return True
    
    def _detect_volume_price_divergence(self, token: DiscoveredToken) -> bool:
        """
        🆕 Detect suspicious volume-price patterns
        Returns True if divergence detected (bad sign)
        """
        # High volume but price dropping = distribution (bad)
        if token.volume_24h > token.liquidity_usd * 0.5 and token.change_24h_pct < -5:
            return True
        
        # Price pumping but low volume = fake pump (bad)
        if token.change_24h_pct > 30 and token.volume_24h < token.liquidity_usd * 0.2:
            return True
        
        return False
    
    def _determine_required_conviction(
        self, 
        token: DiscoveredToken, 
        score: float,
        signal_count: int
    ) -> Conviction:
        """
        🆕 Determine minimum conviction required for entry
        """
        # High score + multiple signals = can enter with lower conviction
        if score >= 15 and signal_count >= 2:
            return Conviction.LOW
        
        # Good score + at least one signal
        if score >= 10 and signal_count >= 1:
            return Conviction.MEDIUM
        
        # Otherwise need high conviction
        return Conviction.HIGH
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: SMART SWAP LOGIC
    # ═══════════════════════════════════════════════════════════════
    
    async def _check_smart_swaps(
        self,
        portfolio: Dict,
        opportunities: List[DiscoveredToken],
        metrics: TradingMetrics,
        market_snapshot: MarketSnapshot
    ) -> Optional[TradeDecision]:
        """
        🆕 SMART SWAPS - Only swap if new opportunity is SIGNIFICANTLY better
        AND current position shows weakness
        """
        positions = portfolio.get("positions", [])
        
        if not positions or not opportunities:
            return None
        
        # Score opportunities
        scored_opportunities = self._score_opportunities_predictive(opportunities)
        
        # Find best opportunity not already held
        best_opportunity = None
        best_score = 0
        
        for token, score, reason in scored_opportunities[:10]:
            symbol_key = f"{token.symbol}_{token.chain}"
            if any(pos.symbol == symbol_key or pos.symbol == token.symbol for pos in positions):
                continue
            
            # Validate
            is_valid, _ = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            if not is_valid or token_validator.is_blacklisted(token.address):
                continue
            
            # 🆕 HIGHER THRESHOLD for swaps
            if score > best_score and score >= self._dynamic_swap_threshold:
                # 🆕 Must have predictive signals for swap
                if hasattr(token, 'predictive_signals') and token.predictive_signals:
                    best_opportunity = (token, score, reason)
                    best_score = score
        
        if not best_opportunity:
            return None
        
        token, new_score, opp_reason = best_opportunity
        
        # Find position to swap - 🆕 SMARTER CRITERIA
        swap_candidates = []
        
        for position in positions:
            swap_score = 0
            swap_reasons = []
            
            # 🆕 Only swap if position shows WEAKNESS
            
            # Losing position
            if position.pnl_pct < -5:
                swap_score += 100
                swap_reasons.append(f"losing {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 0:
                swap_score += 60
                swap_reasons.append(f"slight loss {position.pnl_pct:.1f}%")
            
            # Stale position (no movement)
            elif -2 < position.pnl_pct < 2:
                # Only swap stale if new opportunity is MUCH better
                if new_score >= position.pnl_pct + 15:  # 15 point advantage
                    swap_score += 40
                    swap_reasons.append(f"stale vs score {new_score:.1f}")
            
            # 🆕 DON'T swap profitable positions unless exceptional
            elif position.pnl_pct > 5:
                # Only swap if new opportunity has whale signals
                if hasattr(token, 'predictive_signals'):
                    has_whale = any(
                        s.signal_type in ["whale_buy", "buy_pressure"] 
                        for s in token.predictive_signals
                    )
                    if has_whale and new_score >= 20:
                        swap_score += 20
                        swap_reasons.append(f"whale signal on new token")
            
            if swap_score > 0:
                reason = ", ".join(swap_reasons)
                swap_candidates.append((position, reason, swap_score))
        
        if not swap_candidates:
            return None
        
        # Get best swap candidate
        swap_candidates.sort(key=lambda x: x[2], reverse=True)
        position, reason, _ = swap_candidates[0]
        
        logger.info(f"🔄 SMART SWAP IDENTIFIED!")
        logger.info(f"   OUT: {position.symbol} ({reason})")
        logger.info(f"   IN:  {token.symbol} (Score: {new_score:.1f})")
        
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(position.symbol, {})
        
        return TradeDecision(
            action=TradingAction.SELL,
            from_token=position.symbol,
            to_token="USDC",
            amount_usd=position.value * 0.98,
            conviction=Conviction.HIGH,
            signal_type=SignalType.REBALANCE,
            reason=f"🔄 Smart swap: {position.symbol}→{token.symbol} ({reason})",
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
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: MOMENTUM REVERSAL EXIT
    # ═══════════════════════════════════════════════════════════════
    
    def _check_momentum_reversal_exit(
        self, 
        position: Position, 
        portfolio: Dict,
        opportunities: List[DiscoveredToken]
    ) -> Optional[TradeDecision]:
        """
        🆕 Exit if momentum reverses (even if not at stop loss)
        """
        holdings = portfolio.get("holdings", {})
        position_holding = holdings.get(position.symbol, {})
        
        # Find this token in opportunities to check current state
        current_token = None
        for token in opportunities:
            if token.symbol == position.symbol or f"{token.symbol}_{token.chain}" == position.symbol:
                current_token = token
                break
        
        if not current_token:
            return None
        
        # 🆕 Check for reversal signals
        should_exit = False
        exit_reason = ""
        
        # Was profitable but now dropping fast
        if position.pnl_pct > 5 and current_token.change_24h_pct < -10:
            should_exit = True
            exit_reason = f"Momentum reversal: was +{position.pnl_pct:.1f}%, now dropping"
        
        # Volume spike with price drop = distribution
        if current_token.volume_24h > current_token.liquidity_usd * 0.8:
            if current_token.change_24h_pct < -5:
                should_exit = True
                exit_reason = f"High volume selloff detected"
        
        if should_exit:
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"📉 {exit_reason}",
                metadata={
                    "token_address": position_holding.get("tokenAddress", ""),
                    "chain": position_holding.get("chain", "eth"),
                    "price": position.current_price,
                    "liquidity": 999999,
                    "volume_24h": 999999,
                    "score": 10.0
                }
            )
        
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: ADAPTIVE METHODS
    # ═══════════════════════════════════════════════════════════════
    
    def _update_market_state(self, market_snapshot: MarketSnapshot, metrics: TradingMetrics):
        """
        🆕 Update market state assessment
        """
        # Simple market state based on BTC/ETH performance
        btc_change = getattr(market_snapshot, 'btc_change_24h', 0)
        eth_change = getattr(market_snapshot, 'eth_change_24h', 0)
        
        avg_change = (btc_change + eth_change) / 2
        
        if avg_change > 3:
            self._market_state = "bullish"
        elif avg_change < -3:
            self._market_state = "bearish"
        else:
            self._market_state = "neutral"
        
        self._last_market_update = datetime.now(timezone.utc)
        logger.debug(f"📊 Market state: {self._market_state} (BTC: {btc_change:.1f}%, ETH: {eth_change:.1f}%)")
    
    def _update_adaptive_thresholds(self, metrics: TradingMetrics):
        """
        🆕 Adjust thresholds based on recent performance
        """
        # After losses, be more selective
        if metrics.consecutive_losses >= 3:
            self._dynamic_score_threshold = 8.0  # Higher bar
            self._dynamic_swap_threshold = 12.0
            logger.info(f"   ⚠️ Raised thresholds after {metrics.consecutive_losses} losses")
        elif metrics.consecutive_losses >= 2:
            self._dynamic_score_threshold = 6.0
            self._dynamic_swap_threshold = 10.0
        else:
            self._dynamic_score_threshold = 5.0
            self._dynamic_swap_threshold = 8.0
        
        # In bearish market, be more selective
        if self._market_state == "bearish":
            self._dynamic_score_threshold += 2.0
            self._dynamic_swap_threshold += 2.0
    
    def _is_good_trading_time(self) -> bool:
        """
        🆕 Check if current time is good for trading
        Avoid low-liquidity hours
        """
        now = datetime.now(timezone.utc)
        hour = now.hour
        
        # Best hours: US + EU overlap (13:00-21:00 UTC)
        # Worst hours: 02:00-08:00 UTC (Asia only, low volume)
        
        if 2 <= hour <= 8:
            return False  # Low liquidity hours
        
        return True
    
    def _is_on_cooldown(self, address: str) -> bool:
        """
        🆕 Check if token is on cooldown (recently exited)
        """
        if address not in self._entry_cooldowns:
            return False
        
        cooldown_end = self._entry_cooldowns[address]
        if datetime.now(timezone.utc) < cooldown_end:
            return True
        
        # Cooldown expired
        del self._entry_cooldowns[address]
        return False
    
    def set_cooldown(self, address: str, hours: int = 4):
        """
        🆕 Set cooldown for a token after exit
        """
        self._entry_cooldowns[address] = datetime.now(timezone.utc) + timedelta(hours=hours)
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 NEW: PREDICTIVE POSITION SIZING
    # ═══════════════════════════════════════════════════════════════
    
    def _calculate_predictive_position_size(
        self,
        usdc_available: float,
        total_value: float,
        conviction: Conviction,
        metrics: TradingMetrics,
        opportunity_score: float,
        signal_count: int
    ) -> float:
        """
        🆕 Position sizing with predictive signal bonus
        """
        base_size = config.BASE_POSITION_SIZE
        max_available = usdc_available * 0.95
        
        size = min(base_size, max_available)
        
        # Conviction multiplier
        conviction_mult = {
            Conviction.HIGH: 1.0,
            Conviction.MEDIUM: 0.75,
            Conviction.LOW: 0.5
        }
        size *= conviction_mult.get(conviction, 0.75)
        
        # 🆕 Predictive signal bonus
        if signal_count >= 3:
            size *= 1.3  # 30% bonus for multiple signals
        elif signal_count >= 2:
            size *= 1.15  # 15% bonus
        
        # Score bonus
        if opportunity_score >= 20:
            size *= 1.2
        elif opportunity_score >= 15:
            size *= 1.1
        
        # Loss reduction
        if metrics.consecutive_losses >= 3:
            size *= 0.5
        elif metrics.consecutive_losses >= 2:
            size *= 0.7
        
        # Market state adjustment
        if self._market_state == "bearish":
            size *= 0.7  # Smaller positions in bear market
        elif self._market_state == "bullish":
            size *= 1.1  # Slightly larger in bull market
        
        # Bounds
        if total_value > 0:
            max_position = total_value * config.MAX_POSITION_PCT
            size = min(size, max_position)
        
        size = min(size, max_available)
        size = max(size, config.MIN_TRADE_SIZE)
        
        return size
    
    # ═══════════════════════════════════════════════════════════════
    # EXISTING METHODS (Updated)
    # ═══════════════════════════════════════════════════════════════
    
    def _find_exit_candidates_for_rebalance(
        self,
        positions: List[Position],
        usdc_needed: float,
        portfolio: Dict,
        opportunities: List[DiscoveredToken] = None  # 🆕 Added
    ) -> List[Tuple[Position, str]]:
        """
        Find positions to exit for rebalancing
        🆕 Now considers opportunity quality
        """
        candidates = []
        
        # 🆕 Calculate best opportunity score
        best_opp_score = 0
        if opportunities:
            for token in opportunities[:5]:
                score = self._calculate_predictive_score(token)
                best_opp_score = max(best_opp_score, score)
        
        for position in positions:
            exit_score = 0
            reason_parts = []
            
            # Performance
            if position.pnl_pct < -5:
                exit_score += 100
                reason_parts.append(f"losing {position.pnl_pct:.1f}%")
            elif position.pnl_pct < -2:
                exit_score += 70
                reason_parts.append(f"slight loss {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 2:
                exit_score += 40
                reason_parts.append(f"flat {position.pnl_pct:.1f}%")
            elif position.pnl_pct < 5:
                exit_score += 20
                reason_parts.append(f"small gain {position.pnl_pct:.1f}%")
            
            # Position size
            if position.value < config.BASE_POSITION_SIZE * 0.3:
                exit_score += 30
                reason_parts.append(f"small ${position.value:.0f}")
            
            # Covers need
            if position.value >= usdc_needed:
                exit_score += 15
                reason_parts.append("covers need")
            
            # 🆕 Opportunity comparison
            if best_opp_score >= 15 and position.pnl_pct < 5:
                exit_score += 25
                reason_parts.append(f"better opp ({best_opp_score:.1f})")
            
            reason = ", ".join(reason_parts)
            candidates.append((position, reason, exit_score))
        
        candidates.sort(key=lambda x: x[2], reverse=True)
        return [(pos, reason) for pos, reason, _ in candidates]
    
    def _check_exit_signals(
        self, 
        position: Position, 
        portfolio: Dict,
        market_snapshot: MarketSnapshot = None  # 🆕 Added
    ) -> Optional[TradeDecision]:
        """Check exit signals with market context"""
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
        
        # 🆕 Dynamic stop loss based on market state
        stop_loss_pct = config.STOP_LOSS_PCT
        if self._market_state == "bearish":
            stop_loss_pct = config.STOP_LOSS_PCT * 0.7  # Tighter stops in bear market
        
        # Stop Loss
        if position.should_stop_loss(stop_loss_pct):
            # 🆕 Set cooldown after stop loss
            if position_holding.get("tokenAddress"):
                self.set_cooldown(position_holding["tokenAddress"], hours=6)
            
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"🛑 Stop-loss: {position.pnl_pct:.1f}%",
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
        
        # Take Profit
        if position.should_take_profit(config.TAKE_PROFIT_PCT):
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.5,
                conviction=Conviction.MEDIUM,
                signal_type=SignalType.TAKE_PROFIT,
                reason=f"🎯 Take-profit: {position.pnl_pct:.1f}%",
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
            return {"valid": False, "reason": "low_liquidity"}
        
        if token.volume_24h < config.MIN_VOLUME_24H_USD:
            return {"valid": False, "reason": "low_volume"}
        
        if len(positions) >= config.MAX_POSITIONS:
            return {"valid": False, "reason": "max_positions"}
        
        return {"valid": True, "reason": "OK"}
    
    def _classify_opportunity_predictive(
        self, 
        token: DiscoveredToken,
        signal_types: List[str] = None
    ) -> tuple:
        """
        🆕 Classify opportunity with predictive signals
        """
        score = self._calculate_predictive_score(token)
        change = token.change_24h_pct
        
        # 🆕 Check predictive signals first
        if signal_types:
            if "whale_buy" in signal_types or "buy_pressure" in signal_types:
                return SignalType.VOLUME_SPIKE, Conviction.HIGH
            if "volume_spike" in signal_types:
                return SignalType.VOLUME_SPIKE, Conviction.HIGH
            if "new_launch" in signal_types:
                return SignalType.BREAKOUT, Conviction.MEDIUM
        
        # Fallback to price-based classification
        if score > 15:
            if change > 10:
                return SignalType.BREAKOUT, Conviction.HIGH
            elif change < -10:
                return SignalType.MEAN_REVERSION, Conviction.HIGH
            else:
                return SignalType.MOMENTUM, Conviction.HIGH
        
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