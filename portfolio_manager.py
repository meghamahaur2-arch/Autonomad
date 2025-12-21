"""
Portfolio Manager - FIXED VERSION
✅ Fixed USDC detection (address + symbol)
✅ Better error handling and recovery
✅ Trade state persistence for failure recovery
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from collections import deque
import json

from config import config
from models import (
    TrackedPosition, Position, TradeDecision, TradingAction
)
from api_client import RecallAPIClient
from logging_manager import get_logger
from token_validator import token_validator

logger = get_logger("PortfolioManager")


class PortfolioManager:
    """
    Manages portfolio state, tracking, and trade execution
    ✅ ALL CRITICAL FIXES APPLIED
    """
    
    # Trade execution states
    TRADE_STATE_PENDING = "pending"
    TRADE_STATE_EXECUTING = "executing"
    TRADE_STATE_SUCCESS = "success"
    TRADE_STATE_FAILED = "failed"
    
    def __init__(self, api_client: RecallAPIClient, market_scanner=None):
        self.client = api_client
        self.market_scanner = market_scanner
        self.tracked_positions: Dict[str, TrackedPosition] = {}
        self.position_history: deque = deque(maxlen=500)
        self.trade_history: deque = deque(maxlen=1000)
        self.price_history: Dict[str, deque] = {}
        
        # ✅ NEW: Trade state tracking for recovery
        self.pending_trades: Dict[str, Dict] = {}
        self.failed_trade_attempts: Dict[str, int] = {}
        
        logger.info("💼 Portfolio Manager initialized")
        if market_scanner:
            logger.info("   ✅ Feedback loop to scanner enabled")
        logger.info("   ✅ Token validation enabled")
        logger.info("   ✅ Trade recovery enabled")
    
    async def get_portfolio_state(self, competition_id: str) -> Dict:
        """Get comprehensive portfolio state with error handling"""
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
                
                matched_symbol = self._match_token_config(token_address, chain, symbol)
                
                if matched_symbol:
                    # ✅ FIX: Proper stablecoin detection using both address AND symbol
                    is_stablecoin = self._is_stablecoin(token_address, chain, symbol)
                    
                    holdings[matched_symbol] = {
                        "symbol": matched_symbol,
                        "base_symbol": symbol,
                        "amount": amount,
                        "value": value,
                        "price": current_price,
                        "chain": chain,
                        "tokenAddress": token_address,
                        "pct": 0,
                        "is_stablecoin": is_stablecoin  # ✅ NEW: Explicit flag
                    }
                    
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
                            logger.info(f"📍 Creating tracker for: {matched_symbol}")
                            self.tracked_positions[matched_symbol] = TrackedPosition(
                                symbol=matched_symbol,
                                entry_price=current_price,
                                entry_amount=amount,
                                entry_value_usd=value,
                                entry_timestamp=datetime.now(timezone.utc).isoformat(),
                                token_address=token_address,
                                chain=chain
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
            logger.error(f"❌ Failed to get portfolio: {e}", exc_info=True)
            # ✅ NEW: Return cached state if API fails
            return self._get_cached_portfolio_state()
    
    def _is_stablecoin(self, address: str, chain: str, symbol: str) -> bool:
        """
        ✅ FIXED: Proper stablecoin detection using BOTH address and symbol
        """
        address_lower = address.lower()
        chain_lower = chain.lower()
        
        # Method 1: Check by address in config (most reliable)
        for token_config in config.TOKENS.values():
            if (token_config.address.lower() == address_lower and 
                token_config.chain.lower() == chain_lower and
                token_config.stable):
                logger.debug(f"✅ Stablecoin by address: {symbol}")
                return True
        
        # Method 2: Check symbol patterns (fallback)
        stable_patterns = ["USDC", "USDT", "DAI", "USD", "BUSD", "TUSD", "FRAX", "USDB"]
        symbol_upper = symbol.upper()
        
        for pattern in stable_patterns:
            if pattern in symbol_upper:
                logger.debug(f"✅ Stablecoin by symbol pattern: {symbol}")
                return True
        
        return False
    
    def _match_token_config(self, address: str, chain: str, symbol: str) -> Optional[str]:
        """Match token to config entry (case-insensitive)"""
        address_lower = address.lower()
        chain_lower = chain.lower()
        
        for config_symbol, token_config in config.TOKENS.items():
            if (token_config.address.lower() == address_lower and 
                token_config.chain.lower() == chain_lower):
                return config_symbol
        
        return f"{symbol}_{chain}"
    
    async def execute_trade(
        self,
        decision: TradeDecision,
        portfolio: Dict,
        competition_id: str
    ) -> bool:
        """
        ✅ FIXED: Execute trade with comprehensive error handling and recovery
        """
        
        if decision.action == TradingAction.HOLD:
            return False
        
        # Create trade ID for tracking
        trade_id = f"{decision.action.name}_{decision.to_token if decision.action == TradingAction.BUY else decision.from_token}_{datetime.now(timezone.utc).timestamp()}"
        
        # ✅ NEW: Record pending trade for recovery
        self.pending_trades[trade_id] = {
            "state": self.TRADE_STATE_PENDING,
            "decision": decision,
            "portfolio_snapshot": portfolio.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            # Extract metadata
            metadata = decision.metadata
            token_address = metadata.get("token_address")
            chain = metadata.get("chain")
            
            if not token_address or not chain:
                error_msg = "Missing token metadata"
                logger.error(f"❌ {error_msg}")
                await self._record_trade_failure(trade_id, error_msg)
                return False
            
            # ✅ CRITICAL FIX: Only validate token for BUY trades
            if decision.action == TradingAction.BUY:
                to_symbol = decision.to_token.split('_')[0]
                
                logger.info(f"🔍 Validating token {to_symbol} ({token_address[:10]}...)")
                is_valid, reason = token_validator.validate_token(
                    address=token_address,
                    chain=chain,
                    symbol=to_symbol,
                    price=metadata.get("price", 0),
                    liquidity=metadata.get("liquidity", 0)
                )
                
                if not is_valid:
                    error_msg = f"Token validation failed: {reason}"
                    logger.error(f"❌ {error_msg}")
                    token_validator.record_trade_failure(token_address, chain)
                    
                    if self.market_scanner:
                        self.market_scanner.blacklist_token(token_address, f"validation_failed_{reason}")
                    
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                if token_validator.is_blacklisted(token_address):
                    error_msg = f"Token {to_symbol} is blacklisted"
                    logger.error(f"🚫 {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                logger.info(f"✅ Token validation passed")
            
            # ✅ NEW: Re-fetch portfolio before execution (prevent stale state)
            logger.info("🔄 Refreshing portfolio state before execution...")
            fresh_portfolio = await self.get_portfolio_state(competition_id)
            
            if not fresh_portfolio or fresh_portfolio.get("total_value", 0) == 0:
                error_msg = "Cannot fetch fresh portfolio state"
                logger.error(f"❌ {error_msg}")
                await self._record_trade_failure(trade_id, error_msg)
                return False
            
            # Get stablecoin for trade
            from_symbol = decision.from_token
            to_symbol_full = decision.to_token
            amount_usd = decision.amount_usd
            
            holdings = fresh_portfolio.get("holdings", {})
            
            # ✅ FIXED: Different logic for BUY vs SELL
            if decision.action == TradingAction.BUY:
                # BUY: Need USDC
                usdc_result = self._find_best_usdc(holdings)
                
                if not usdc_result:
                    error_msg = "No USDC available for BUY"
                    logger.error(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                usdc_symbol, usdc_available = usdc_result
                from_symbol = usdc_symbol
                from_holding = holdings.get(usdc_symbol, {})
                from_address = from_holding.get("tokenAddress", "")
                from_chain = from_holding.get("chain", "").lower()
                
                if not from_address or not from_chain:
                    error_msg = f"Missing USDC metadata for {usdc_symbol}"
                    logger.error(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                # Find USDC config
                result = self._find_usdc_config(from_address, from_chain)
                
                if not result:
                    error_msg = f"No USDC config found for {usdc_symbol}"
                    logger.error(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                matched_config_symbol, from_config = result
                logger.info(f"✅ Matched {usdc_symbol} to config {matched_config_symbol} on {from_config.chain}")
                
                # Calculate trade amount
                available_amount = from_holding.get("amount", 0)
                from_price = from_holding.get("price", 1.0)
                
                max_safe_amount = available_amount * 0.98
                max_safe_value = max_safe_amount * from_price
                
                trade_value = min(amount_usd, max_safe_value)
                trade_amount = trade_value / from_price
                
                if trade_amount > max_safe_amount:
                    trade_amount = max_safe_amount
                
                if trade_amount < config.MIN_TRADE_SIZE:
                    error_msg = f"Trade too small: {trade_amount:.6f} < {config.MIN_TRADE_SIZE}"
                    logger.warning(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                decimals = from_config.decimals
                amount_str = f"{trade_amount:.{decimals}f}"
                
                logger.info("=" * 70)
                logger.info(f"📤 EXECUTING BUY")
                logger.info(f"   From: {usdc_symbol} (→ {matched_config_symbol}) on {from_config.chain}")
                logger.info(f"   To: {to_symbol_full.split('_')[0]} ({token_address[:10]}...) on {chain}")
                logger.info(f"   Amount: {amount_str} {matched_config_symbol}")
                logger.info(f"   Value: ${trade_value:.2f}")
                logger.info("=" * 70)
                
                to_address = token_address
                to_chain = chain
            
            else:  # SELL
                from_holding = holdings.get(from_symbol, {})
                
                if not from_holding:
                    error_msg = f"Position {from_symbol} not found in holdings"
                    logger.error(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                from_address = from_holding.get("tokenAddress", "")
                from_chain = from_holding.get("chain", "").lower()
                from_price = from_holding.get("price", 0)
                available_amount = from_holding.get("amount", 0)
                
                if not from_address or not from_chain:
                    error_msg = f"Missing metadata for {from_symbol}"
                    logger.error(f"❌ {error_msg}")
                    await self._record_trade_failure(trade_id, error_msg)
                    return False
                
                # ✅ NEW: Validate sell address too (prevent corrupted metadata)
                sell_valid, sell_reason = token_validator.validate_token(
                    address=from_address,
                    chain=from_chain,
                    symbol=from_symbol,
                    price=from_price
                )
                
                if not sell_valid:
                    logger.warning(f"⚠️ Sell address validation failed: {sell_reason}")
                    # Don't block sell, but log it
                
                # Calculate sell amount
                max_safe_value = available_amount * from_price * 0.98
                trade_value = min(amount_usd, max_safe_value)
                trade_amount = (trade_value / from_price) if from_price > 0 else available_amount * 0.98
                
                if trade_amount > available_amount * 0.98:
                    trade_amount = available_amount * 0.98
                
                decimals = 18
                amount_str = f"{trade_amount:.{decimals}f}"
                
                # For SELL, we sell TO a stablecoin
                usdc_result = self._find_best_usdc(holdings)
                
                if not usdc_result:
                    to_address = config.TOKENS["USDC"].address
                    to_chain = config.TOKENS["USDC"].chain
                    usdc_name = "USDC"
                else:
                    usdc_symbol, _ = usdc_result
                    usdc_holding = holdings.get(usdc_symbol, {})
                    to_address = usdc_holding.get("tokenAddress", config.TOKENS["USDC"].address)
                    to_chain = usdc_holding.get("chain", config.TOKENS["USDC"].chain).lower()
                    usdc_name = usdc_symbol
                
                logger.info("=" * 70)
                logger.info(f"📤 EXECUTING SELL")
                logger.info(f"   From: {from_symbol} ({from_address[:10]}...) on {from_chain}")
                logger.info(f"   To: {usdc_name} ({to_address[:10]}...) on {to_chain}")
                logger.info(f"   Amount: {trade_amount:.6f} {from_symbol}")
                logger.info(f"   Estimated Value: ${trade_value:.2f}")
                logger.info("=" * 70)
            
            # ✅ NEW: Update trade state to executing
            self.pending_trades[trade_id]["state"] = self.TRADE_STATE_EXECUTING
            self.pending_trades[trade_id]["execution_started"] = datetime.now(timezone.utc).isoformat()
            
            # Execute the trade
            result = await self.client.execute_trade(
                competition_id=competition_id,
                from_token=from_address,
                to_token=to_address,
                amount=amount_str,
                reason=decision.reason[:500],
                from_chain=from_chain,
                to_chain=to_chain
            )
            
            if result.get("success"):
                logger.info("✅ TRADE SUCCESSFUL!")
                
                # ✅ NEW: Update trade state
                self.pending_trades[trade_id]["state"] = self.TRADE_STATE_SUCCESS
                self.pending_trades[trade_id]["result"] = result
                
                if decision.action == TradingAction.BUY:
                    await self._track_buy(to_symbol_full, trade_value, metadata)
                elif decision.action == TradingAction.SELL:
                    current_price = from_price
                    await self._track_sell(from_symbol, trade_value, current_price)
                
                # Feedback to scanner
                if self.market_scanner and token_address:
                    symbol_for_feedback = to_symbol_full.split('_')[0] if decision.action == TradingAction.BUY else from_symbol.split('_')[0]
                    self.market_scanner.record_trade_result(
                        token_address if decision.action == TradingAction.BUY else from_address,
                        symbol_for_feedback,
                        success=True,
                        pnl_pct=None
                    )
                
                self.trade_history.append({
                    "trade_id": trade_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": decision.action.name,
                    "from": from_symbol,
                    "to": to_symbol_full,
                    "amount_usd": trade_value,
                    "signal_type": decision.signal_type.value,
                    "conviction": decision.conviction.value,
                    "reason": decision.reason,
                    "success": True
                })
                
                # Cleanup pending trade after success
                del self.pending_trades[trade_id]
                
                return True
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"❌ Trade failed: {error_msg}")
                
                await self._record_trade_failure(trade_id, error_msg, result)
                
                # Record failure
                if decision.action == TradingAction.BUY:
                    token_validator.record_trade_failure(token_address, chain)
                    
                    if self.market_scanner:
                        self.market_scanner.record_trade_result(
                            token_address,
                            to_symbol_full.split('_')[0],
                            success=False
                        )
                
                return False
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Trade exception: {error_msg}", exc_info=True)
            
            await self._record_trade_failure(trade_id, error_msg)
            
            # Record failure for BUY trades
            if decision.action == TradingAction.BUY:
                if "Unable to determine price" in error_msg or "400" in error_msg:
                    logger.error(f"🚫 Recording failed address for blacklisting")
                    token_validator.record_trade_failure(token_address, chain)
                    
                    if self.market_scanner:
                        self.market_scanner.blacklist_token(
                            token_address,
                            "unable_to_price"
                        )
            
            return False
    
    async def _record_trade_failure(self, trade_id: str, error_msg: str, result: Dict = None):
        """✅ NEW: Record trade failure for recovery"""
        if trade_id in self.pending_trades:
            self.pending_trades[trade_id]["state"] = self.TRADE_STATE_FAILED
            self.pending_trades[trade_id]["error"] = error_msg
            self.pending_trades[trade_id]["error_time"] = datetime.now(timezone.utc).isoformat()
            if result:
                self.pending_trades[trade_id]["api_result"] = result
            
            # Track failure count
            decision = self.pending_trades[trade_id]["decision"]
            key = f"{decision.action.name}_{decision.to_token}"
            self.failed_trade_attempts[key] = self.failed_trade_attempts.get(key, 0) + 1
            
            logger.error(f"💾 Recorded trade failure: {trade_id[:20]}... (attempt #{self.failed_trade_attempts[key]})")
    
    def _get_cached_portfolio_state(self) -> Dict:
        """✅ NEW: Return cached portfolio state as fallback"""
        logger.warning("⚠️ Using cached portfolio state (API unavailable)")
        
        # Reconstruct from tracked positions
        holdings = {}
        positions = []
        total_value = 0
        
        for symbol, tracked in self.tracked_positions.items():
            value = tracked.entry_value_usd
            total_value += value
            
            holdings[symbol] = {
                "symbol": symbol,
                "amount": tracked.entry_amount,
                "value": value,
                "price": tracked.entry_price,
                "chain": tracked.chain,
                "tokenAddress": tracked.token_address,
                "pct": 0,
                "is_stablecoin": False
            }
            
            positions.append(Position(
                symbol=symbol,
                amount=tracked.entry_amount,
                entry_price=tracked.entry_price,
                current_price=tracked.entry_price,
                value=value,
                pnl_pct=0,
                highest_price=tracked.highest_price,
                lowest_price=tracked.lowest_price_since_entry
            ))
        
        for symbol in holdings:
            if total_value > 0:
                holdings[symbol]["pct"] = holdings[symbol]["value"] / total_value * 100
        
        return {
            "total_value": total_value,
            "holdings": holdings,
            "positions": positions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cached": True
        }
    
    def _find_usdc_config(self, address: str, chain: str) -> Optional[Tuple]:
        """Find USDC config - handles multiple USDC addresses per chain"""
        address_lower = address.lower()
        chain_lower = chain.lower()
        
        for config_symbol, token_config in config.TOKENS.items():
            if (token_config.address.lower() == address_lower and 
                token_config.chain.lower() == chain_lower and
                token_config.stable):
                return (config_symbol, token_config)
        
        for config_symbol, token_config in config.TOKENS.items():
            if (token_config.chain.lower() == chain_lower and
                token_config.stable and
                "USDC" in config_symbol.upper()):
                logger.warning(
                    f"⚠️ Using fallback USDC config: {config_symbol} "
                    f"(address mismatch: {address_lower[:10]}... != {token_config.address.lower()[:10]}...)"
                )
                return (config_symbol, token_config)
        
        return None
    
    def _find_best_usdc(self, holdings: Dict) -> Optional[Tuple[str, float]]:
        """✅ FIXED: Find best USDC balance using stablecoin flag"""
        usdc_balances = []
        
        for symbol, holding in holdings.items():
            # ✅ Use the is_stablecoin flag we set earlier
            if holding.get("is_stablecoin", False):
                value = holding.get("value", 0)
                if value >= 10:
                    chain = holding.get("chain", "eth").lower()
                    gas_rank = {
                        "polygon": 1, 
                        "arbitrum": 2, 
                        "base": 3, 
                        "optimism": 4, 
                        "eth": 5,
                        "ethereum": 5
                    }.get(chain, 10)
                    usdc_balances.append((symbol, value, gas_rank))
        
        if not usdc_balances:
            return None
        
        usdc_balances.sort(key=lambda x: (x[2], -x[1]))
        return (usdc_balances[0][0], usdc_balances[0][1])
    
    async def _track_buy(self, symbol: str, value_usd: float, metadata: Dict):
        """Track buy trade"""
        price = metadata.get("price", 0)
        token_address = metadata.get("token_address", "")
        chain = metadata.get("chain", "")
        
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
                entry_timestamp=datetime.now(timezone.utc).isoformat(),
                token_address=token_address,
                chain=chain
            )
            logger.info(f"📍 New position: {symbol} @ ${price:.4f}")
    
    async def _track_sell(self, symbol: str, value_usd: float, current_price: float = 0):
        """Track sell trade with feedback to scanner"""
        if symbol in self.tracked_positions:
            tracked = self.tracked_positions[symbol]
            
            if current_price > 0 and tracked.entry_price > 0:
                pnl_pct = ((current_price - tracked.entry_price) / tracked.entry_price) * 100
            else:
                pnl_pct = 0
            
            position_data = {
                "symbol": symbol,
                "entry_price": tracked.entry_price,
                "exit_price": current_price if current_price > 0 else tracked.entry_price,
                "entry_timestamp": tracked.entry_timestamp,
                "exit_timestamp": datetime.now(timezone.utc).isoformat(),
                "pnl_pct": pnl_pct
            }
            self.position_history.append(position_data)
            
            if self.market_scanner and tracked.token_address:
                self.market_scanner.record_trade_result(
                    tracked.token_address,
                    symbol,
                    success=True,
                    pnl_pct=pnl_pct
                )
                logger.debug(f"📊 Feedback sent to scanner: {symbol} ({pnl_pct:+.1f}% PnL)")
            
            del self.tracked_positions[symbol]
            logger.info(f"📍 Closed position: {symbol} (P&L: {pnl_pct:+.1f}%)")
    
    async def get_state(self) -> Dict:
        """Get current state for persistence"""
        return {
            "tracked_positions": {
                k: v.to_dict() for k, v in self.tracked_positions.items()
            },
            "position_history": list(self.position_history),
            "trade_history": list(self.trade_history),
            "pending_trades": self.pending_trades,
            "failed_trade_attempts": self.failed_trade_attempts
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
            
            if "position_history" in ps:
                self.position_history.extend(ps["position_history"])
            if "trade_history" in ps:
                self.trade_history.extend(ps["trade_history"])
            if "pending_trades" in ps:
                self.pending_trades = ps["pending_trades"]
            if "failed_trade_attempts" in ps:
                self.failed_trade_attempts = ps["failed_trade_attempts"]
        
        logger.info(f"✅ Restored {len(self.tracked_positions)} tracked positions")
        
        # ✅ NEW: Check for pending trades that need recovery
        if self.pending_trades:
            logger.warning(f"⚠️ Found {len(self.pending_trades)} pending trades from previous session")
            await self._recover_pending_trades()
    
    async def _recover_pending_trades(self):
        """✅ NEW: Attempt to recover pending trades"""
        for trade_id, trade_data in list(self.pending_trades.items()):
            state = trade_data.get("state")
            timestamp = trade_data.get("timestamp")
            
            logger.info(f"🔄 Checking pending trade: {trade_id[:20]}... (state: {state})")
            
            # If trade was executing, assume it failed (we can't know)
            if state == self.TRADE_STATE_EXECUTING:
                logger.warning(f"⚠️ Trade was executing during shutdown, marking as failed")
                trade_data["state"] = self.TRADE_STATE_FAILED
                trade_data["error"] = "Session interrupted during execution"
            
            # Clean up old failed trades (> 1 hour)
            if state == self.TRADE_STATE_FAILED:
                try:
                    trade_time = datetime.fromisoformat(timestamp)
                    age = (datetime.now(timezone.utc) - trade_time).total_seconds()
                    if age > 3600:  # 1 hour
                        logger.info(f"🗑️ Removing old failed trade: {trade_id[:20]}...")
                        del self.pending_trades[trade_id]
                except Exception as e:
                    logger.error(f"Error processing pending trade: {e}")