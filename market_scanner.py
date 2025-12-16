"""
Enhanced Self-Thinking Market Scanner
Uses multiple free APIs for comprehensive token discovery
- DexScreener (trending + search)
- CoinGecko (top gainers/losers)
- Birdeye (Solana tokens)
- DEX aggregators
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

logger = get_logger("MarketScanner")


class MultiAPIMarketScanner:
    """
    Advanced market scanner using multiple free APIs
    Discovers hundreds of tokens across chains
    """
    
    # API endpoints
    DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"
    COINGECKO_BASE = "https://api.coingecko.com/api/v3"
    BIRDEYE_BASE = "https://public-api.birdeye.so"
    
    # Chain-specific configs
    CHAIN_CONFIGS = {
        "ethereum": {
            "min_liquidity": 500_000,
            "min_volume": 1_000_000,
            "gas_cost_weight": 1.0,
            "quality_bonus": 1.5
        },
        "polygon": {
            "min_liquidity": 100_000,
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
            "min_liquidity": 100_000,
            "min_volume": 200_000,
            "gas_cost_weight": 0.15,
            "quality_bonus": 0.9
        },
        "optimism": {
            "min_liquidity": 150_000,
            "min_volume": 300_000,
            "gas_cost_weight": 0.2,
            "quality_bonus": 1.1
        },
        "solana": {
            "min_liquidity": 50_000,
            "min_volume": 100_000,
            "gas_cost_weight": 0.05,
            "quality_bonus": 1.0
        },
        "bsc": {
            "min_liquidity": 80_000,
            "min_volume": 150_000,
            "gas_cost_weight": 0.08,
            "quality_bonus": 0.8
        }
    }
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, any] = {}
        self._discovered_tokens: Dict[str, DiscoveredToken] = {}
        self._volume_history: Dict[str, deque] = {}
        
        # Auto-blacklist system
        self._blacklist: Set[str] = set()
        self._token_performance: Dict[str, Dict] = {}
        self._failed_trades: Dict[str, int] = {}
        
        # Rate limiting
        self._semaphore = asyncio.Semaphore(15)
        self._last_request_time = {}
        
        logger.info("🔍 Multi-API Market Scanner initialized")
        logger.info("   ✅ DexScreener: Trending + Search")
        logger.info("   ✅ CoinGecko: Top Gainers/Losers")
        logger.info("   ✅ Birdeye: Solana Tokens")
        logger.info("   ✅ Auto-blacklist learning enabled")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
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
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _rate_limit(self, api_name: str, delay: float = 0.5):
        """Rate limiting per API"""
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
        """
        Comprehensive market scan using multiple APIs
        """
        if chains is None:
            chains = ["ethereum", "polygon", "arbitrum", "base", "optimism", "solana"]
        
        # LOWERED thresholds for more opportunities
        global_min_liquidity = min_liquidity or config.MIN_LIQUIDITY_USD
        global_min_volume = min_volume or config.MIN_VOLUME_24H_USD
        
        logger.info(f"🔍 Multi-API scan across {len(chains)} chains...")
        logger.info(f"   Min Liquidity: ${global_min_liquidity:,.0f}")
        logger.info(f"   Min Volume: ${global_min_volume:,.0f}")
        logger.info(f"   Blacklist: {len(self._blacklist)} tokens")
        
        all_tokens = []
        
        # Parallel API calls
        tasks = []
        
        # 1. DexScreener - Use profiles endpoint (better for discovery)
        for chain in ["ethereum", "polygon", "arbitrum", "base", "optimism", "bsc"]:
            if chain in chains:
                tasks.append(self._scan_dexscreener_profiles(chain))
        
        # 2. CoinGecko top gainers/losers
        tasks.append(self._scan_coingecko_gainers())
        tasks.append(self._scan_coingecko_losers())
        
        # 3. DexScreener trending (as fallback)
        tasks.append(self._scan_dexscreener_trending())
        
        # Execute all tasks
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect results
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"API call failed: {result}")
                continue
            if isinstance(result, list):
                all_tokens.extend(result)
        
        logger.info(f"   📊 Raw tokens discovered: {len(all_tokens)}")
        
        # Deduplicate by address
        unique_tokens = {}
        for token in all_tokens:
            key = f"{token.address}_{token.chain}"
            if key not in unique_tokens:
                unique_tokens[key] = token
            else:
                # Keep higher scored version
                if token.opportunity_score > unique_tokens[key].opportunity_score:
                    unique_tokens[key] = token
        
        all_tokens = list(unique_tokens.values())
        logger.info(f"   🔄 After deduplication: {len(all_tokens)}")
        
        # Apply chain quality bonus
        for token in all_tokens:
            chain_config = self.CHAIN_CONFIGS.get(token.chain, {})
            token.opportunity_score *= chain_config.get("quality_bonus", 1.0)
        
        # Filter and score
        filtered_tokens = self._filter_tokens(all_tokens)
        logger.info(f"   ✅ After filtering: {len(filtered_tokens)}")
        
        scored_tokens = self._score_opportunities(filtered_tokens)
        logger.info(f"   💎 High quality tokens: {len(scored_tokens)}")
        
        # Update cache
        for token in scored_tokens:
            self._discovered_tokens[f"{token.symbol}_{token.chain}"] = token
        
        logger.info(f"✅ Multi-API scan complete: {len(scored_tokens)} opportunities")
        
        return scored_tokens
    
    async def _scan_dexscreener_trending(self) -> List[DiscoveredToken]:
        """Scan DexScreener trending tokens (cross-chain)"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            # Try multiple endpoints
            urls = [
                f"{self.DEXSCREENER_BASE}/tokens/trending",
                f"{self.DEXSCREENER_BASE}/tokens/hot"
            ]
            
            for url in urls:
                try:
                    async with self._semaphore:
                        async with session.get(url, timeout=15) as resp:
                            if resp.status != 200:
                                continue
                            
                            data = await resp.json()
                            
                            # Handle different response formats
                            pairs = None
                            if isinstance(data, dict):
                                pairs = data.get("pairs") or data.get("data") or data.get("tokens")
                            elif isinstance(data, list):
                                pairs = data
                            
                            if not pairs:
                                continue
                            
                            tokens = []
                            for pair in pairs[:50]:  # Top 50 trending
                                token = self._parse_dexscreener_pair(pair)
                                if token:
                                    tokens.append(token)
                            
                            if tokens:
                                logger.info(f"   DexScreener Trending: {len(tokens)} tokens")
                                return tokens
                
                except Exception as e:
                    logger.debug(f"   Trending endpoint {url} failed: {e}")
                    continue
            
            logger.info("   DexScreener Trending: No data from any endpoint")
            return []
        
        except Exception as e:
            logger.warning(f"   DexScreener trending failed: {e}")
            return []
    
    async def _scan_dexscreener_profiles(self, chain: str) -> List[DiscoveredToken]:
        """Scan DexScreener profiles endpoint (better volume data)"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            # Use profiles endpoint with boosted flag
            url = f"{self.DEXSCREENER_BASE}/profiles/latest/dex/tokens"
            
            async with self._semaphore:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   DexScreener profiles {chain}: HTTP {resp.status}")
                        # Fallback to chain search
                        return await self._scan_dexscreener_chain(
                            chain, 
                            config.MIN_LIQUIDITY_USD,
                            config.MIN_VOLUME_24H_USD
                        )
                    
                    data = await resp.json()
                    
                    # Parse profiles
                    pairs = []
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                item_pairs = item.get("pairs", [])
                                if isinstance(item_pairs, list):
                                    pairs.extend(item_pairs)
                    elif isinstance(data, dict):
                        pairs = data.get("pairs", [])
                    
                    tokens = []
                    seen = set()
                    
                    for pair in pairs:
                        try:
                            # Filter by chain
                            pair_chain = pair.get("chainId", "").lower()
                            if pair_chain != chain:
                                continue
                            
                            token = self._parse_dexscreener_pair(pair)
                            if token and token.address not in seen:
                                tokens.append(token)
                                seen.add(token.address)
                        
                        except Exception as e:
                            logger.debug(f"Failed to parse profile pair: {e}")
                            continue
                    
                    logger.info(f"   DexScreener {chain}: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.debug(f"   DexScreener profiles {chain} failed: {e}")
            # Fallback to chain search
            return await self._scan_dexscreener_chain(
                chain,
                config.MIN_LIQUIDITY_USD, 
                config.MIN_VOLUME_24H_USD
            )
    
    async def _scan_dexscreener_chain(
        self, 
        chain: str, 
        min_liquidity: float, 
        min_volume: float
    ) -> List[DiscoveredToken]:
        """Scan specific chain on DexScreener"""
        try:
            await self._rate_limit("dexscreener", 1.0)
            session = await self._get_session()
            
            # Try both search and chain-specific endpoints
            urls_params = [
                (f"{self.DEXSCREENER_BASE}/search", {"q": chain}),
                (f"{self.DEXSCREENER_BASE}/tokens/{chain}", None)
            ]
            
            for url, params in urls_params:
                try:
                    async with self._semaphore:
                        async with session.get(url, params=params, timeout=15) as resp:
                            if resp.status != 200:
                                continue
                            
                            data = await resp.json()
                            
                            # Handle different response formats
                            pairs = None
                            if isinstance(data, dict):
                                pairs = data.get("pairs") or data.get("data") or data.get("tokens")
                            elif isinstance(data, list):
                                pairs = data
                            
                            if not pairs:
                                continue
                            
                            tokens = []
                            seen = set()
                            
                            for pair in pairs[:100]:  # Top 100 per chain
                                try:
                                    # Flexible chain matching
                                    chain_id = ""
                                    if isinstance(pair, dict):
                                        chain_id = pair.get("chainId", pair.get("chain", "")).lower()
                                    
                                    # Accept if chain matches or no chain specified
                                    if chain_id and chain_id != chain.lower():
                                        continue
                                    
                                    token = self._parse_dexscreener_pair(pair)
                                    if token and token.address not in seen:
                                        if (token.liquidity_usd >= min_liquidity and 
                                            token.volume_24h >= min_volume):
                                            tokens.append(token)
                                            seen.add(token.address)
                                
                                except Exception as e:
                                    logger.debug(f"Failed to parse pair: {e}")
                                    continue
                            
                            if tokens:
                                logger.info(f"   DexScreener {chain}: {len(tokens)} tokens")
                                return tokens
                
                except Exception as e:
                    logger.debug(f"   DexScreener chain endpoint failed: {e}")
                    continue
            
            logger.info(f"   DexScreener {chain}: No data from any endpoint")
            return []
        
        except Exception as e:
            logger.warning(f"   DexScreener {chain} failed: {e}")
            return []
    
    def _parse_dexscreener_pair(self, pair: Dict) -> Optional[DiscoveredToken]:
        """Parse DexScreener pair data with robust error handling"""
        try:
            if not isinstance(pair, dict):
                return None
            
            # Extract base token (flexible field names)
            base_token = pair.get("baseToken") or pair.get("token") or {}
            quote_token = pair.get("quoteToken") or pair.get("quoteToken") or {}
            
            # Quote token validation
            quote_symbol = quote_token.get("symbol", "").upper()
            if quote_symbol not in ["USDC", "USDT", "DAI", "WETH", "ETH", "SOL", "WBNB", "BUSD"]:
                return None
            
            # Extract required fields
            address = (base_token.get("address") or pair.get("address") or "").lower()
            symbol = (base_token.get("symbol") or pair.get("symbol") or "UNKNOWN").upper()
            chain = (pair.get("chainId") or pair.get("chain") or "ethereum").lower()
            
            if not address or address == "":
                return None
            
            # Price extraction (multiple possible locations)
            price = 0.0
            price_fields = [
                pair.get("priceUsd"),
                pair.get("price"),
                base_token.get("priceUsd"),
                base_token.get("price")
            ]
            for p in price_fields:
                if p is not None:
                    try:
                        price = float(p)
                        if price > 0:
                            break
                    except (ValueError, TypeError):
                        continue
            
            # Liquidity extraction
            liquidity = 0.0
            liquidity_data = pair.get("liquidity") or {}
            if isinstance(liquidity_data, dict):
                liquidity = float(liquidity_data.get("usd") or liquidity_data.get("value") or 0)
            elif isinstance(liquidity_data, (int, float, str)):
                try:
                    liquidity = float(liquidity_data)
                except (ValueError, TypeError):
                    pass
            
            # Volume extraction
            volume = 0.0
            volume_data = pair.get("volume") or {}
            if isinstance(volume_data, dict):
                volume = float(volume_data.get("h24") or volume_data.get("24h") or 0)
            elif isinstance(volume_data, (int, float, str)):
                try:
                    volume = float(volume_data)
                except (ValueError, TypeError):
                    pass
            
            # Price change extraction
            change_24h = 0.0
            price_change = pair.get("priceChange") or {}
            if isinstance(price_change, dict):
                change_24h = float(price_change.get("h24") or price_change.get("24h") or 0)
            elif isinstance(price_change, (int, float, str)):
                try:
                    change_24h = float(price_change)
                except (ValueError, TypeError):
                    pass
            
            # Market cap (optional)
            market_cap = None
            mc_fields = [pair.get("fdv"), pair.get("marketCap"), pair.get("mcap")]
            for mc in mc_fields:
                if mc is not None:
                    try:
                        market_cap = float(mc)
                        if market_cap > 0:
                            break
                    except (ValueError, TypeError):
                        continue
            
            # Validation
            if price <= 0 or liquidity <= 0:
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain=chain,
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change_24h,
                market_cap=market_cap
            )
        
        except Exception as e:
            logger.debug(f"Failed to parse DexScreener pair: {e}")
            return None
    
    async def _scan_coingecko_gainers(self) -> List[DiscoveredToken]:
        """Scan CoinGecko top gainers (24h)"""
        try:
            await self._rate_limit("coingecko", 2.0)
            session = await self._get_session()
            
            url = f"{self.COINGECKO_BASE}/coins/markets"
            params = {
                "vs_currency": "usd",
                "order": "price_change_percentage_24h_desc",
                "per_page": 100,
                "page": 1,
                "sparkline": "false"
            }
            
            async with self._semaphore:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   CoinGecko gainers: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    if not isinstance(data, list):
                        logger.debug(f"   CoinGecko gainers: Unexpected response format")
                        return []
                    
                    tokens = []
                    for coin in data:
                        try:
                            token = self._parse_coingecko_coin(coin)
                            if token:
                                tokens.append(token)
                        except Exception as e:
                            logger.debug(f"Failed to parse CoinGecko coin: {e}")
                            continue
                    
                    logger.info(f"   CoinGecko Gainers: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.warning(f"   CoinGecko gainers failed: {e}")
            return []
    
    async def _scan_coingecko_losers(self) -> List[DiscoveredToken]:
        """Scan CoinGecko top losers (24h) for mean reversion"""
        try:
            await self._rate_limit("coingecko", 2.0)
            session = await self._get_session()
            
            url = f"{self.COINGECKO_BASE}/coins/markets"
            params = {
                "vs_currency": "usd",
                "order": "price_change_percentage_24h_asc",
                "per_page": 50,
                "page": 1,
                "sparkline": "false"
            }
            
            async with self._semaphore:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   CoinGecko losers: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    if not isinstance(data, list):
                        logger.debug(f"   CoinGecko losers: Unexpected response format")
                        return []
                    
                    tokens = []
                    for coin in data:
                        try:
                            # Only moderate dips (not crashes)
                            change = coin.get("price_change_percentage_24h")
                            if change is None:
                                continue
                            
                            change_val = float(change)
                            if -15 < change_val < -3:  # -15% to -3%
                                token = self._parse_coingecko_coin(coin)
                                if token:
                                    tokens.append(token)
                        except Exception as e:
                            logger.debug(f"Failed to parse CoinGecko coin: {e}")
                            continue
                    
                    logger.info(f"   CoinGecko Losers: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.warning(f"   CoinGecko losers failed: {e}")
            return []
    
    def _parse_coingecko_coin(self, coin: Dict) -> Optional[DiscoveredToken]:
        """Parse CoinGecko coin data with robust error handling"""
        try:
            if not isinstance(coin, dict):
                return None
            
            symbol = (coin.get("symbol") or "").upper()
            if not symbol:
                return None
            
            # Try to find contract address
            platforms = coin.get("platforms") or {}
            address = None
            chain = "ethereum"
            
            # Chain priority: Ethereum > Polygon > Arbitrum > Optimism > Base
            chain_mappings = [
                ("ethereum", "ethereum"),
                ("polygon-pos", "polygon"),
                ("arbitrum-one", "arbitrum"),
                ("optimistic-ethereum", "optimism"),
                ("base", "base"),
                ("binance-smart-chain", "bsc")
            ]
            
            for platform_key, chain_name in chain_mappings:
                if platform_key in platforms and platforms[platform_key]:
                    addr = platforms[platform_key]
                    if addr and isinstance(addr, str) and addr.strip():
                        address = addr.lower()
                        chain = chain_name
                        break
            
            if not address:
                return None
            
            # Extract metrics with safe conversions
            price = 0.0
            try:
                price = float(coin.get("current_price") or 0)
            except (ValueError, TypeError):
                return None
            
            market_cap = 0.0
            try:
                market_cap = float(coin.get("market_cap") or 0)
            except (ValueError, TypeError):
                pass
            
            volume = 0.0
            try:
                volume = float(coin.get("total_volume") or 0)
            except (ValueError, TypeError):
                pass
            
            change_24h = 0.0
            try:
                change_24h = float(coin.get("price_change_percentage_24h") or 0)
            except (ValueError, TypeError):
                pass
            
            # Estimate liquidity (10% of market cap or 2x volume)
            liquidity = 0.0
            if market_cap > 0:
                liquidity = market_cap * 0.1
            elif volume > 0:
                liquidity = volume * 2
            
            if price <= 0 or liquidity <= 0:
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
            logger.debug(f"Failed to parse CoinGecko coin: {e}")
            return None
    
    async def _scan_birdeye_solana(self) -> List[DiscoveredToken]:
        """Scan Birdeye for Solana tokens"""
        try:
            await self._rate_limit("birdeye", 1.5)
            session = await self._get_session()
            
            # Birdeye trending tokens
            url = f"{self.BIRDEYE_BASE}/defi/tokenlist"
            params = {
                "sort_by": "v24hUSD",
                "sort_type": "desc",
                "offset": 0,
                "limit": 50
            }
            
            async with self._semaphore:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   Birdeye Solana: HTTP {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    # Handle different response formats
                    tokens_data = []
                    if isinstance(data, dict):
                        tokens_data = (
                            data.get("data", {}).get("tokens", []) or
                            data.get("tokens", []) or
                            []
                        )
                    elif isinstance(data, list):
                        tokens_data = data
                    
                    if not tokens_data:
                        logger.debug(f"   Birdeye Solana: No tokens in response")
                        return []
                    
                    tokens = []
                    for token_data in tokens_data:
                        try:
                            token = self._parse_birdeye_token(token_data)
                            if token:
                                tokens.append(token)
                        except Exception as e:
                            logger.debug(f"Failed to parse Birdeye token: {e}")
                            continue
                    
                    logger.info(f"   Birdeye Solana: {len(tokens)} tokens")
                    return tokens
        
        except Exception as e:
            logger.warning(f"   Birdeye Solana failed: {e}")
            return []
    
    def _parse_birdeye_token(self, token_data: Dict) -> Optional[DiscoveredToken]:
        """Parse Birdeye token data with robust error handling"""
        try:
            if not isinstance(token_data, dict):
                return None
            
            address = (token_data.get("address") or "").lower()
            symbol = (token_data.get("symbol") or "UNKNOWN").upper()
            
            if not address:
                return None
            
            # Safe conversions
            price = 0.0
            try:
                price = float(token_data.get("price") or 0)
            except (ValueError, TypeError):
                return None
            
            volume = 0.0
            try:
                volume = float(token_data.get("v24hUSD") or token_data.get("volume24h") or 0)
            except (ValueError, TypeError):
                pass
            
            liquidity = 0.0
            try:
                liquidity = float(token_data.get("liquidity") or 0)
            except (ValueError, TypeError):
                pass
            
            change_24h = 0.0
            try:
                change_24h = float(token_data.get("priceChange24h") or token_data.get("change24h") or 0)
            except (ValueError, TypeError):
                pass
            
            market_cap = None
            try:
                mc_val = token_data.get("mc") or token_data.get("marketCap")
                if mc_val:
                    market_cap = float(mc_val)
                    if market_cap <= 0:
                        market_cap = None
            except (ValueError, TypeError):
                pass
            
            if price <= 0 or volume <= 0:
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain="solana",
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change_24h,
                market_cap=market_cap
            )
        
        except Exception as e:
            logger.debug(f"Failed to parse Birdeye token: {e}")
            return None
    
    def _filter_tokens(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Apply filters with auto-blacklist learning"""
        filtered = []
        
        for token in tokens:
            # Skip blacklisted
            if token.address in self._blacklist:
                continue
            
            # Scam detection
            suspicious = [
                "test", "xxx", "scam", "rug", "fake",
                "elon", "moon", "safe"
            ]
            if any(p in token.symbol.lower() for p in suspicious):
                self._soft_blacklist(token.address, "suspicious_pattern")
                continue
            
            # Liquidity/volume ratio check
            if token.volume_24h > 0:
                lv_ratio = token.liquidity_usd / token.volume_24h
                
                if lv_ratio < 0.05:  # Wash trading
                    self._soft_blacklist(token.address, "wash_trading")
                    continue
                
                if lv_ratio > 20:  # Stale pool
                    continue
            
            # Market cap check
            if token.market_cap and token.market_cap < 500_000:
                continue
            
            # Symbol length
            if len(token.symbol) > 10:
                continue
            
            # Price sanity
            if token.price <= 0 or token.price > 1_000_000:
                continue
            
            filtered.append(token)
        
        return filtered
    
    def _score_opportunities(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """Score tokens with comprehensive metrics"""
        for token in tokens:
            score = 0.0
            
            # Volume surge
            volume_surge = self._calc_volume_surge(
                f"{token.symbol}_{token.chain}", 
                token.volume_24h
            )
            score += volume_surge * 1.5
            
            # Price momentum
            abs_change = abs(token.change_24h_pct)
            if abs_change > 3:
                score += abs_change * 0.4
            
            # Liquidity tiers
            if token.liquidity_usd > 500_000:
                score += 3.0
            elif token.liquidity_usd > 250_000:
                score += 2.0
            elif token.liquidity_usd > 100_000:
                score += 1.0
            
            # Volume tiers
            if token.volume_24h > 2_000_000:
                score += 3.0
            elif token.volume_24h > 1_000_000:
                score += 2.0
            elif token.volume_24h > 500_000:
                score += 1.0
            
            # Market cap sweet spot
            if token.market_cap:
                if 20_000_000 < token.market_cap < 500_000_000:
                    score += 3.0
                elif 5_000_000 < token.market_cap < 20_000_000:
                    score += 1.5
            
            # Performance history bonus
            if token.address in self._token_performance:
                perf = self._token_performance[token.address]
                win_rate = perf.get("win_rate", 0)
                if win_rate > 0.6:
                    score += 2.0
                elif win_rate < 0.3:
                    score -= 1.0
            
            token.opportunity_score = max(0.0, score)
        
        # Sort and filter - LOWERED threshold from 6.0 to 4.0
        tokens.sort(key=lambda t: t.opportunity_score, reverse=True)
        quality_tokens = [t for t in tokens if t.opportunity_score >= 4.0]
        
        logger.info(f"   💎 {len(quality_tokens)} tokens scored >= 4.0")
        
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
        
        # EMA calculation
        ema_alpha = 0.3
        ema = hist[0]
        for v in list(hist)[1:]:
            ema = ema_alpha * v + (1 - ema_alpha) * ema
        
        if ema == 0:
            return 0.5
        
        ratio = volume / ema
        return max(0.0, ratio - 0.7)
    
    def blacklist_token(self, address: str, reason: str = "manual"):
        """Permanently blacklist a token"""
        self._blacklist.add(address.lower())
        if address not in self._token_performance:
            self._token_performance[address] = {}
        self._token_performance[address]["blacklist_reason"] = reason
        self._token_performance[address]["blacklisted_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"🚫 Blacklisted: {address[:10]}... ({reason})")
    
    def _soft_blacklist(self, address: str, reason: str):
        """Temporary blacklist based on failures"""
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
        
        # Auto-blacklist logic
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


# Alias for backward compatibility
MarketScanner = MultiAPIMarketScanner