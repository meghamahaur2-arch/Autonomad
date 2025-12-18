"""
Enhanced Self-Thinking Market Scanner - INTEGRATED WITH TOKEN VALIDATOR
Uses multiple free APIs for comprehensive token discovery
✅ NOW VALIDATES ALL ADDRESSES BEFORE RETURNING
"""
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from collections import deque
from aiohttp import ClientTimeout, TCPConnector

from config import config
from models import (
    DiscoveredToken, MarketSnapshot, SignalType, 
    Conviction, InsufficientLiquidityError
)
from logging_manager import get_logger
from token_validator import token_validator  # ✅ ADDED

logger = get_logger("MarketScanner")


class MultiAPIMarketScanner:
    """
    🔥 FIXED: Enhanced scanner with token address validation
    - DexScreener boosted/trending endpoints
    - GeckoTerminal API (free, no key needed)
    - ✅ TOKEN VALIDATION on all discovered tokens
    """
    
    DEXSCREENER_BASE = "https://api.dexscreener.com"
    GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
    
    CHAIN_CONFIGS = {
        "ethereum": {
            "dex_id": "ethereum",
            "gecko_id": "eth",
            "min_liquidity": 500_000,
            "min_volume": 1_000_000,
            "quality_bonus": 1.5
        },
        "polygon": {
            "dex_id": "polygon",
            "gecko_id": "polygon_pos",
            "min_liquidity": 100_000,
            "min_volume": 200_000,
            "quality_bonus": 1.0
        },
        "arbitrum": {
            "dex_id": "arbitrum",
            "gecko_id": "arbitrum_one",
            "min_liquidity": 150_000,
            "min_volume": 300_000,
            "quality_bonus": 1.2
        },
        "base": {
            "dex_id": "base",
            "gecko_id": "base",
            "min_liquidity": 100_000,
            "min_volume": 200_000,
            "quality_bonus": 1.3
        },
        "optimism": {
            "dex_id": "optimism",
            "gecko_id": "optimism",
            "min_liquidity": 150_000,
            "min_volume": 300_000,
            "quality_bonus": 1.1
        },
        "solana": {
            "dex_id": "solana",
            "gecko_id": "solana",
            "min_liquidity": 50_000,
            "min_volume": 100_000,
            "quality_bonus": 1.2
        },
        "bsc": {
            "dex_id": "bsc",
            "gecko_id": "bsc",
            "min_liquidity": 80_000,
            "min_volume": 150_000,
            "quality_bonus": 0.8
        }
    }
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, any] = {}
        self._discovered_tokens: Dict[str, DiscoveredToken] = {}
        self._volume_history: Dict[str, deque] = {}
        
        self._blacklist: Set[str] = set()
        self._token_performance: Dict[str, Dict] = {}
        self._failed_trades: Dict[str, int] = {}
        
        self._semaphore = asyncio.Semaphore(15)
        self._last_request_time = {}
        
        logger.info("🔍 Multi-API Market Scanner initialized")
        logger.info("   ✅ DexScreener: Latest + Boosted + Search + By Chain")
        logger.info("   ✅ GeckoTerminal: Trending pools")
        logger.info("   ✅ Token Validator: Address validation enabled")
        logger.info("   ✅ Auto-blacklist learning enabled")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=30,
                limit_per_host=15,
                ttl_dns_cache=300
            )
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=20),
                connector=connector
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _rate_limit(self, api_name: str, delay: float = 0.5):
        now = datetime.now()
        last = self._last_request_time.get(api_name)
        
        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
        
        self._last_request_time[api_name] = datetime.now()
    
    async def scan_market(
        self, 
        chains: Optional[List[str]] = None,
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None
    ) -> List[DiscoveredToken]:
        if chains is None:
            chains = ["ethereum", "polygon", "arbitrum", "base", "optimism", "solana", "bsc"]
        
        global_min_liquidity = min_liquidity or config.MIN_LIQUIDITY_USD
        global_min_volume = min_volume or config.MIN_VOLUME_24H_USD
        
        logger.info(f"🔍 Multi-API scan across {len(chains)} chains...")
        logger.info(f"   Min Liquidity: ${global_min_liquidity:,.0f}")
        logger.info(f"   Min Volume: ${global_min_volume:,.0f}")
        logger.info(f"   Blacklist: {len(self._blacklist)} tokens")
        
        all_tokens = []
        tasks = []
        
        # DexScreener boosted tokens
        tasks.append(self._scan_dexscreener_boosted())
        
        # DexScreener search
        tasks.append(self._scan_dexscreener_search_top())
        
        # DexScreener by chain
        for chain in ["ethereum", "polygon", "arbitrum", "base", "optimism", "solana", "bsc"]:
            if chain in chains:
                tasks.append(self._scan_dexscreener_by_chain(chain))
        
        # GeckoTerminal trending pools
        for chain in ["eth", "polygon_pos", "arbitrum_one", "base", "optimism", "solana", "bsc"]:
            tasks.append(self._scan_geckoterminal_trending(chain))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"API call failed: {result}")
                continue
            if isinstance(result, list):
                all_tokens.extend(result)
        
        logger.info(f"   📊 Raw tokens discovered: {len(all_tokens)}")
        
        # Deduplicate
        unique_tokens = {}
        for token in all_tokens:
            key = f"{token.address}_{token.chain}"
            if key not in unique_tokens:
                unique_tokens[key] = token
            else:
                if token.opportunity_score > unique_tokens[key].opportunity_score:
                    unique_tokens[key] = token
        
        all_tokens = list(unique_tokens.values())
        logger.info(f"   🔄 After deduplication: {len(all_tokens)}")
        
        # Apply chain quality bonus
        for token in all_tokens:
            chain_config = self.CHAIN_CONFIGS.get(token.chain, {})
            token.opportunity_score *= chain_config.get("quality_bonus", 1.0)
        
        # Filter tokens
        filtered_tokens = self._filter_tokens(all_tokens)
        logger.info(f"   ✅ After filtering: {len(filtered_tokens)}")
        
        scored_tokens = self._score_opportunities(filtered_tokens)
        logger.info(f"   💎 High quality tokens: {len(scored_tokens)}")
        
        for token in scored_tokens:
            self._discovered_tokens[f"{token.symbol}_{token.chain}"] = token
        
        logger.info(f"✅ Multi-API scan complete: {len(scored_tokens)} opportunities")
        
        return scored_tokens
    
    async def _scan_dexscreener_boosted(self) -> List[DiscoveredToken]:
        """Get boosted tokens from DexScreener"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            url = f"{self.DEXSCREENER_BASE}/token-boosts/latest/v1"
            
            async with self._semaphore:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   DexScreener boosted: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    if not data:
                        return []
                    
                    chain_id = data.get("chainId")
                    token_address = data.get("tokenAddress")
                    
                    if not chain_id or not token_address:
                        return []
                    
                    pairs_url = f"{self.DEXSCREENER_BASE}/latest/dex/tokens/{token_address}"
                    
                    async with session.get(pairs_url, timeout=15) as pairs_resp:
                        if pairs_resp.status != 200:
                            return []
                        
                        pairs_data = await pairs_resp.json()
                        pairs = pairs_data.get("pairs", [])
                        
                        tokens = []
                        for pair in pairs[:10]:
                            token = self._parse_dexscreener_pair(pair)
                            if token:
                                token.opportunity_score += 3.0
                                tokens.append(token)
                        
                        logger.info(f"   DexScreener Boosted: {len(tokens)} tokens")
                        return tokens
        
        except Exception as e:
            logger.debug(f"   DexScreener boosted failed: {e}")
            return []
    
    async def _scan_dexscreener_search_top(self) -> List[DiscoveredToken]:
        """Search DexScreener for top volume tokens"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            search_terms = ["ETH", "BTC", "SOL", "USDC", "PEPE", "LINK", "UNI"]
            all_tokens = []
            seen = set()
            
            for term in search_terms:
                try:
                    url = f"{self.DEXSCREENER_BASE}/latest/dex/search"
                    params = {"q": term}
                    
                    async with self._semaphore:
                        async with session.get(url, params=params, timeout=15) as resp:
                            if resp.status != 200:
                                continue
                            
                            data = await resp.json()
                            pairs = data.get("pairs", [])
                            
                            for pair in pairs[:30]:
                                token = self._parse_dexscreener_pair(pair)
                                if token and token.address not in seen:
                                    all_tokens.append(token)
                                    seen.add(token.address)
                    
                    await asyncio.sleep(0.3)
                
                except Exception as e:
                    logger.debug(f"   Search term '{term}' failed: {e}")
                    continue
            
            logger.info(f"   DexScreener Search: {len(all_tokens)} tokens")
            return all_tokens
        
        except Exception as e:
            logger.debug(f"   DexScreener search failed: {e}")
            return []
    
    async def _scan_dexscreener_by_chain(self, chain: str) -> List[DiscoveredToken]:
        """Scan DexScreener by specific chain"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            chain_config = self.CHAIN_CONFIGS.get(chain, {})
            dex_chain_id = chain_config.get("dex_id", chain)
            
            top_tokens = {
                "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2,0x514910771AF9Ca656af840dff83E8264EcF986CA",
                "polygon": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
                "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                "base": "0x4200000000000000000000000000000000000006",
                "optimism": "0x4200000000000000000000000000000000000006",
                "solana": "So11111111111111111111111111111111111111112",
                "bsc": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
            }
            
            token_addresses = top_tokens.get(chain, "")
            if not token_addresses:
                return []
            
            url = f"{self.DEXSCREENER_BASE}/latest/dex/tokens/{token_addresses}"
            
            async with self._semaphore:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   DexScreener {chain}: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    tokens = []
                    seen = set()
                    
                    for pair in pairs[:50]:
                        token = self._parse_dexscreener_pair(pair)
                        if token and token.address not in seen:
                            tokens.append(token)
                            seen.add(token.address)
                    
                    logger.info(f"   DexScreener {chain}: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.debug(f"   DexScreener {chain} failed: {e}")
            return []
    
    async def _scan_geckoterminal_trending(self, network: str) -> List[DiscoveredToken]:
        """Scan GeckoTerminal trending pools"""
        try:
            await self._rate_limit("geckoterminal", 1.5)
            session = await self._get_session()
            
            url = f"{self.GECKOTERMINAL_BASE}/networks/{network}/trending_pools"
            
            async with self._semaphore:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   GeckoTerminal {network}: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    pools = data.get("data", [])
                    
                    tokens = []
                    for pool in pools[:20]:
                        token = self._parse_geckoterminal_pool(pool, network)
                        if token:
                            tokens.append(token)
                    
                    logger.info(f"   GeckoTerminal {network}: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.debug(f"   GeckoTerminal {network} failed: {e}")
            return []
    
    def _parse_geckoterminal_pool(self, pool: Dict, network: str) -> Optional[DiscoveredToken]:
        """Parse GeckoTerminal pool data with validation"""
        try:
            attributes = pool.get("attributes", {})
            relationships = pool.get("relationships", {})
            
            base_token_data = relationships.get("base_token", {}).get("data", {})
            base_token_id = base_token_data.get("id", "").split("_")
            
            if len(base_token_id) < 2:
                return None
            
            address = base_token_id[1].lower()
            symbol = attributes.get("name", "").split("/")[0].strip()
            
            network_map = {
                "eth": "ethereum",
                "polygon_pos": "polygon",
                "arbitrum_one": "arbitrum",
                "base": "base",
                "optimism": "optimism",
                "solana": "solana",
                "bsc": "bsc"
            }
            chain = network_map.get(network, "ethereum")
            
            price = float(attributes.get("base_token_price_usd") or 0)
            liquidity = float(attributes.get("reserve_in_usd") or 0)
            volume = float(attributes.get("volume_usd", {}).get("h24") or 0)
            
            price_change_data = attributes.get("price_change_percentage", {})
            change_24h = float(price_change_data.get("h24") or 0)
            
            if price <= 0 or liquidity <= 0:
                return None
            
            # ✅ VALIDATE TOKEN BEFORE RETURNING
            is_valid, reason = token_validator.validate_token(
                address=address,
                chain=chain,
                symbol=symbol,
                price=price,
                liquidity=liquidity
            )
            
            if not is_valid:
                logger.debug(f"❌ Invalid token {symbol} from GeckoTerminal: {reason}")
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain=chain,
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change_24h,
                market_cap=None
            )
        
        except Exception as e:
            logger.debug(f"Failed to parse GeckoTerminal pool: {e}")
            return None
    
    def _parse_dexscreener_pair(self, pair: Dict) -> Optional[DiscoveredToken]:
        """Parse DexScreener pair data with validation"""
        try:
            if not isinstance(pair, dict):
                return None
            
            base_token = pair.get("baseToken") or {}
            quote_token = pair.get("quoteToken") or {}
            
            quote_symbol = quote_token.get("symbol", "").upper()
            if quote_symbol not in ["USDC", "USDT", "DAI", "WETH", "ETH", "SOL", "WBNB", "BUSD"]:
                return None
            
            address = (base_token.get("address") or "").lower()
            symbol = (base_token.get("symbol") or "UNKNOWN").upper()
            
            raw_chain = (pair.get("chainId") or "").lower()
            chain_map = {
                "ethereum": "ethereum",
                "polygon": "polygon", 
                "arbitrum": "arbitrum",
                "base": "base",
                "optimism": "optimism",
                "solana": "solana",
                "bsc": "bsc",
                "bnb": "bsc",
                "eth": "ethereum"
            }
            chain = chain_map.get(raw_chain, raw_chain)
            
            if not address:
                return None
            
            price = float(pair.get("priceUsd") or 0)
            
            liquidity_data = pair.get("liquidity") or {}
            liquidity = float(liquidity_data.get("usd") or 0)
            
            volume_data = pair.get("volume") or {}
            volume = float(volume_data.get("h24") or 0)
            
            price_change = pair.get("priceChange") or {}
            change_24h = float(price_change.get("h24") or 0)
            
            market_cap = float(pair.get("fdv") or pair.get("marketCap") or 0)
            
            if price <= 0 or liquidity <= 0:
                return None
            
            # ✅ VALIDATE TOKEN BEFORE RETURNING
            is_valid, reason = token_validator.validate_token(
                address=address,
                chain=chain,
                symbol=symbol,
                price=price,
                liquidity=liquidity
            )
            
            if not is_valid:
                logger.debug(f"❌ Invalid token {symbol} from DexScreener: {reason}")
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain=chain,
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change_24h,
                market_cap=market_cap if market_cap > 0 else None
            )
        
        except Exception as e:
            logger.debug(f"Failed to parse DexScreener pair: {e}")
            return None
    
    def _filter_tokens(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Filter tokens with validation"""
        filtered = []
        
        for token in tokens:
            # Skip blacklisted
            if token.address in self._blacklist:
                continue
            
            # Skip validator-blacklisted addresses
            if token_validator.is_blacklisted(token.address):
                logger.debug(f"⚫ Skipping blacklisted: {token.symbol}")
                continue
            
            # Suspicious patterns
            suspicious = ["test", "xxx", "scam", "rug", "fake"]
            if any(p in token.symbol.lower() for p in suspicious):
                self._soft_blacklist(token.address, "suspicious_pattern")
                continue
            
            # Liquidity/volume ratio
            if token.volume_24h > 0:
                lv_ratio = token.liquidity_usd / token.volume_24h
                
                if lv_ratio < 0.02:
                    self._soft_blacklist(token.address, "wash_trading")
                    continue
                
                if lv_ratio > 50:
                    continue
            
            # Market cap minimum
            if token.market_cap and token.market_cap < 100_000:
                continue
            
            # Symbol length
            if len(token.symbol) > 15:
                continue
            
            # Price range
            if token.price <= 0 or token.price > 10_000_000:
                continue
            
            filtered.append(token)
        
        return filtered
    
    def _score_opportunities(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Score opportunities"""
        for token in tokens:
            score = 0.0
            
            # Volume surge
            volume_surge = self._calc_volume_surge(
                f"{token.symbol}_{token.chain}", 
                token.volume_24h
            )
            score += volume_surge * 1.5
            
            # Price change
            abs_change = abs(token.change_24h_pct)
            if abs_change > 2:
                score += abs_change * 0.5
            
            # Liquidity tiers
            if token.liquidity_usd > 500_000:
                score += 3.0
            elif token.liquidity_usd > 250_000:
                score += 2.0
            elif token.liquidity_usd > 100_000:
                score += 1.5
            elif token.liquidity_usd > 50_000:
                score += 1.0
            
            # Volume tiers
            if token.volume_24h > 2_000_000:
                score += 3.0
            elif token.volume_24h > 1_000_000:
                score += 2.0
            elif token.volume_24h > 500_000:
                score += 1.5
            elif token.volume_24h > 250_000:
                score += 1.0
            
            # Market cap sweet spot
            if token.market_cap:
                if 10_000_000 < token.market_cap < 500_000_000:
                    score += 3.0
                elif 1_000_000 < token.market_cap < 10_000_000:
                    score += 2.0
            
            # Historical performance
            if token.address in self._token_performance:
                perf = self._token_performance[token.address]
                win_rate = perf.get("win_rate", 0)
                if win_rate > 0.6:
                    score += 2.0
                elif win_rate < 0.3:
                    score -= 0.5
            
            token.opportunity_score = max(0.0, score)
        
        tokens.sort(key=lambda t: t.opportunity_score, reverse=True)
        
        quality_tokens = [t for t in tokens if t.opportunity_score >= 3.0]
        
        logger.info(f"   💎 {len(quality_tokens)} tokens scored >= 3.0")
        
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
            return 0.2
        
        ema_alpha = 0.3
        ema = hist[0]
        for v in list(hist)[1:]:
            ema = ema_alpha * v + (1 - ema_alpha) * ema
        
        if ema == 0:
            return 0.5
        
        ratio = volume / ema
        return max(0.0, ratio - 0.5)
    
    def blacklist_token(self, address: str, reason: str = "manual"):
        """Blacklist a token"""
        self._blacklist.add(address.lower())
        
        # Also blacklist in validator
        token_validator.record_trade_failure(address, "unknown")
        
        if address not in self._token_performance:
            self._token_performance[address] = {}
        self._token_performance[address]["blacklist_reason"] = reason
        self._token_performance[address]["blacklisted_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"🚫 Blacklisted: {address[:10]}... ({reason})")
    
    def _soft_blacklist(self, address: str, reason: str):
        """Soft blacklist with counter"""
        if address not in self._failed_trades:
            self._failed_trades[address] = 0
        self._failed_trades[address] += 1
        
        if self._failed_trades[address] >= 3:
            self.blacklist_token(address, f"auto_{reason}")
    
    def record_trade_result(
        self, 
        token_address: str, 
        token_symbol: str,
        success: bool,
        pnl_pct: Optional[float] = None
    ):
        """Record trade result for learning"""
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
            perf["losses"] += 1
            perf["consecutive_losses"] += 1
            self._soft_blacklist(address, "trade_failure")
        
        perf["win_rate"] = perf["wins"] / perf["trades"] if perf["trades"] > 0 else 0
        perf["avg_pnl"] = perf["total_pnl"] / perf["trades"] if perf["trades"] > 0 else 0
        
        # Auto-blacklist poor performers
        if perf["trades"] >= 5:
            if perf["win_rate"] < 0.25:
                self.blacklist_token(address, f"low_winrate_{perf['win_rate']*100:.0f}%")
            elif perf["consecutive_losses"] >= 5:
                self.blacklist_token(address, f"consecutive_losses")
            elif perf["avg_pnl"] < -5:
                self.blacklist_token(address, f"avg_loss_{perf['avg_pnl']:.1f}%")
    
    def get_discovered_tokens(self) -> Dict[str, DiscoveredToken]:
        """Get all discovered tokens"""
        return self._discovered_tokens
    
    def get_top_opportunities(self, n: int = 10) -> List[DiscoveredToken]:
        """Get top N opportunities"""
        tokens = sorted(
            self._discovered_tokens.values(),
            key=lambda t: t.opportunity_score,
            reverse=True
        )
        return tokens[:n]


# Export
MarketScanner = MultiAPIMarketScanner