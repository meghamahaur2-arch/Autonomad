"""
Self-Thinking Market Scanner
Automatically discovers and analyzes trading opportunities
"""
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from collections import deque
from aiohttp import ClientTimeout, TCPConnector

from config import config
from models import (
    DiscoveredToken, MarketSnapshot, SignalType, 
    Conviction, InsufficientLiquidityError
)
from logging_manager import get_logger

logger = get_logger("MarketScanner")


class MarketScanner:
    """
    Autonomous market scanner with auto-blacklist learning
    """
    
    BASE_URL = "https://api.dexscreener.com/latest/dex"
    
    # Chain-specific tuning (different chains have different characteristics)
    CHAIN_CONFIGS = {
        "ethereum": {
            "min_liquidity": 500_000,  # ETH = expensive, needs more liquidity
            "min_volume": 1_000_000,
            "gas_cost_weight": 1.0,    # Highest gas
            "quality_bonus": 1.5       # Most established tokens
        },
        "polygon": {
            "min_liquidity": 100_000,  # Cheap gas, can trade smaller
            "min_volume": 200_000,
            "gas_cost_weight": 0.1,
            "quality_bonus": 1.0
        },
        "arbitrum": {
            "min_liquidity": 150_000,
            "min_volume": 300_000,
            "gas_cost_weight": 0.2,
            "quality_bonus": 1.2
        },
        "base": {
            "min_liquidity": 100_000,  # Newer chain, lower requirements
            "min_volume": 200_000,
            "gas_cost_weight": 0.15,
            "quality_bonus": 0.9       # Less established
        },
        "optimism": {
            "min_liquidity": 150_000,
            "min_volume": 300_000,
            "gas_cost_weight": 0.2,
            "quality_bonus": 1.1
        }
    }
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, any] = {}
        self._discovered_tokens: Dict[str, DiscoveredToken] = {}
        self._volume_history: Dict[str, deque] = {}
        
        # AUTO-BLACKLIST SYSTEM
        self._blacklist: Set[str] = set()  # Addresses to avoid
        self._token_performance: Dict[str, Dict] = {}  # Track how tokens perform
        self._failed_trades: Dict[str, int] = {}  # Count failures per token
        
        # Rate limiting
        self._semaphore = asyncio.Semaphore(10)
        self._last_request_time = datetime.now()
        self._request_count = 0
        
        logger.info("🔍 Advanced Market Scanner initialized")
        logger.info("   ✅ Auto-blacklist learning enabled")
        logger.info("   ✅ Chain-specific tuning active")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300
            )
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=20),
                connector=connector
            )
        return self._session
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def scan_market(
        self, 
        chains: Optional[List[str]] = None,
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None
    ) -> List[DiscoveredToken]:
        """
        Scan market for trading opportunities with chain-specific tuning
        """
        if chains is None:
            chains = ["ethereum", "polygon", "arbitrum", "base", "optimism"]
        
        # Use global minimums as fallback, but chains have their own configs
        global_min_liquidity = min_liquidity or config.MIN_LIQUIDITY_USD
        global_min_volume = min_volume or config.MIN_VOLUME_24H_USD
        
        logger.info(f"🔍 Scanning {len(chains)} chains for opportunities...")
        logger.info(f"   Global Min Liquidity: ${global_min_liquidity:,.0f}")
        logger.info(f"   Global Min Volume: ${global_min_volume:,.0f}")
        logger.info(f"   Blacklist size: {len(self._blacklist)} tokens")
        
        all_tokens = []
        
        for chain in chains:
            try:
                # Get chain-specific config
                chain_config = self.CHAIN_CONFIGS.get(chain, {
                    "min_liquidity": global_min_liquidity,
                    "min_volume": global_min_volume,
                    "gas_cost_weight": 0.5,
                    "quality_bonus": 1.0
                })
                
                tokens = await self._scan_chain(
                    chain, 
                    chain_config["min_liquidity"],
                    chain_config["min_volume"]
                )
                
                # Apply chain-specific quality bonus
                for token in tokens:
                    token.opportunity_score *= chain_config["quality_bonus"]
                
                all_tokens.extend(tokens)
                logger.info(f"   ✅ {chain}: Found {len(tokens)} tokens (bonus: {chain_config['quality_bonus']}x)")
            except Exception as e:
                logger.warning(f"   ⚠️ {chain}: Failed to scan - {e}")
        
        # Filter and score
        filtered_tokens = self._filter_tokens(all_tokens)
        scored_tokens = self._score_opportunities(filtered_tokens)
        
        # Update cache
        for token in scored_tokens:
            self._discovered_tokens[f"{token.symbol}_{token.chain}"] = token
        
        logger.info(f"✅ Scan complete: {len(scored_tokens)} opportunities found")
        
        return scored_tokens
    
    async def _scan_chain(
        self, 
        chain: str, 
        min_liquidity: float, 
        min_volume: float
    ) -> List[DiscoveredToken]:
        """Scan a specific chain for tokens"""
        async with self._semaphore:
            session = await self._get_session()
            
            # DexScreener trending endpoint
            url = f"{self.BASE_URL}/search"
            
            # Search for high-volume pairs on this chain
            try:
                await asyncio.sleep(0.1)  # Rate limiting
                
                async with session.get(
                    url, 
                    params={"q": chain},
                    timeout=15
                ) as resp:
                    if resp.status != 200:
                        return []
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    tokens = []
                    seen_addresses = set()
                    
                    for pair in pairs[:50]:  # Limit to top 50 pairs
                        try:
                            # Extract token info
                            chain_id = pair.get("chainId", "").lower()
                            if chain_id != chain.lower():
                                continue
                            
                            base_token = pair.get("baseToken", {})
                            quote_token = pair.get("quoteToken", {})
                            
                            # Skip if quote isn't a stablecoin
                            if quote_token.get("symbol", "").upper() not in ["USDC", "USDT", "DAI", "WETH"]:
                                continue
                            
                            address = base_token.get("address", "").lower()
                            symbol = base_token.get("symbol", "UNKNOWN")
                            
                            # Skip duplicates
                            if address in seen_addresses:
                                continue
                            seen_addresses.add(address)
                            
                            # Extract metrics
                            price = float(pair.get("priceUsd", 0))
                            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
                            volume = float(pair.get("volume", {}).get("h24", 0))
                            change_24h = float(pair.get("priceChange", {}).get("h24", 0))
                            market_cap = pair.get("fdv")  # Fully diluted valuation
                            
                            # Filter by liquidity and volume
                            if liquidity < min_liquidity or volume < min_volume:
                                continue
                            
                            # Create discovered token
                            token = DiscoveredToken(
                                symbol=symbol,
                                address=address,
                                chain=chain,
                                price=price,
                                liquidity_usd=liquidity,
                                volume_24h=volume,
                                change_24h_pct=change_24h,
                                market_cap=float(market_cap) if market_cap else None
                            )
                            
                            tokens.append(token)
                            
                        except Exception as e:
                            logger.debug(f"Failed to parse pair: {e}")
                            continue
                    
                    return tokens
                    
            except Exception as e:
                logger.warning(f"Failed to scan {chain}: {e}")
                return []
    
    def _filter_tokens(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Apply filters with auto-blacklist learning"""
        filtered = []
        
        for token in tokens:
            # Skip auto-blacklisted tokens
            if token.address in self._blacklist:
                logger.debug(f"⏭️ Auto-blacklisted: {token.symbol} ({self._get_blacklist_reason(token.address)})")
                continue
            
            # ENHANCED: More comprehensive scam detection (relaxed slightly)
            suspicious_patterns = [
                "test", "xxx", "scam", "rug", "fake",
                "elon", "moon", "safe",  # Removed some aggressive filters
                "token", "coin",  # Generic names
                "v2", "2.0", "new"  # Fork indicators
            ]
            symbol_lower = token.symbol.lower()
            if any(pattern in symbol_lower for pattern in suspicious_patterns):
                logger.debug(f"⏭️ Skipping suspicious token: {token.symbol}")
                self._soft_blacklist(token.address, "suspicious_pattern")
                continue
            
            # Check liquidity/volume ratio (RELAXED from 0.1 to 0.05)
            if token.volume_24h > 0:
                lv_ratio = token.liquidity_usd / token.volume_24h
                
                if lv_ratio < 0.05:  # RELAXED: Volume is 20x liquidity (was 10x)
                    logger.debug(f"⏭️ {token.symbol}: Suspicious L/V ratio ({lv_ratio:.3f})")
                    self._soft_blacklist(token.address, "wash_trading")
                    continue
                
                # Also check if liquidity is TOO high vs volume
                if lv_ratio > 20:
                    logger.debug(f"⏭️ {token.symbol}: Stale pool (high liquidity, low volume)")
                    continue
            
            # Market cap check (RELAXED from $1M to $500k)
            if token.market_cap and token.market_cap < 500_000:  # RELAXED
                logger.debug(f"⏭️ {token.symbol}: Market cap too low (${token.market_cap:,.0f})")
                continue
            
            # Symbol length check
            if len(token.symbol) > 10:
                logger.debug(f"⏭️ {token.symbol}: Symbol too long")
                continue
            
            # Price sanity check
            if token.price <= 0 or token.price > 1_000_000:
                logger.debug(f"⏭️ {token.symbol}: Invalid price (${token.price})")
                continue
            
            filtered.append(token)
        
        return filtered
    
    def _score_opportunities(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """
        Score tokens with chain-specific adjustments (RELAXED thresholds)
        """
        for token in tokens:
            score = 0.0
            
            # Get chain config for bonus
            chain_config = self.CHAIN_CONFIGS.get(token.chain, {"quality_bonus": 1.0})
            
            # Volume surge component (REDUCED weight)
            volume_surge = self._calc_volume_surge(
                f"{token.symbol}_{token.chain}", 
                token.volume_24h
            )
            score += volume_surge * 1.5  # REDUCED from 2.0
            
            # Price momentum component (MORE LENIENT - lowered from 5% to 3%)
            abs_change = abs(token.change_24h_pct)
            if abs_change > 3:  # RELAXED from 5%
                score += abs_change * 0.4  # INCREASED from 0.3
            
            # Liquidity safety component (ADJUSTED for lower minimums)
            if token.liquidity_usd > 500_000:  # $500k+
                score += 3.0
            elif token.liquidity_usd > 250_000:  # $250k+
                score += 2.0
            elif token.liquidity_usd > 100_000:  # $100k+ (LOWERED)
                score += 1.0
            else:
                score -= 1.0  # Light penalty
            
            # Volume component (ADJUSTED for lower minimums)
            if token.volume_24h > 2_000_000:  # $2M+ (LOWERED from $5M)
                score += 3.0
            elif token.volume_24h > 1_000_000:  # $1M+ (LOWERED from $2M)
                score += 2.0
            elif token.volume_24h > 500_000:  # $500k+ (LOWERED from $1M)
                score += 1.0
            else:
                score -= 0.5  # Light penalty
            
            # Market cap component (ADJUSTED range)
            if token.market_cap:
                if 20_000_000 < token.market_cap < 500_000_000:  # $20M-$500M sweet spot (LOWERED from $50M)
                    score += 3.0
                elif 5_000_000 < token.market_cap < 20_000_000:  # $5M-$20M (LOWERED from $10M-$50M)
                    score += 1.5
                elif token.market_cap < 2_000_000:  # <$2M (LOWERED from $5M)
                    score -= 2.0  # REDUCED penalty
            
            # Stability bonus (prefer established tokens)
            if abs_change < 3:  # Less than 3% move
                score += 1.0
            
            # Volume consistency check
            if volume_surge < 0.5:  # Volume is stable
                score += 0.5
            
            # Performance history bonus (if we've traded this before)
            if token.address in self._token_performance:
                perf = self._token_performance[token.address]
                win_rate = perf.get("win_rate", 0)
                if win_rate > 0.6:  # 60%+ win rate
                    score += 2.0
                    logger.debug(f"   🌟 {token.symbol}: Performance bonus ({win_rate*100:.0f}% win rate)")
                elif win_rate < 0.3:  # <30% win rate
                    score -= 1.0
            
            token.opportunity_score = max(0.0, score)
        
        # Sort by score descending
        tokens.sort(key=lambda t: t.opportunity_score, reverse=True)
        
        # RELAXED: Return tokens with score > 6.0 (was 8.0)
        quality_tokens = [t for t in tokens if t.opportunity_score >= 6.0]
        
        logger.info(f"   💎 {len(quality_tokens)} high-quality tokens (score >= 6.0)")
        
        return quality_tokens
    
    def _calc_volume_surge(self, identifier: str, volume: float) -> float:
        """Calculate volume surge score"""
        if volume <= 0:
            return 0.0
        
        if identifier not in self._volume_history:
            self._volume_history[identifier] = deque(maxlen=20)
        
        hist = self._volume_history[identifier]
        hist.append(volume)
        
        if len(hist) < 3:
            return 0.1
        
        if len(hist) < 7:
            avg = sum(hist) / len(hist)
            if avg == 0:
                return 0.3
            ratio = volume / avg
            return max(0.0, (ratio - 0.5) * 0.5)
        
        # EMA calculation
        ema_alpha = 0.3
        ema = hist[0]
        for v in list(hist)[1:]:
            ema = ema_alpha * v + (1 - ema_alpha) * ema
        
        if ema == 0:
            return 0.5
        
        ratio = volume / ema
        surge_score = max(0.0, ratio - 0.7)
        
        return surge_score
    
    def blacklist_token(self, address: str, reason: str = "manual"):
        """Permanently blacklist a token"""
        self._blacklist.add(address.lower())
        if address not in self._token_performance:
            self._token_performance[address] = {}
        self._token_performance[address]["blacklist_reason"] = reason
        self._token_performance[address]["blacklisted_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"🚫 Permanently blacklisted: {address[:10]}... ({reason})")
    
    def _soft_blacklist(self, address: str, reason: str):
        """Soft blacklist (temporary, based on failures)"""
        if address not in self._failed_trades:
            self._failed_trades[address] = 0
        self._failed_trades[address] += 1
        
        # Auto-blacklist after 3 failures
        if self._failed_trades[address] >= 3:
            self.blacklist_token(address, f"auto_{reason}_x{self._failed_trades[address]}")
    
    def _get_blacklist_reason(self, address: str) -> str:
        """Get why a token was blacklisted"""
        if address in self._token_performance:
            return self._token_performance[address].get("blacklist_reason", "unknown")
        return "unknown"
    
    def record_trade_result(
        self, 
        token_address: str, 
        token_symbol: str,
        success: bool,
        pnl_pct: Optional[float] = None
    ):
        """
        🔄 FEEDBACK LOOP: Learn from trade results
        Auto-blacklist tokens that consistently fail
        """
        address = token_address.lower()
        
        if address not in self._token_performance:
            self._token_performance[address] = {
                "symbol": token_symbol,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "consecutive_losses": 0
            }
        
        perf = self._token_performance[address]
        perf["trades"] += 1
        
        if success and pnl_pct is not None:
            if pnl_pct > 0:
                perf["wins"] += 1
                perf["consecutive_losses"] = 0
            else:
                perf["losses"] += 1
                perf["consecutive_losses"] += 1
            perf["total_pnl"] += pnl_pct
        elif not success:
            # Trade failed (e.g., execution error, slippage)
            perf["losses"] += 1
            perf["consecutive_losses"] += 1
            self._soft_blacklist(address, "trade_failure")
        
        perf["win_rate"] = perf["wins"] / perf["trades"] if perf["trades"] > 0 else 0
        perf["avg_pnl"] = perf["total_pnl"] / perf["trades"] if perf["trades"] > 0 else 0
        
        # Auto-blacklist logic
        if perf["trades"] >= 5:  # Need at least 5 trades
            if perf["win_rate"] < 0.25:  # <25% win rate
                self.blacklist_token(address, f"low_winrate_{perf['win_rate']*100:.0f}%")
            elif perf["consecutive_losses"] >= 5:
                self.blacklist_token(address, f"consecutive_losses_x{perf['consecutive_losses']}")
            elif perf["avg_pnl"] < -5:  # Average loss >5%
                self.blacklist_token(address, f"avg_loss_{perf['avg_pnl']:.1f}%")
        
        logger.debug(
            f"📊 {token_symbol}: {perf['wins']}W/{perf['losses']}L "
            f"({perf['win_rate']*100:.0f}% WR, {perf['avg_pnl']:+.1f}% avg)"
        )
    
    def get_discovered_tokens(self) -> Dict[str, DiscoveredToken]:
        """Get all currently discovered tokens"""
        return self._discovered_tokens
    
    def get_top_opportunities(self, n: int = 10) -> List[DiscoveredToken]:
        """Get top N opportunities by score"""
        tokens = sorted(
            self._discovered_tokens.values(),
            key=lambda t: t.opportunity_score,
            reverse=True
        )
        return tokens[:n]