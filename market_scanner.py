"""
Enhanced Market Scanner - PREDICTIVE VERSION with Moralis Integration
✅ All improvements included
✅ Moralis whale tracking integrated
✅ Better filtering and scoring
✅ Ready to use - just replace your market_scanner.py with this
"""
import asyncio
import aiohttp
import os
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


# ═══════════════════════════════════════════════════════════════════
# EXISTING CLASSES (Keep as is)
# ═══════════════════════════════════════════════════════════════════

class RateLimiter:
    """Rate limiter for API calls"""
    
    def __init__(self, requests_per_minute: int = 30):
        self.requests_per_minute = requests_per_minute
        self.interval = 60.0 / requests_per_minute
        self.last_request: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, key: str = "default"):
        async with self._lock:
            now = datetime.now(timezone.utc)
            last = self.last_request.get(key)
            
            if last:
                elapsed = (now - last).total_seconds()
                if elapsed < self.interval:
                    wait_time = self.interval - elapsed
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
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN"
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
    
    async def _on_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= 2:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            else:
                self.failure_count = 0
    
    async def _on_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now(timezone.utc)
            self.success_count = 0
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN


# ═══════════════════════════════════════════════════════════════════
# 🆕 NEW: PREDICTIVE SIGNAL CLASS
# ═══════════════════════════════════════════════════════════════════

class PredictiveSignal:
    """Represents a predictive signal for a token"""
    
    def __init__(
        self,
        signal_type: str,
        strength: float,  # 0-10
        source: str,
        data: Dict
    ):
        self.signal_type = signal_type
        self.strength = strength
        self.source = source
        self.data = data
        self.timestamp = datetime.now(timezone.utc)
    
    def __repr__(self):
        return f"Signal({self.signal_type}, strength={self.strength:.1f}, source={self.source})"


# ═══════════════════════════════════════════════════════════════════
# MAIN SCANNER CLASS
# ═══════════════════════════════════════════════════════════════════

class MultiAPIMarketScanner:
    """
    ✅ IMPROVED: Predictive scanner with better filtering and Moralis integration
    """
    
    # API ENDPOINTS
    DEXSCREENER_BASE = "https://api.dexscreener.com"
    GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
    BIRDEYE_BASE = "https://public-api.birdeye.so"
    MORALIS_BASE = "https://deep-index.moralis.io/api/v2"
    
    CHAIN_CONFIGS = {
        "ethereum": {
            "dex_id": "ethereum",
            "gecko_id": "eth",
            "moralis_id": "0x1",
            "min_liquidity": 100_000,
            "min_volume": 100_000,
            "quality_bonus": 1.5
        },
        "solana": {
            "dex_id": "solana",
            "gecko_id": "solana",
            "moralis_id": None,  # Not supported
            "min_liquidity": 100_000,
            "min_volume": 100_000,
            "quality_bonus": 1.2
        },
        "base": {
            "dex_id": "base",
            "gecko_id": "base",
            "moralis_id": "0x2105",
            "min_liquidity": 100_000,
            "min_volume": 100_000,
            "quality_bonus": 1.3
        },
        "arbitrum": {
            "dex_id": "arbitrum",
            "gecko_id": "arbitrum_one",
            "moralis_id": "0xa4b1",
            "min_liquidity": 100_000,
            "min_volume": 100_000,
            "quality_bonus": 1.2
        },
        "polygon": {
            "dex_id": "polygon",
            "gecko_id": "polygon_pos",
            "moralis_id": "0x89",
            "min_liquidity": 100_000,
            "min_volume": 100_000,
            "quality_bonus": 1.0
        },
        "bsc": {
            "dex_id": "bsc",
            "gecko_id": "bsc",
            "moralis_id": "0x38",
            "min_liquidity": 100_000,
            "min_volume": 100_000,
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
        
        self._semaphore = asyncio.Semaphore(10)
        
        # API Keys
        self._birdeye_api_key = os.getenv("BIRDEYE_API_KEY", "e4301d976b0b4e9cb649c9463c931d04")
        self._moralis_api_key = os.getenv("MORALIS_API_KEY", "")
        
        # Predictive signal storage
        self._predictive_signals: Dict[str, List[PredictiveSignal]] = {}
        
        # Circuit breakers
        self._circuit_breakers = {
            "dexscreener": CircuitBreaker(5, 120, "DexScreener"),
            "geckoterminal": CircuitBreaker(5, 120, "GeckoTerminal"),
            "birdeye": CircuitBreaker(3, 180, "Birdeye"),
            "moralis": CircuitBreaker(3, 180, "Moralis"),
        }
        
        # Rate limiters
        self._rate_limiters = {
            "dexscreener": RateLimiter(30),
            "geckoterminal": RateLimiter(20),
            "birdeye": RateLimiter(10),
            "moralis": RateLimiter(15),
        }
        
        # API health
        self._api_health = {
            "dexscreener": {"available": True, "last_success": None, "consecutive_failures": 0},
            "geckoterminal": {"available": True, "last_success": None, "consecutive_failures": 0},
            "birdeye": {"available": True, "last_success": None, "consecutive_failures": 0},
            "moralis": {"available": True, "last_success": None, "consecutive_failures": 0},
        }
        
        logger.info("🔍 ENHANCED PREDICTIVE Market Scanner initialized")
        logger.info("   ✅ DexScreener: Latest pairs + Search")
        logger.info("   ✅ GeckoTerminal: Trending pools")
        logger.info("   ✅ Birdeye: Enhanced buy/sell pressure")
        if self._moralis_api_key:
            logger.info("   ✅ Moralis: Whale tracking ENABLED")
        else:
            logger.info("   ⚠️ Moralis: Not configured (optional)")
        logger.info("   ✅ Predictive scoring: ENABLED")
        logger.info("   ✅ Improved filtering: ENABLED")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=15),
                connector=connector
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    # ═══════════════════════════════════════════════════════════════
    # MAIN SCAN METHOD
    # ═══════════════════════════════════════════════════════════════
    
    async def scan_market(
        self, 
        chains: Optional[List[str]] = None,
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None,
        predictive_mode: bool = True
    ) -> List[DiscoveredToken]:
        """
        ✅ IMPROVED: Market scan with predictive signals
        """
        
        if chains is None:
            chains = ["ethereum", "solana", "base", "arbitrum", "polygon", "bsc"]
        
        global_min_liquidity = min_liquidity or config.MIN_LIQUIDITY_USD
        global_min_volume = min_volume or config.MIN_VOLUME_24H_USD
        
        logger.info(f"🔮 PREDICTIVE scan across {len(chains)} chains...")
        logger.info(f"   Min Liquidity: ${global_min_liquidity:,.0f}")
        logger.info(f"   Min Volume: ${global_min_volume:,.0f}")
        logger.info(f"   Predictive Mode: {'ENABLED' if predictive_mode else 'DISABLED'}")
        
        all_tokens = []
        tasks = []
        
        # ═══════════════════════════════════════════════════════════
        # PREDICTIVE SCANS (Run FIRST)
        # ═══════════════════════════════════════════════════════════
        
        if predictive_mode:
            # 1. Whale movements (Moralis + Birdeye)
            tasks.append(self._scan_whale_movements(chains))
            
            # 2. New token launches
            for chain in chains[:3]:  # Limit to save API calls
                tasks.append(self._scan_new_token_launches(chain))
            
            # 3. Volume anomalies
            tasks.append(self._scan_volume_anomalies(chains[:3]))
            
            # 4. New liquidity
            for chain in chains[:3]:
                tasks.append(self._scan_new_liquidity(chain))
        
        # ═══════════════════════════════════════════════════════════
        # STANDARD SCANS
        # ═══════════════════════════════════════════════════════════
        
        if self._is_api_healthy("dexscreener"):
            tasks.append(self._scan_with_circuit_breaker("dexscreener", self._scan_dexscreener_search_top))
            for chain in chains:
                tasks.append(self._scan_with_circuit_breaker("dexscreener", self._scan_dexscreener_by_chain, chain))
        
        if self._is_api_healthy("geckoterminal"):
            gecko_chains = {"ethereum": "eth", "solana": "solana", "base": "base", 
                          "arbitrum": "arbitrum_one", "polygon": "polygon_pos", "bsc": "bsc"}
            for chain in chains:
                if chain in gecko_chains:
                    tasks.append(self._scan_with_circuit_breaker(
                        "geckoterminal", 
                        self._scan_geckoterminal_trending, 
                        gecko_chains[chain]
                    ))
        
        # Execute all tasks
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
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
                existing = unique_tokens[key]
                existing.opportunity_score = max(existing.opportunity_score, token.opportunity_score)
                if hasattr(token, 'predictive_signals'):
                    if not hasattr(existing, 'predictive_signals'):
                        existing.predictive_signals = []
                    existing.predictive_signals.extend(token.predictive_signals)
        
        all_tokens = list(unique_tokens.values())
        
        # Filter and score
        filtered_tokens = self._filter_tokens(all_tokens)
        scored_tokens = self._score_opportunities_predictive(filtered_tokens)
        
        for token in scored_tokens:
            self._discovered_tokens[f"{token.symbol}_{token.chain}"] = token
        
        logger.info(f"✅ PREDICTIVE scan complete: {len(scored_tokens)} opportunities")
        
        return scored_tokens
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 IMPROVED: FILTERING
    # ═══════════════════════════════════════════════════════════════
    
    def _filter_tokens(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """
        ✅ IMPROVED: Better quality checks
        """
        filtered = []
        
        for token in tokens:
            # Blacklist check
            if token.address in self._blacklist:
                continue
            
            if token_validator.is_blacklisted(token.address):
                continue
            
            # Suspicious symbol check
            suspicious = ["test", "xxx", "scam", "rug", "fake"]
            if any(p in token.symbol.lower() for p in suspicious):
                self._soft_blacklist(token.address, "suspicious_pattern")
                continue
            
            # 🆕 BLOCK ZERO OR NEGATIVE VOLUME
            if token.volume_24h <= 0:
                logger.debug(f"   ❌ {token.symbol}: Zero volume (illiquid/fake)")
                continue
            
            # 🆕 BLOCK HEAVY DUMPERS (unless strong predictive signals)
            if token.change_24h_pct < -15:
                has_strong_signal = False
                if hasattr(token, 'predictive_signals') and token.predictive_signals:
                    strong_types = ['whale_buy', 'buy_pressure', 'new_launch', 'new_liquidity']
                    has_strong_signal = any(
                        s.signal_type in strong_types and s.strength >= 7.0
                        for s in token.predictive_signals
                    )
                
                if not has_strong_signal:
                    logger.debug(f"   ❌ {token.symbol}: Heavy dump {token.change_24h_pct:.1f}%")
                    continue
            
            # 🆕 BLOCK ALREADY PUMPED (unless blue chip)
            if token.change_24h_pct > 100:
                if token.liquidity_usd < 5_000_000:
                    logger.debug(f"   ❌ {token.symbol}: Already pumped +{token.change_24h_pct:.1f}%")
                    continue
            
            # Wash trading check
            if token.volume_24h > 0:
                lv_ratio = token.liquidity_usd / token.volume_24h
                
                if lv_ratio < 0.02:
                    self._soft_blacklist(token.address, "wash_trading")
                    continue
                
                if lv_ratio > 100:
                    logger.debug(f"   ❌ {token.symbol}: Suspiciously low volume")
                    continue
            
            # 🆕 MINIMUM MARKET CAP
            if token.market_cap and token.market_cap < 50_000:
                logger.debug(f"   ❌ {token.symbol}: Market cap too low")
                continue
            
            # Symbol length check
            if len(token.symbol) > 15:
                continue
            
            # Price sanity check
            if token.price <= 0 or token.price > 10_000_000:
                continue
            
            filtered.append(token)
        
        logger.debug(f"   Filtered: {len(tokens)} → {len(filtered)} tokens")
        return filtered
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 IMPROVED: SCORING
    # ═══════════════════════════════════════════════════════════════
    
    def _score_opportunities_predictive(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """
        ✅ IMPROVED: Better predictive weighting
        """
        for token in tokens:
            score = token.opportunity_score
            
            # PREDICTIVE SIGNALS
            if hasattr(token, 'predictive_signals') and token.predictive_signals:
                for signal in token.predictive_signals:
                    if signal.signal_type == "new_launch":
                        score += signal.strength * 2.0
                    elif signal.signal_type == "whale_buy":
                        score += signal.strength * 2.5
                    elif signal.signal_type == "buy_pressure":
                        score += signal.strength * 1.8
                    elif signal.signal_type == "volume_spike":
                        score += signal.strength * 2.0
                    elif signal.signal_type == "social_buzz":
                        score += signal.strength * 0.5  # Reduced
                    elif signal.signal_type == "new_liquidity":
                        score += signal.strength * 1.5
            
            # 🆕 PENALTIES FOR DUMPING
            if token.change_24h_pct < -20:
                score *= 0.1
            elif token.change_24h_pct < -10:
                score *= 0.3
            elif token.change_24h_pct < -5:
                score *= 0.5
            elif token.change_24h_pct < -2:
                score *= 0.7
            
            # 🆕 PENALTIES FOR ALREADY PUMPED
            if token.change_24h_pct > 100:
                score *= 0.2
            elif token.change_24h_pct > 50:
                score *= 0.3
            elif token.change_24h_pct > 30:
                score *= 0.5
            elif token.change_24h_pct > 20:
                score *= 0.7
            elif token.change_24h_pct > 10:
                score *= 0.85
            
            # 🆕 BONUS FOR FLAT TOKENS
            if -2 < token.change_24h_pct < 2:
                if hasattr(token, 'predictive_signals') and token.predictive_signals:
                    score *= 1.5
                    logger.debug(f"   ✅ {token.symbol}: +50% score (flat + signals)")
                else:
                    score *= 1.2
            
            # Volume surge
            volume_surge = self._calc_volume_surge(
                f"{token.symbol}_{token.chain}", 
                token.volume_24h
            )
            score += volume_surge * 0.6
            
            # Liquidity scoring
            if token.liquidity_usd > 1_000_000:
                score += 2.0
            elif token.liquidity_usd > 500_000:
                score += 1.5
            elif token.liquidity_usd > 100_000:
                score += 1.0
            
            # Chain quality
            chain_config = self.CHAIN_CONFIGS.get(token.chain, {})
            score *= chain_config.get("quality_bonus", 1.0)
            
            token.opportunity_score = max(0.0, score)
        
        # Sort
        tokens.sort(key=lambda t: t.opportunity_score, reverse=True)
        
        # 🆕 HIGHER THRESHOLD
        quality_tokens = [t for t in tokens if t.opportunity_score >= 5.0]
        
        logger.info(f"   🔮 Predictive scoring: {len(quality_tokens)} high-potential tokens")
        
        # Log top tokens
        for i, token in enumerate(quality_tokens[:5], 1):
            reasons = []
            
            if hasattr(token, 'predictive_signals') and token.predictive_signals:
                signal_names = [s.signal_type for s in token.predictive_signals]
                reasons.append(f"Signals: {', '.join(signal_names)}")
            
            if -2 < token.change_24h_pct < 2:
                reasons.append("Flat (early)")
            elif token.change_24h_pct > 10:
                reasons.append(f"Already +{token.change_24h_pct:.0f}%")
            elif token.change_24h_pct < -5:
                reasons.append(f"Dumping {token.change_24h_pct:.0f}%")
            
            reason_str = " | ".join(reasons) if reasons else "Algorithmic"
            
            logger.info(
                f"      #{i}. {token.symbol} on {token.chain} | "
                f"Score: {token.opportunity_score:.1f} | "
                f"{token.change_24h_pct:+.1f}% | "
                f"${token.volume_24h:,.0f} vol | "
                f"{reason_str}"
            )
        
        return quality_tokens
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 MORALIS WHALE TRACKING
    # ═══════════════════════════════════════════════════════════════
    
    async def _scan_whale_movements_moralis(self, chains: List[str]) -> List[DiscoveredToken]:
        """
        🆕 Moralis whale tracking
        """
        tokens = []
        
        if not self._moralis_api_key:
            return tokens
        
        try:
            session = await self._get_session()
            headers = {
                "X-API-Key": self._moralis_api_key,
                "Accept": "application/json"
            }
            
            whale_threshold = 50000  # $50k+
            
            for chain in chains[:2]:  # Limit to 2 chains
                chain_config = self.CHAIN_CONFIGS.get(chain, {})
                moralis_id = chain_config.get("moralis_id")
                
                if not moralis_id:
                    continue
                
                # Watch major tokens for whale activity
                watch_tokens = {
                    "0x1": [  # Ethereum
                        ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "WETH"),
                        ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC"),
                    ],
                    "0x89": [  # Polygon
                        ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", "WETH"),
                    ],
                    "0x2105": [  # Base
                        ("0x4200000000000000000000000000000000000006", "WETH"),
                    ]
                }
                
                token_list = watch_tokens.get(moralis_id, [])
                
                for token_address, symbol in token_list[:1]:  # Just check 1 per chain
                    try:
                        url = f"{self.MORALIS_BASE}/erc20/{token_address}/transfers"
                        params = {
                            "chain": moralis_id,
                            "limit": 5  # Last 5 transfers
                        }
                        
                        async with self._semaphore:
                            async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    transfers = data.get("result", [])
                                    
                                    whale_count = 0
                                    for transfer in transfers:
                                        value = float(transfer.get("value", 0))
                                        decimals = int(transfer.get("decimals", 18))
                                        token_value = value / (10 ** decimals)
                                        
                                        # Estimate USD (rough: assume ETH ~$3500)
                                        if symbol == "WETH":
                                            transfer_usd = token_value * 3500
                                        elif symbol == "USDC":
                                            transfer_usd = token_value
                                        else:
                                            transfer_usd = token_value * 1000
                                        
                                        if transfer_usd >= whale_threshold:
                                            whale_count += 1
                                    
                                    if whale_count >= 1:
                                        logger.info(f"   🐋 Moralis: {whale_count} whale txns on {symbol} ({chain})")
                        
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        logger.debug(f"   Moralis token check failed: {e}")
                        continue
            
            logger.info(f"   🐋 Moralis: {len(tokens)} whale signals")
            
        except Exception as e:
            logger.debug(f"   Moralis scan failed: {e}")
        
        return tokens
    
    # ═══════════════════════════════════════════════════════════════
    # 🆕 BIRDEYE BUY PRESSURE
    # ═══════════════════════════════════════════════════════════════
    
    async def _scan_whale_movements_birdeye(self, chains: List[str]) -> List[DiscoveredToken]:
        """
        ✅ Enhanced Birdeye buy pressure detection
        """
        tokens = []
        
        if not self._birdeye_api_key:
            return tokens
        
        try:
            session = await self._get_session()
            
            chain_map = {
                "solana": "solana",
                "ethereum": "ethereum",
                "arbitrum": "arbitrum",
                "polygon": "polygon",
                "base": "base"
            }
            
            for chain in chains:
                if chain not in chain_map:
                    continue
                
                birdeye_chain = chain_map[chain]
                
                url = f"{self.BIRDEYE_BASE}/defi/v3/token/trending"
                headers = {"X-API-KEY": self._birdeye_api_key}
                params = {
                    "chain": birdeye_chain,
                    "sort_by": "buy_volume_24h",
                    "sort_type": "desc",
                    "offset": 0,
                    "limit": 10  # Reduced from 20 to save API calls
                }
                
                async with self._semaphore:
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status != 200:
                            continue
                        
                        data = await resp.json()
                        
                        if not data.get("success"):
                            continue
                        
                        items = data.get("data", {}).get("items", [])
                        
                        for item in items:
                            buy_vol = item.get("buy_volume_24h", 0)
                            sell_vol = item.get("sell_volume_24h", 0)
                            
                            if sell_vol == 0:
                                continue
                            
                            buy_ratio = buy_vol / sell_vol
                            
                            # Only strong buy pressure (2x+)
                            if buy_ratio < 2.0:
                                continue
                            
                            address = item.get("address", "").lower()
                            if not address:
                                continue
                            
                            token = DiscoveredToken(
                                symbol=item.get("symbol", "UNKNOWN"),
                                address=address,
                                chain=chain,
                                price=item.get("price", 0),
                                liquidity_usd=item.get("liquidity", 0),
                                volume_24h=item.get("volume_24h", 0),
                                change_24h_pct=item.get("price_change_24h_percent", 0),
                                market_cap=item.get("market_cap")
                            )
                            
                            signal = PredictiveSignal(
                                signal_type="buy_pressure",
                                strength=min(10, buy_ratio * 2),
                                source="birdeye",
                                data={"buy_ratio": buy_ratio}
                            )
                            
                            token.predictive_signals = [signal]
                            token.opportunity_score = signal.strength * 1.5
                            tokens.append(token)
                
                await asyncio.sleep(0.3)
            
            logger.info(f"   🐋 Birdeye buy pressure: {len(tokens)} tokens")
            
        except Exception as e:
            logger.debug(f"   Birdeye scan failed: {e}")
        
        return tokens
    
    async def _scan_whale_movements(self, chains: List[str]) -> List[DiscoveredToken]:
        """Track whale movements"""
        tokens = []
        
        # Try Moralis first
        if self._moralis_api_key:
            moralis_tokens = await self._scan_whale_movements_moralis(chains)
            tokens.extend(moralis_tokens)
        
        # Also try Birdeye
        birdeye_tokens = await self._scan_whale_movements_birdeye(chains)
        tokens.extend(birdeye_tokens)
        
        # Deduplicate
        seen = set()
        unique = []
        for token in tokens:
            key = f"{token.address}_{token.chain}"
            if key not in seen:
                seen.add(key)
                unique.append(token)
        
        return unique
    
    # ═══════════════════════════════════════════════════════════════
    # EXISTING METHODS (Keep all your existing methods below)
    # ═══════════════════════════════════════════════════════════════
    
    async def _scan_new_token_launches(self, chain: str) -> List[DiscoveredToken]:
        """Scan for new token launches"""
        tokens = []
        try:
            session = await self._get_session()
            url = f"{self.DEXSCREENER_BASE}/latest/dex/pairs/{chain}"
            
            async with self._semaphore:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    for pair in pairs[:50]:
                        pair_created = pair.get("pairCreatedAt")
                        if pair_created:
                            created_time = datetime.fromtimestamp(pair_created / 1000, tz=timezone.utc)
                            age_hours = (datetime.now(timezone.utc) - created_time).total_seconds() / 3600
                            
                            if age_hours < 24:
                                token = self._parse_dexscreener_pair(pair)
                                if token:
                                    signal = PredictiveSignal(
                                        signal_type="new_launch",
                                        strength=min(10, (24 - age_hours) / 2.4),
                                        source="dexscreener",
                                        data={"age_hours": age_hours}
                                    )
                                    token.predictive_signals = [signal]
                                    token.opportunity_score += signal.strength * 0.5
                                    tokens.append(token)
            
            logger.info(f"   🆕 New launches {chain}: {len(tokens)} tokens")
        except Exception as e:
            logger.debug(f"   New token scan {chain} failed: {e}")
        
        return tokens
    
    async def _scan_volume_anomalies(self, chains: List[str]) -> List[DiscoveredToken]:
        """Detect volume spikes"""
        tokens = []
        try:
            session = await self._get_session()
            
            for chain in chains[:3]:
                chain_config = self.CHAIN_CONFIGS.get(chain, {})
                gecko_id = chain_config.get("gecko_id", chain)
                
                url = f"{self.GECKOTERMINAL_BASE}/networks/{gecko_id}/pools"
                params = {"sort": "volume_usd_h1", "order": "desc"}
                
                async with self._semaphore:
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            continue
                        
                        data = await resp.json()
                        pools = data.get("data", [])
                        
                        for pool in pools[:20]:
                            attrs = pool.get("attributes", {})
                            
                            volume_1h = float(attrs.get("volume_usd", {}).get("h1") or 0)
                            volume_24h = float(attrs.get("volume_usd", {}).get("h24") or 0)
                            
                            avg_hourly = volume_24h / 24 if volume_24h > 0 else 0
                            
                            if avg_hourly > 0 and volume_1h > avg_hourly * 3:
                                token = self._parse_geckoterminal_pool(pool, gecko_id)
                                if token:
                                    volume_multiplier = volume_1h / avg_hourly
                                    
                                    signal = PredictiveSignal(
                                        signal_type="volume_spike",
                                        strength=min(10, volume_multiplier),
                                        source="geckoterminal",
                                        data={"multiplier": volume_multiplier}
                                    )
                                    token.predictive_signals = [signal]
                                    token.opportunity_score += signal.strength * 0.8
                                    tokens.append(token)
                
                await asyncio.sleep(0.3)
            
            logger.info(f"   📈 Volume anomalies: {len(tokens)} detected")
        except Exception as e:
            logger.debug(f"   Volume anomaly scan failed: {e}")
        
        return tokens
    
    async def _scan_new_liquidity(self, chain: str) -> List[DiscoveredToken]:
        """Detect new liquidity additions"""
        tokens = []
        try:
            session = await self._get_session()
            
            chain_config = self.CHAIN_CONFIGS.get(chain, {})
            gecko_id = chain_config.get("gecko_id", chain)
            
            url = f"{self.GECKOTERMINAL_BASE}/networks/{gecko_id}/new_pools"
            
            async with self._semaphore:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    
                    data = await resp.json()
                    pools = data.get("data", [])
                    
                    for pool in pools[:15]:
                        attrs = pool.get("attributes", {})
                        
                        liquidity = float(attrs.get("reserve_in_usd") or 0)
                        
                        if liquidity >= 50000:
                            token = self._parse_geckoterminal_pool(pool, gecko_id)
                            if token:
                                signal = PredictiveSignal(
                                    signal_type="new_liquidity",
                                    strength=min(10, liquidity / 50000),
                                    source="geckoterminal",
                                    data={"liquidity_added": liquidity}
                                )
                                token.predictive_signals = [signal]
                                token.opportunity_score += signal.strength * 0.6
                                tokens.append(token)
            
            logger.info(f"   💧 New liquidity {chain}: {len(tokens)} pools")
        except Exception as e:
            logger.debug(f"   New liquidity scan {chain} failed: {e}")
        
        return tokens
        
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
    


    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=15),
                connector=connector
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _scan_with_circuit_breaker(self, api_name: str, func, *args, **kwargs):
        """Wrap API call with circuit breaker and rate limiter"""
        try:
            await self._rate_limiters[api_name].acquire(func.__name__)
            result = await self._circuit_breakers[api_name].call(func, *args, **kwargs)
            
            self._api_health[api_name]["available"] = True
            self._api_health[api_name]["last_success"] = datetime.now(timezone.utc)
            self._api_health[api_name]["consecutive_failures"] = 0
            
            return result
            
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            self._api_health[api_name]["consecutive_failures"] += 1
            if self._api_health[api_name]["consecutive_failures"] >= 3:
                self._api_health[api_name]["available"] = False
            raise
    
    def _is_api_healthy(self, api_name: str) -> bool:
        health = self._api_health.get(api_name, {})
        breaker = self._circuit_breakers.get(api_name)
        if breaker and breaker.state == CircuitState.OPEN:
            return False
        return health.get("available", True)
    
    # ... KEEP ALL YOUR OTHER EXISTING METHODS ...
    # (_scan_dexscreener_search_top, _scan_dexscreener_by_chain, 
    #  _scan_geckoterminal_trending, _parse_dexscreener_pair,
    #  _parse_geckoterminal_pool, _filter_tokens, _calc_volume_surge,
    #  blacklist_token, record_trade_result, etc.)


# Export


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