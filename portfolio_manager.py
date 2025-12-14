"""
Portfolio Manager - Handles portfolio state and trade execution
"""
from datetime import datetime, timezone
from typing import Dict, Optional
from collections import deque

from config import config
from models import (
    TrackedPosition, Position, TradeDecision, TradingAction
)
from api_client import RecallAPIClient
from logging_manager import get_logger

logger = get_logger("PortfolioManager")


class PortfolioManager:
    """
    Manages portfolio state, tracking, and trade execution
    Feeds results back to market scanner for learning
    """
    
    def __init__(self, api_client: RecallAPIClient, market_scanner=None):
        self.client = api_client
        self.market_scanner = market_scanner  # For feedback loop
        self.tracked_positions: Dict[str, TrackedPosition] = {}
        self.position_history: list = []
        self.trade_history: list = []
        self.price_history: Dict[str, deque] = {}
        
        logger.info("💼 Portfolio Manager initialized")
        if market_scanner:
            logger.info("   ✅ Feedback loop to scanner enabled")
    
    async def get_portfolio_state(self, competition_id: str) -> Dict:
        """Get comprehensive portfolio state"""
        try:
            portfolio = await self.client.get_portfolio(competition_id)
            
            balances = portfolio.get("balances", [])
            total_value = 0
            holdings = {}
            positions = []
            
            for balance in balances:
                symbol = balance.get("symbol", "")
                amount = float(balance.get("amount", 0))
                current_price = float(balance.get("price", 0))
                chain = balance.get("specificChain", "")
                token_address = balance.get("tokenAddress", "").lower()
                
                value = amount * current_price
                total_value += value
                
                # Find matching config
                matched_symbol = self._match_token_config(token_address, chain, symbol)
                
                if matched_symbol:
                    # Check if it's a stablecoin or discovered token
                    is_stablecoin = matched_symbol in config.TOKENS and config.TOKENS[matched_symbol].stable
                    
                    holdings[matched_symbol] = {
                        "symbol": matched_symbol,
                        "base_symbol": symbol,
                        "amount": amount,
                        "value": value,
                        "price": current_price,
                        "chain": chain,
                        "pct": 0
                    }
                    
                    # Build position objects for non-stables with value > minimum
                    if not is_stablecoin and amount > 0 and value >= config.MIN_POSITION_VALUE:
                        tracked = self.tracked_positions.get(matched_symbol)
                        
                        if tracked:
                            entry_price = tracked.entry_price
                            pnl_pct = (
                                (current_price - entry_price) / entry_price * 100
                                if entry_price > 0 else 0
                            )
                            
                            tracked.update_price_tracking(current_price)
                            
                            positions.append(Position(
                                symbol=matched_symbol,
                                amount=amount,
                                entry_price=entry_price,
                                current_price=current_price,
                                value=value,
                                pnl_pct=pnl_pct,
                                highest_price=tracked.highest_price,
                                lowest_price=tracked.lowest_price_since_entry
                            ))
                        else:
                            # Create tracking for existing position
                            logger.info(f"📍 Creating tracker for: {matched_symbol}")
                            self.tracked_positions[matched_symbol] = TrackedPosition(
                                symbol=matched_symbol,
                                entry_price=current_price,
                                entry_amount=amount,
                                entry_value_usd=value,
                                entry_timestamp=datetime.now(timezone.utc).isoformat()
                            )
                            
                            positions.append(Position(
                                symbol=matched_symbol,
                                amount=amount,
                                entry_price=current_price,
                                current_price=current_price,
                                value=value,
                                pnl_pct=0,
                                highest_price=current_price,
                                lowest_price=current_price
                            ))
            
            # Calculate percentages
            for symbol in holdings:
                if total_value > 0:
                    holdings[symbol]["pct"] = holdings[symbol]["value"] / total_value * 100
            
            return {
                "total_value": total_value,
                "holdings": holdings,
                "positions": positions,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get portfolio: {e}")
            return {}
    
    def _match_token_config(self, address: str, chain: str, symbol: str) -> Optional[str]:
        """
        Match token to config entry
        Only stablecoins are in config - discovered tokens are handled dynamically
        """
        # Check if it's a stablecoin (these are in config)
        for config_symbol, token_config in config.TOKENS.items():
            if (token_config.address.lower() == address.lower() and 
                token_config.chain == chain):
                return config_symbol
        
        # For discovered tokens (not in config), create identifier
        # Format: SYMBOL_CHAIN (e.g., PEPE_ethereum, ARB_arbitrum)
        return f"{symbol}_{chain}"
    
    async def execute_trade(
        self,
        decision: TradeDecision,
        portfolio: Dict,
        competition_id: str
    ) -> bool:
        """Execute trade with comprehensive pre-trade validation"""
        
        if decision.action == TradingAction.HOLD:
            return False
        
        # PRODUCTION: Pre-trade validation
        validation_result = self._validate_trade(decision, portfolio)
        if not validation_result["valid"]:
            logger.warning(f"⚠️ Trade validation failed: {validation_result['reason']}")
            return False
        
        # Extract metadata
        metadata = decision.metadata
        token_address = metadata.get("token_address")
        chain = metadata.get("chain")
        
        if not token_address or not chain:
            logger.error("❌ Missing token metadata")
            return False
        
        # Get stablecoin for trade
        from_symbol = decision.from_token
        to_symbol = decision.to_token
        amount_usd = decision.amount_usd
        
        # Find best USDC balance
        holdings = portfolio.get("holdings", {})
        usdc_symbol, usdc_available = self._find_best_usdc(holdings)
        
        if not usdc_symbol:
            logger.error("❌ No USDC available")
            return False
        
        # Get token configs
        from_config = config.TOKENS.get(usdc_symbol)
        
        if not from_config:
            logger.error(f"❌ Invalid from token: {usdc_symbol}")
            return False
        
        # Calculate trade amount
        usdc_balance = holdings.get(usdc_symbol, {})
        available_amount = usdc_balance.get("amount", 0)
        from_price = usdc_balance.get("price", 1.0)
        
        max_safe_amount = available_amount * 0.98
        max_safe_value = max_safe_amount * from_price
        
        trade_value = min(amount_usd, max_safe_value)
        trade_amount = trade_value / from_price
        
        if trade_amount > max_safe_amount:
            trade_amount = max_safe_amount
        
        if trade_amount < config.MIN_TRADE_SIZE:
            logger.warning(f"❌ Trade too small")
            return False
        
        # Format amount
        decimals = from_config.decimals
        amount_str = f"{trade_amount:.{decimals}f}"
        
        logger.info("=" * 70)
        logger.info(f"📤 EXECUTING {decision.action.name}")
        logger.info(f"   From: {usdc_symbol} on {from_config.chain}")
        logger.info(f"   To: {to_symbol} ({token_address[:10]}...)")
        logger.info(f"   Amount: {amount_str} USDC")
        logger.info(f"   Value: ${trade_value:.2f}")
        logger.info(f"   Signal: {decision.signal_type.value}")
        logger.info(f"   Conviction: {decision.conviction.value}")
        logger.info("=" * 70)
        
        try:
            result = await self.client.execute_trade(
                competition_id=competition_id,
                from_token=from_config.address,
                to_token=token_address,
                amount=amount_str,
                reason=decision.reason[:500],
                from_chain=from_config.chain,
                to_chain=chain
            )
            
            if result.get("success"):
                logger.info("✅ TRADE SUCCESSFUL!")
                
                # Track position
                if decision.action == TradingAction.BUY:
                    await self._track_buy(to_symbol, trade_value, metadata)
                elif decision.action == TradingAction.SELL:
                    await self._track_sell(from_symbol, trade_value)
                
                # 🔄 FEEDBACK TO SCANNER: Trade succeeded
                if self.market_scanner and token_address:
                    self.market_scanner.record_trade_result(
                        token_address,
                        to_symbol,
                        success=True,
                        pnl_pct=None  # Don't know PnL yet (just entry)
                    )
                
                # Record in history
                self.trade_history.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": decision.action.name,
                    "from": from_symbol,
                    "to": to_symbol,
                    "amount_usd": trade_value,
                    "signal_type": decision.signal_type.value,
                    "conviction": decision.conviction.value,
                    "reason": decision.reason
                })
                
                return True
            else:
                logger.error(f"❌ Trade failed: {result.get('error')}")
                
                # 🔄 FEEDBACK TO SCANNER: Trade failed
                if self.market_scanner and token_address:
                    self.market_scanner.record_trade_result(
                        token_address,
                        to_symbol,
                        success=False
                    )
                
                return False
                
        except Exception as e:
            logger.error(f"❌ Trade error: {e}")
            return False
    
    def _validate_trade(self, decision: TradeDecision, portfolio: Dict) -> Dict:
        """
        PRODUCTION: Comprehensive pre-trade validation
        Returns: {"valid": bool, "reason": str}
        """
        # Check 1: Minimum trade size
        if decision.amount_usd < config.MIN_TRADE_SIZE:
            return {
                "valid": False,
                "reason": f"Trade size ${decision.amount_usd:.2f} < minimum ${config.MIN_TRADE_SIZE}"
            }
        
        # Check 2: Token metadata present
        metadata = decision.metadata
        if not metadata.get("token_address") or not metadata.get("chain"):
            return {
                "valid": False,
                "reason": "Missing token metadata"
            }
        
        # Check 3: Liquidity validation
        liquidity = metadata.get("liquidity", 0)
        if liquidity < config.MIN_LIQUIDITY_USD:
            return {
                "valid": False,
                "reason": f"Insufficient liquidity ${liquidity:,.0f} < ${config.MIN_LIQUIDITY_USD:,.0f}"
            }
        
        # Check 4: Volume validation
        volume = metadata.get("volume_24h", 0)
        if volume < config.MIN_VOLUME_24H_USD:
            return {
                "valid": False,
                "reason": f"Insufficient volume ${volume:,.0f} < ${config.MIN_VOLUME_24H_USD:,.0f}"
            }
        
        # Check 5: Opportunity score
        score = metadata.get("score", 0)
        if score < config.OPPORTUNITY_SCORE_THRESHOLD:
            return {
                "valid": False,
                "reason": f"Score {score:.1f} < threshold {config.OPPORTUNITY_SCORE_THRESHOLD}"
            }
        
        # Check 6: Portfolio capacity
        positions = portfolio.get("positions", [])
        if len(positions) >= config.MAX_POSITIONS:
            return {
                "valid": False,
                "reason": f"Max positions reached ({len(positions)}/{config.MAX_POSITIONS})"
            }
        
        # Check 7: USDC availability
        holdings = portfolio.get("holdings", {})
        total_usdc = sum(
            h["value"] for s, h in holdings.items()
            if "USDC" in s or "USD" in s
        )
        if total_usdc < decision.amount_usd:
            return {
                "valid": False,
                "reason": f"Insufficient USDC (${total_usdc:.2f} < ${decision.amount_usd:.2f})"
            }
        
        # Check 8: Risk limits
        total_value = portfolio.get("total_value", 0)
        deployed = sum(
            h["value"] for s, h in holdings.items()
            if s != "USDC" and not config.TOKENS.get(s, config.TOKENS.get("USDC")).stable
        )
        
        # Would this trade exceed max risk?
        new_deployed = deployed + decision.amount_usd
        new_deployed_pct = new_deployed / total_value if total_value > 0 else 0
        
        if new_deployed_pct > config.MAX_PORTFOLIO_RISK:
            return {
                "valid": False,
                "reason": f"Would exceed max risk ({new_deployed_pct*100:.0f}% > {config.MAX_PORTFOLIO_RISK*100:.0f}%)"
            }
        
        # All checks passed
        return {"valid": True, "reason": "All validations passed"}
    
    async def _track_buy(self, symbol: str, value_usd: float, metadata: Dict):
        """Track buy trade"""
        price = metadata.get("price", 0)
        
        if symbol in self.tracked_positions:
            self.tracked_positions[symbol].update_for_add(
                value_usd / price if price > 0 else 0,
                price,
                value_usd
            )
            logger.info(f"📍 Updated position: {symbol}")
        else:
            self.tracked_positions[symbol] = TrackedPosition(
                symbol=symbol,
                entry_price=price,
                entry_amount=value_usd / price if price > 0 else 0,
                entry_value_usd=value_usd,
                entry_timestamp=datetime.now(timezone.utc).isoformat()
            )
            logger.info(f"📍 New position: {symbol} @ ${price:.4f}")
    
    async def _track_sell(self, symbol: str, value_usd: float):
        """Track sell trade with feedback to scanner"""
        if symbol in self.tracked_positions:
            tracked = self.tracked_positions[symbol]
            
            # Calculate PnL
            exit_price = tracked.entry_price  # Simplified - would need actual exit price
            pnl_pct = 0  # TODO: Calculate actual PnL
            
            # Archive to history
            position_data = {
                "symbol": symbol,
                "entry_price": tracked.entry_price,
                "entry_timestamp": tracked.entry_timestamp,
                "exit_timestamp": datetime.now(timezone.utc).isoformat(),
                "pnl_pct": pnl_pct
            }
            self.position_history.append(position_data)
            
            # 🔄 FEEDBACK TO SCANNER: Record exit result
            if self.market_scanner and hasattr(tracked, 'token_address'):
                self.market_scanner.record_trade_result(
                    tracked.token_address,
                    symbol,
                    success=True,
                    pnl_pct=pnl_pct
                )
            
            # Remove from tracking
            del self.tracked_positions[symbol]
            logger.info(f"📍 Closed position: {symbol} (P&L: {pnl_pct:+.1f}%)")
    
    def _find_best_usdc(self, holdings: Dict) -> tuple:
        """Find best USDC balance"""
        usdc_balances = []
        
        for symbol, holding in holdings.items():
            if "USDC" in symbol or "USD" in symbol:
                value = holding.get("value", 0)
                if value >= 10:
                    chain = holding.get("chain", "eth")
                    gas_rank = {"polygon": 1, "arbitrum": 2, "base": 3, "eth": 5}.get(chain, 10)
                    usdc_balances.append((symbol, value, gas_rank))
        
        if not usdc_balances:
            return None, 0
        
        # Sort by gas cost then value
        usdc_balances.sort(key=lambda x: (x[2], -x[1]))
        
        return usdc_balances[0][0], usdc_balances[0][1]
    
    async def get_state(self) -> Dict:
        """Get current state for persistence"""
        return {
            "tracked_positions": {
                k: v.to_dict() for k, v in self.tracked_positions.items()
            },
            "position_history": self.position_history[-500:],
            "trade_history": self.trade_history[-1000:]
        }
    
    async def restore_state(self, state: Dict):
        """Restore state from persistence"""
        if "portfolio_state" in state:
            ps = state["portfolio_state"]
            
            if "tracked_positions" in ps:
                self.tracked_positions = {
                    k: TrackedPosition.from_dict(v)
                    for k, v in ps["tracked_positions"].items()
                }
            
            self.position_history = ps.get("position_history", [])
            self.trade_history = ps.get("trade_history", [])
        
        logger.info(f"✅ Restored {len(self.tracked_positions)} tracked positions")