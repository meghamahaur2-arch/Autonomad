"""
🔥 MEME TOKEN HUNTER - High-Risk, High-Reward Scanner
Specialized scanner for catching explosive meme tokens like CAT, DOGS
"""
import asyncio
import aiohttp
from typing import List, Dict, Optional, Set
from datetime import datetime, timezone
from collections import deque

from logging_manager import get_logger
from models import DiscoveredToken

logger = get_logger("MemeHunter")


class MemeTokenHunter:
    """
    🎯 Aggressive meme token scanner
    - Lower filters for early detection
    - Momentum-based scoring
    - Viral signal detection
    """
    
    DEXSCREENER_BASE = "https://api.dexscreener.com"
    GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
    
    # 🔥 AGGRESSIVE FILTERS (Much lower than your current agent)
    MIN_LIQUIDITY = 10_000      # Down from 50k
    MIN_VOLUME = 20_000         # Down from 100k
    MIN_SCORE = 2.0             # Down from 5.0
    
    # Meme token patterns
    MEME_KEYWORDS = [
        "CAT", "DOG", "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", 
        "WIF", "MEME", "MOON", "ROCKET", "APE", "CHAD", "WOJAK",
        "DOGS", "CATS", "PUPPY", "KITTEN", "FROG", "BIRD",
        "ELON", "TRUMP", "BIDEN", "HARAMBE", "GRUMPY"
    ]
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._seen_tokens: Set[str] = set()
        self._viral_signals: Dict[str, int] = {}
        self._momentum_tracker: Dict[str, deque] = {}
        
        logger.info("🎯 Meme Token Hunter initialized")
        logger.info(f"   Min Liquidity: ${self.MIN_LIQUIDITY:,}")
        logger.info(f"   Min Volume: ${self.MIN_VOLUME:,}")
        logger.info(f"   Tracking {len(self.MEME_KEYWORDS)} meme patterns")
    
    async def scan_for_memes(self, chains: List[str] = None) -> List[DiscoveredToken]:
        """
        🔍 Aggressive scan for meme tokens
        """
        if chains is None:
            chains = ["ethereum", "base", "solana", "polygon", "bsc"]
        
        logger.info(f"🎯 Scanning for EXPLOSIVE meme tokens...")
        
        all_tokens = []
        
        # 1. DexScreener boosted (often meme tokens)
        boosted = await self._scan_boosted()
        all_tokens.extend(boosted)
        
        # 2. Search for specific meme keywords
        for keyword in self.MEME_KEYWORDS[:10]:  # Top 10 patterns
            results = await self._search_keyword(keyword)
            all_tokens.extend(results)
            await asyncio.sleep(0.5)
        
        # 3. GeckoTerminal trending
        for chain in ["eth", "base", "solana", "polygon_pos", "bsc"]:
            trending = await self._scan_trending(chain)
            all_tokens.extend(trending)
            await asyncio.sleep(0.5)
        
        # Deduplicate
        unique = {}
        for token in all_tokens:
            key = f"{token.address}_{token.chain}"
            if key not in unique:
                unique[key] = token
            else:
                # Keep higher score
                if token.opportunity_score > unique[key].opportunity_score:
                    unique[key] = token
        
        tokens = list(unique.values())
        logger.info(f"   📊 Found {len(tokens)} unique tokens")
        
        # Filter with AGGRESSIVE thresholds
        filtered = self._aggressive_filter(tokens)
        logger.info(f"   ✅ {len(filtered)} passed aggressive filters")
        
        # Score with meme-specific logic
        scored = self._score_meme_potential(filtered)
        logger.info(f"   🔥 {len(scored)} high-potential memes")
        
        # Sort by score
        scored.sort(key=lambda t: t.opportunity_score, reverse=True)
        
        return scored
    
    def _aggressive_filter(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """
        🔥 Much more aggressive than normal filters
        """
        filtered = []
        
        for token in tokens:
            # Skip if seen before
            key = f"{token.address}_{token.chain}"
            if key in self._seen_tokens:
                continue
            
            # AGGRESSIVE liquidity check (10x lower than normal)
            if token.liquidity_usd < self.MIN_LIQUIDITY:
                continue
            
            # AGGRESSIVE volume check
            if token.volume_24h < self.MIN_VOLUME:
                continue
            
            # Skip extremely low prices (rug risk)
            if token.price < 0.0000001:
                continue
            
            # Skip if symbol too long (usually scams)
            if len(token.symbol) > 12:
                continue
            
            filtered.append(token)
            self._seen_tokens.add(key)
        
        return filtered
    
    def _score_meme_potential(self, tokens: List[DiscoveredToken]) -> List[DiscoveredToken]:
        """
        🎯 Score based on MEME characteristics, not fundamentals
        """
        for token in tokens:
            score = 0.0
            
            # 1. Meme keyword match (HUGE bonus)
            symbol_upper = token.symbol.upper()
            if any(keyword in symbol_upper for keyword in self.MEME_KEYWORDS):
                score += 20.0  # MASSIVE boost
                logger.info(f"   🔥 MEME MATCH: {token.symbol}")
            
            # 2. Price momentum (bigger = better for memes)
            if token.change_24h_pct > 50:
                score += 15.0
            elif token.change_24h_pct > 20:
                score += 10.0
            elif token.change_24h_pct > 10:
                score += 5.0
            
            # 3. Volume surge (memes = viral activity)
            if token.volume_24h > 1_000_000:
                score += 10.0
            elif token.volume_24h > 500_000:
                score += 7.0
            elif token.volume_24h > 100_000:
                score += 4.0
            
            # 4. Liquidity tier (lower is riskier but higher reward)
            if 10_000 < token.liquidity_usd < 100_000:
                score += 8.0  # Sweet spot for early entry
            elif 100_000 < token.liquidity_usd < 500_000:
                score += 5.0
            elif token.liquidity_usd > 500_000:
                score += 2.0  # Safer but less upside
            
            # 5. Market cap (small = more room to grow)
            if token.market_cap:
                if token.market_cap < 1_000_000:
                    score += 10.0  # Micro cap gem
                elif token.market_cap < 10_000_000:
                    score += 6.0
                elif token.market_cap < 100_000_000:
                    score += 3.0
            
            # 6. Chain bonus (meme tokens thrive on certain chains)
            chain_bonus = {
                "base": 5.0,      # Base is HOT for memes
                "solana": 4.0,    # Solana meme season
                "ethereum": 2.0,  # OG but expensive
                "bsc": 3.0,       # Meme friendly
                "polygon": 1.0
            }
            score += chain_bonus.get(token.chain, 0)
            
            # 7. Track momentum
            self._track_momentum(token)
            momentum_score = self._get_momentum_score(token)
            score += momentum_score
            
            token.opportunity_score = score
        
        # Only return tokens with decent score
        return [t for t in tokens if t.opportunity_score >= self.MIN_SCORE]
    
    def _track_momentum(self, token: DiscoveredToken):
        """Track price momentum over time"""
        key = f"{token.symbol}_{token.chain}"
        
        if key not in self._momentum_tracker:
            self._momentum_tracker[key] = deque(maxlen=10)
        
        self._momentum_tracker[key].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": token.price,
            "volume": token.volume_24h,
            "change_24h": token.change_24h_pct
        })
    
    def _get_momentum_score(self, token: DiscoveredToken) -> float:
        """Calculate momentum score from historical data"""
        key = f"{token.symbol}_{token.chain}"
        
        if key not in self._momentum_tracker:
            return 0.0
        
        history = list(self._momentum_tracker[key])
        
        if len(history) < 2:
            return 0.0
        
        # Check if price is accelerating
        recent_changes = [h["change_24h"] for h in history[-3:]]
        if all(c > 0 for c in recent_changes):
            # Consistent upward momentum
            return 5.0
        
        # Check volume acceleration
        recent_volumes = [h["volume"] for h in history[-3:]]
        if len(recent_volumes) >= 2:
            if recent_volumes[-1] > recent_volumes[0] * 1.5:
                # 50% volume increase
                return 3.0
        
        return 0.0
    
    async def _scan_boosted(self) -> List[DiscoveredToken]:
        """Scan DexScreener boosted tokens"""
        try:
            session = await self._get_session()
            url = f"{self.DEXSCREENER_BASE}/token-boosts/latest/v1"
            
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
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
                        token = self._parse_pair(pair)
                        if token:
                            tokens.append(token)
                    
                    logger.info(f"   Boosted: {len(tokens)} tokens")
                    return tokens
        except Exception as e:
            logger.debug(f"Boosted scan failed: {e}")
            return []
    
    async def _search_keyword(self, keyword: str) -> List[DiscoveredToken]:
        """Search for specific keyword"""
        try:
            session = await self._get_session()
            url = f"{self.DEXSCREENER_BASE}/latest/dex/search"
            params = {"q": keyword}
            
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                pairs = data.get("pairs", [])
                
                tokens = []
                for pair in pairs[:20]:
                    token = self._parse_pair(pair)
                    if token:
                        tokens.append(token)
                
                if tokens:
                    logger.info(f"   '{keyword}': {len(tokens)} tokens")
                return tokens
        except Exception as e:
            logger.debug(f"Search '{keyword}' failed: {e}")
            return []
    
    async def _scan_trending(self, network: str) -> List[DiscoveredToken]:
        """Scan trending pools"""
        try:
            session = await self._get_session()
            url = f"{self.GECKOTERMINAL_BASE}/networks/{network}/trending_pools"
            
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                pools = data.get("data", [])
                
                tokens = []
                for pool in pools[:20]:
                    token = self._parse_gecko_pool(pool, network)
                    if token:
                        tokens.append(token)
                
                logger.info(f"   {network}: {len(tokens)} tokens")
                return tokens
        except Exception as e:
            logger.debug(f"Trending {network} failed: {e}")
            return []
    
    def _parse_pair(self, pair: Dict) -> Optional[DiscoveredToken]:
        """Parse DexScreener pair"""
        try:
            base_token = pair.get("baseToken", {})
            
            address = base_token.get("address", "").lower()
            symbol = base_token.get("symbol", "").upper()
            chain = pair.get("chainId", "").lower()
            
            price = float(pair.get("priceUsd", 0))
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            volume = float(pair.get("volume", {}).get("h24", 0))
            change = float(pair.get("priceChange", {}).get("h24", 0))
            market_cap = float(pair.get("fdv", 0) or pair.get("marketCap", 0))
            
            if not address or price <= 0:
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain=chain,
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change,
                market_cap=market_cap if market_cap > 0 else None
            )
        except Exception:
            return None
    
    def _parse_gecko_pool(self, pool: Dict, network: str) -> Optional[DiscoveredToken]:
        """Parse GeckoTerminal pool"""
        try:
            attributes = pool.get("attributes", {})
            relationships = pool.get("relationships", {})
            
            base_token_data = relationships.get("base_token", {}).get("data", {})
            base_token_id = base_token_data.get("id", "").split("_")
            
            if len(base_token_id) < 2:
                return None
            
            address = base_token_id[1].lower()
            symbol = attributes.get("name", "").split("/")[0].strip().upper()
            
            chain_map = {
                "eth": "ethereum",
                "base": "base",
                "solana": "solana",
                "polygon_pos": "polygon",
                "bsc": "bsc"
            }
            chain = chain_map.get(network, "ethereum")
            
            price = float(attributes.get("base_token_price_usd", 0))
            liquidity = float(attributes.get("reserve_in_usd", 0))
            volume = float(attributes.get("volume_usd", {}).get("h24", 0))
            change = float(attributes.get("price_change_percentage", {}).get("h24", 0))
            
            if not address or price <= 0:
                return None
            
            return DiscoveredToken(
                symbol=symbol,
                address=address,
                chain=chain,
                price=price,
                liquidity_usd=liquidity,
                volume_24h=volume,
                change_24h_pct=change,
                market_cap=None
            )
        except Exception:
            return None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session
    
    async def close(self):
        """Close session"""
        if self._session and not self._session.closed:
            await self._session.close()