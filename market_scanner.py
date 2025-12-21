"""
Enhanced Market Scanner - FIXED VERSION
✅ Added circuit breakers for all external APIs
✅ Added proper rate limiting per API
✅ Better error handling and recovery
"""
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
from collections import deque
from aiohttp import ClientTimeout, TCPConnector

from config import config
from models import (
    DiscoveredToken, CircuitBreakerOpenError, CircuitState
)
from logging_manager import get_logger
from token_validator import token_validator

logger = get_logger("MarketScanner")


class RateLimiter:
    """✅ NEW: Rate limiter for API calls"""
    
    def __init__(self, requests_per_minute: int = 30):
        self.requests_per_minute = requests_per_minute
        self.interval = 60.0 / requests_per_minute  # Seconds between requests
        self.last_request: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, key: str = "default"):
        """Wait until we can make a request"""
        async with self._lock:
            now = datetime.now(timezone.utc)
            last = self.last_request.get(key)
            
            if last:
                elapsed = (now - last).total_seconds()
                if elapsed < self.interval:
                    wait_time = self.interval - elapsed
                    logger.debug(f"⏳ Rate limit: waiting {wait_time:.2f}s for {key}")
                    await asyncio.sleep(wait_time)
            
            self.last_request[key] = datetime.now(timezone.utc)


class CircuitBreaker:
    """Circuit breaker for API resilience"""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        name: str = "default"
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.success_count = 0
        self._lock = asyncio.Lock()
    
    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    logger.info(f"🔄 Circuit breaker '{self.name}' entering HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Retry after {self._time_until_reset():.0f}s"
                    )
        
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    def _time_until_reset(self) -> float:
        if self.last_failure_time is None:
            return 0
        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        return max(0, self.recovery_timeout - elapsed)
    
    async def _on_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= 2:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    logger.info(f"✅ Circuit breaker '{self.name}' CLOSED (recovered)")
            else:
                self.failure_count = 0
    
    async def _on_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now(timezone.utc)
            self.success_count = 0
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(
                    f"⚠️ Circuit breaker '{self.name}' OPEN after {self.failure_count} failures"
                )


class MultiAPIMarketScanner:
    """
    ✅ FIXED: Enhanced scanner with circuit breakers and rate limiting
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
        
        self._semaphore = asyncio.Semaphore(10)  # ✅ Reduced from 15 to 10
        
        # ✅ NEW: Circuit breakers for each API
        self._circuit_breakers = {
            "dexscreener": CircuitBreaker(
                failure_threshold=5,
                recovery_timeout=120,  # 2 minutes
                name="DexScreener"
            ),
            "geckoterminal": CircuitBreaker(
                failure_threshold=5,
                recovery_timeout=120,
                name="GeckoTerminal"
            )
        }
        
        # ✅ NEW: Rate limiters for each API
        self._rate_limiters = {
            "dexscreener": RateLimiter(requests_per_minute=30),
            "geckoterminal": RateLimiter(requests_per_minute=20)
        }
        
        # ✅ NEW: API health tracking
        self._api_health = {
            "dexscreener": {"available": True, "last_success": None, "consecutive_failures": 0},
            "geckoterminal": {"available": True, "last_success": None, "consecutive_failures": 0}
        }
        
        logger.info("🔍 Multi-API Market Scanner initialized")
        logger.info("   ✅ DexScreener: Latest + Boosted + Search + By Chain")
        logger.info("   ✅ GeckoTerminal: Trending pools")
        logger.info("   ✅ Token Validator: Address validation enabled")
        logger.info("   ✅ Circuit breakers: ENABLED")
        logger.info("   ✅ Rate limiting: ENABLED")
        logger.info("   ✅ Auto-blacklist learning enabled")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=10,  # ✅ Reduced from 30
                limit_per_host=5,  # ✅ Reduced from 15
                ttl_dns_cache=300
            )
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=15),  # ✅ Reduced from 20
                connector=connector
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def scan_market(
        self, 
        chains: Optional[List[str]] = None,
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None
    ) -> List[DiscoveredToken]:
        """✅ FIXED: Market scan with circuit breaker protection"""
        
        if chains is None:
            chains = ["ethereum", "polygon", "arbitrum", "base", "optimism", "solana", "bsc"]
        
        global_min_liquidity = min_liquidity or config.MIN_LIQUIDITY_USD
        global_min_volume = min_volume or config.MIN_VOLUME_24H_USD
        
        logger.info(f"🔍 Multi-API scan across {len(chains)} chains...")
        logger.info(f"   Min Liquidity: ${global_min_liquidity:,.0f}")
        logger.info(f"   Min Volume: ${global_min_volume:,.0f}")
        logger.info(f"   Blacklist: {len(self._blacklist)} tokens")
        
        # ✅ NEW: Log API health
        self._log_api_health()
        
        all_tokens = []
        tasks = []
        
        # Only add tasks for healthy APIs
        if self._is_api_healthy("dexscreener"):
            tasks.append(self._scan_with_circuit_breaker("dexscreener", self._scan_dexscreener_boosted))
            tasks.append(self._scan_with_circuit_breaker("dexscreener", self._scan_dexscreener_search_top))
            
            for chain in ["ethereum", "polygon", "arbitrum", "base", "optimism", "solana", "bsc"]:
                if chain in chains:
                    tasks.append(self._scan_with_circuit_breaker("dexscreener", self._scan_dexscreener_by_chain, chain))
        else:
            logger.warning("⚠️ DexScreener circuit breaker OPEN - skipping")
        
        if self._is_api_healthy("geckoterminal"):
            for chain in ["eth", "polygon_pos", "arbitrum_one", "base", "optimism", "solana", "bsc"]:
                tasks.append(self._scan_with_circuit_breaker("geckoterminal", self._scan_geckoterminal_trending, chain))
        else:
            logger.warning("⚠️ GeckoTerminal circuit breaker OPEN - skipping")
        
        # Execute all tasks
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, CircuitBreakerOpenError):
                logger.debug(f"Circuit breaker prevented call: {result}")
                continue
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
    
    async def _scan_with_circuit_breaker(self, api_name: str, func, *args, **kwargs):
        """✅ NEW: Wrap API call with circuit breaker and rate limiter"""
        try:
            # Rate limiting
            await self._rate_limiters[api_name].acquire(func.__name__)
            
            # Circuit breaker
            result = await self._circuit_breakers[api_name].call(func, *args, **kwargs)
            
            # ✅ Record success
            self._api_health[api_name]["available"] = True
            self._api_health[api_name]["last_success"] = datetime.now(timezone.utc)
            self._api_health[api_name]["consecutive_failures"] = 0
            
            return result
            
        except CircuitBreakerOpenError as e:
            # Circuit breaker open - don't count as failure
            raise
        except Exception as e:
            # ✅ Record failure
            self._api_health[api_name]["consecutive_failures"] += 1
            logger.warning(f"⚠️ {api_name} call failed: {e}")
            
            if self._api_health[api_name]["consecutive_failures"] >= 3:
                self._api_health[api_name]["available"] = False
                logger.error(f"❌ {api_name} marked as unavailable after 3 failures")
            
            raise
    
    def _is_api_healthy(self, api_name: str) -> bool:
        """✅ NEW: Check if API is healthy"""
        health = self._api_health.get(api_name, {})
        
        # Check circuit breaker state
        breaker = self._circuit_breakers.get(api_name)
        if breaker and breaker.state == CircuitState.OPEN:
            return False
        
        # Check consecutive failures
        if health.get("consecutive_failures", 0) >= 3:
            # But allow recovery after 5 minutes
            last_success = health.get("last_success")
            if last_success:
                age = (datetime.now(timezone.utc) - last_success).total_seconds()
                if age > 300:  # 5 minutes
                    logger.info(f"🔄 {api_name} recovery timeout passed, allowing retry")
                    health["consecutive_failures"] = 0
                    return True
            return False
        
        return health.get("available", True)
    
    def _log_api_health(self):
        """✅ NEW: Log current API health status"""
        for api_name, health in self._api_health.items():
            breaker = self._circuit_breakers.get(api_name)
            state = breaker.state.name if breaker else "UNKNOWN"
            failures = health.get("consecutive_failures", 0)
            
            status = "🟢" if health.get("available") else "🔴"
            logger.debug(f"   {status} {api_name}: {state}, failures: {failures}")
    
    async def _scan_dexscreener_boosted(self) -> List[DiscoveredToken]:
        """Get boosted tokens from DexScreener - FIXED"""
        try:
            session = await self._get_session()
            url = f"{self.DEXSCREENER_BASE}/token-boosts/latest/v1"
            
            async with self._semaphore:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.debug(f"   DexScreener boosted: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    # ✅ FIX: The API returns a list directly, not a dict
                    if not data or not isinstance(data, list):
                        logger.debug(f"   DexScreener boosted: unexpected format")
                        return []
                    
                    tokens = []
                    
                    # Process each boosted token in the list
                    for boost_item in data[:10]:  # Limit to 10 items
                        if not isinstance(boost_item, dict):
                            continue
                        
                        chain_id = boost_item.get("chainId")
                        token_address = boost_item.get("tokenAddress")
                        
                        if not chain_id or not token_address:
                            continue
                        
                        # Now fetch pairs for this token
                        pairs_url = f"{self.DEXSCREENER_BASE}/latest/dex/tokens/{token_address}"
                        
                        try:
                            async with session.get(pairs_url) as pairs_resp:
                                if pairs_resp.status != 200:
                                    continue
                                
                                pairs_data = await pairs_resp.json()
                                pairs = pairs_data.get("pairs", [])
                                
                                for pair in pairs[:5]:  # Limit to 5 pairs per token
                                    token = self._parse_dexscreener_pair(pair)
                                    if token:
                                        token.opportunity_score += 3.0  # Boost score
                                        tokens.append(token)
                            
                            await asyncio.sleep(0.2)  # Rate limit between token lookups
                            
                        except Exception as e:
                            logger.debug(f"   Failed to fetch pairs for {token_address}: {e}")
                            continue
                    
                    logger.info(f"   DexScreener Boosted: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.debug(f"   DexScreener boosted failed: {e}")
            raise  # Re-raise for circuit breaker
        
    async def _scan_dexscreener_search_top(self) -> List[DiscoveredToken]:
        """Search DexScreener for top volume tokens"""
        try:
            session = await self._get_session()
            
            search_terms = ["ETH", "BTC", "SOL", "USDC", "PEPE"]
            all_tokens = []
            seen = set()
            
            for term in search_terms:
                try:
                    url = f"{self.DEXSCREENER_BASE}/latest/dex/search"
                    params = {"q": term}
                    
                    async with self._semaphore:
                        async with session.get(url, params=params) as resp:
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
            raise
    
    async def _scan_dexscreener_by_chain(self, chain: str) -> List[DiscoveredToken]:
        """Scan DexScreener by specific chain"""
        try:
            session = await self._get_session()
            
            chain_config = self.CHAIN_CONFIGS.get(chain, {})
            dex_chain_id = chain_config.get("dex_id", chain)
            
            top_tokens = {
                "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
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
                async with session.get(url) as resp:
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
            raise
    
    async def _scan_geckoterminal_trending(self, network: str) -> List[DiscoveredToken]:
        """Scan GeckoTerminal trending pools"""
        try:
            session = await self._get_session()
            url = f"{self.GECKOTERMINAL_BASE}/networks/{network}/trending_pools"
            
            async with self._semaphore:
                async with session.get(url) as resp:
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
            raise
    
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
            
            # ✅ VALIDATE during parsing (not after)
            is_valid, reason = token_validator.validate_token(
                address=address,
                chain=chain,
                symbol=symbol,
                price=price,
                liquidity=liquidity
            )
            
            if not is_valid:
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
            
            # ✅ VALIDATE during parsing
            is_valid, reason = token_validator.validate_token(
                address=address,
                chain=chain,
                symbol=symbol,
                price=price,
                liquidity=liquidity
            )
            
            if not is_valid:
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
        """Filter tokens"""
        filtered = []
        
        for token in tokens:
            if token.address in self._blacklist:
                continue
            
            if token_validator.is_blacklisted(token.address):
                continue
            
            suspicious = ["test", "xxx", "scam", "rug", "fake"]
            if any(p in token.symbol.lower() for p in suspicious):
                self._soft_blacklist(token.address, "suspicious_pattern")
                continue
            
            if token.volume_24h > 0:
                lv_ratio = token.liquidity_usd / token.volume_24h
                
                if lv_ratio < 0.02:
                    self._soft_blacklist(token.address, "wash_trading")
                    continue
                
                if lv_ratio > 50:
                    continue
            
            if token.market_cap and token.market_cap < 100_000:
                continue
            
            if len(token.symbol) > 15:
                continue
            
            if token.price <= 0 or token.price > 10_000_000:
                continue
            
            filtered.append(token)
        
        return filtered
    
    def _score_opportunities(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Score opportunities"""
        for token in tokens:
            score = 0.0
            
            volume_surge = self._calc_volume_surge(
                f"{token.symbol}_{token.chain}", 
                token.volume_24h
            )
            score += volume_surge * 1.5
            
            abs_change = abs(token.change_24h_pct)
            if abs_change > 2:
                score += abs_change * 0.5
            
            if token.liquidity_usd > 500_000:
                score += 3.0
            elif token.liquidity_usd > 250_000:
                score += 2.0
            elif token.liquidity_usd > 100_000:
                score += 1.5
            elif token.liquidity_usd > 50_000:
                score += 1.0
            
            if token.volume_24h > 2_000_000:
                score += 3.0
            elif token.volume_24h > 1_000_000:
                score += 2.0
            elif token.volume_24h > 500_000:
                score += 1.5
            elif token.volume_24h > 250_000:
                score += 1.0
            
            if token.market_cap:
                if 10_000_000 < token.market_cap < 500_000_000:
                    score += 3.0
                elif 1_000_000 < token.market_cap < 10_000_000:
                    score += 2.0
            
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
    
    def get_health_status(self) -> Dict:
        """✅ NEW: Get scanner health status"""
        return {
            "api_health": {
                api: {
                    "available": health["available"],
                    "consecutive_failures": health["consecutive_failures"],
                    "circuit_breaker": self._circuit_breakers[api].state.name,
                    "last_success": health["last_success"].isoformat() if health["last_success"] else None
                }
                for api, health in self._api_health.items()
            },
            "discovered_tokens": len(self._discovered_tokens),
            "blacklisted_tokens": len(self._blacklist)
        }


# Export
MarketScanner = MultiAPIMarketScanner