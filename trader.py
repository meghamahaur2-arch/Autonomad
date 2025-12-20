"""
🔥 CAT & DOGS Token Trader - Simple & Aggressive
Uses CoinGecko API for token discovery
Trades only CAT and DOGS tokens like the top performing agents
"""
import asyncio
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, List
import aiohttp

from config import config
from logging_manager import get_logger
from api_client import RecallAPIClient

logger = get_logger("CatDogsTrader")


class CatDogsTrader:
    """
    🐱🐶 Ultra-simple trader focused on CAT and DOGS tokens
    Uses CoinGecko Terminal API (no key needed)
    """
    
    # CoinGecko Terminal API
    GECKO_BASE = "https://api.geckoterminal.com/api/v2"
    
    # Known CAT/DOGS token info (we'll search for these)
    TARGET_TOKENS = {
        "CAT": ["CAT", "CATS", "KITTY"],
        "DOGS": ["DOGS", "DOG", "PUPPY", "DOGE"]
    }
    
    # Chains to search (in order of preference)
    SEARCH_CHAINS = [
        "eth",           # Ethereum
        "base",          # Base (popular for memes)
        "solana",        # Solana
        "polygon_pos",   # Polygon
        "arbitrum_one",  # Arbitrum
        "bsc"            # BSC
    ]
    
    # Trading parameters
    POSITION_SIZE = 10000  # 10k USDC per trade
    MAX_TRADES = 50
    CHECK_INTERVAL = 60
    MIN_LIQUIDITY = 5000   # Minimum $5k liquidity
    
    def __init__(self):
        self.client = RecallAPIClient(config.RECALL_API_KEY, config.base_url)
        self.competition_id: Optional[str] = None
        self.trades_executed = 0
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info("🐱🐶 CAT & DOGS Trader initialized (CoinGecko API)")
        logger.info(f"   Position Size: ${self.POSITION_SIZE:,} USDC")
        logger.info(f"   Max Trades: {self.MAX_TRADES}")
    
    async def initialize(self):
        """Initialize trader"""
        self.competition_id = await self._select_competition()
        logger.info(f"✅ Competition: {self.competition_id}")
    
    async def _select_competition(self) -> str:
        """Select competition"""
        if config.COMPETITION_ID:
            return config.COMPETITION_ID
        
        try:
            user_comps = await self.client.get_user_competitions()
            competitions = user_comps.get("competitions", [])
            
            if not competitions:
                all_comps = await self.client.get_competitions()
                competitions = all_comps.get("competitions", [])
            
            if not competitions:
                raise ValueError("No competitions found")
            
            active_comps = [c for c in competitions if c.get("status") == "active"]
            selected = active_comps[0] if active_comps else competitions[0]
            
            return selected.get("id")
        except Exception as e:
            logger.error(f"Failed to select competition: {e}")
            raise
    
    async def run(self):
        """Main trading loop"""
        logger.info("=" * 80)
        logger.info("🐱🐶 CAT & DOGS TRADER STARTING")
        logger.info("=" * 80)
        
        await self.initialize()
        
        self._running = True
        
        while self._running and not self._shutdown_event.is_set():
            try:
                await self.run_cycle()
                
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.CHECK_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                await asyncio.sleep(30)
        
        logger.info("🛑 Trader stopped")
    
    async def run_cycle(self):
        """Single trading cycle"""
        logger.info(f"\n{'~' * 70}")
        logger.info(f"🔄 CYCLE #{self.trades_executed + 1}")
        logger.info(f"{'~' * 70}")
        
        if self.trades_executed >= self.MAX_TRADES:
            logger.info(f"✅ Max trades reached ({self.MAX_TRADES}). Stopping.")
            self._running = False
            return
        
        # Get portfolio
        portfolio = await self.client.get_portfolio(self.competition_id)
        balances = portfolio.get("balances", [])
        
        logger.info(f"💼 Portfolio has {len(balances)} tokens")
        
        # Find ANY stablecoin balance
        stable_balance = None
        for balance in balances:
            symbol = balance.get("symbol", "").upper()
            amount = float(balance.get("amount", 0))
            value = float(balance.get("value", 0))
            
            logger.info(f"   {symbol}: {amount:.4f} (${value:.2f})")
            
            # Check if it's a stablecoin
            if any(s in symbol for s in ["USDC", "USDT", "DAI", "USD", "BUSD"]):
                if value >= 100:  # At least $100
                    stable_balance = balance
                    logger.info(f"   ✅ Found stablecoin: {symbol}")
        
        if not stable_balance:
            logger.warning("❌ No stablecoin balance found (need USDC/USDT/DAI)")
            logger.warning("   Make sure you have at least $100 in stablecoins")
            return
        
        stable_symbol = stable_balance.get("symbol", "")
        stable_amount = float(stable_balance.get("amount", 0))
        stable_value = float(stable_balance.get("value", 0))
        stable_address = stable_balance.get("tokenAddress", "")
        stable_chain = stable_balance.get("specificChain", "")
        
        logger.info(f"💰 Using: {stable_symbol} = ${stable_value:.2f}")
        
        # Search for CAT or DOGS tokens
        target_token = await self._find_target_token()
        
        if not target_token:
            logger.info("🔍 No CAT/DOGS tokens found. Searching again next cycle...")
            return
        
        # Calculate trade amount (use available balance)
        trade_amount = min(self.POSITION_SIZE, stable_value * 0.95)
        
        if trade_amount < 100:
            logger.warning(f"❌ Balance too low: ${stable_value:.2f}")
            return
        
        # Execute trade
        logger.info(f"\n🎯 TARGET FOUND: {target_token['symbol']}")
        logger.info(f"   Address: {target_token['address'][:20]}...")
        logger.info(f"   Chain: {target_token['chain']}")
        logger.info(f"   Price: ${target_token['price']:.8f}")
        logger.info(f"   Liquidity: ${target_token['liquidity']:,.0f}")
        
        success = await self._execute_trade(
            from_address=stable_address,
            from_chain=stable_chain,
            to_address=target_token["address"],
            to_chain=target_token["chain"],
            amount=trade_amount,
            symbol=target_token["symbol"]
        )
        
        if success:
            self.trades_executed += 1
            logger.info(f"✅ Trade {self.trades_executed}/{self.MAX_TRADES} executed")
        else:
            logger.warning("❌ Trade failed")
    
    async def _find_target_token(self) -> Optional[Dict]:
        """
        Search CoinGecko Terminal for CAT or DOGS tokens
        """
        logger.info("🔍 Searching CoinGecko for CAT/DOGS tokens...")
        
        session = await self._get_session()
        
        # Search each chain
        for chain in self.SEARCH_CHAINS:
            logger.info(f"   Searching {chain}...")
            
            try:
                # Get trending pools on this chain
                url = f"{self.GECKO_BASE}/networks/{chain}/trending_pools"
                
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"   {chain}: HTTP {resp.status}")
                        continue
                    
                    data = await resp.json()
                    pools = data.get("data", [])
                    
                    # Check each pool
                    for pool in pools:
                        token = self._parse_gecko_pool(pool, chain)
                        if token and self._is_target_token(token["symbol"]):
                            logger.info(f"   ✅ Found {token['symbol']} on {chain}!")
                            return token
                
                await asyncio.sleep(1)  # Rate limit
                
            except Exception as e:
                logger.debug(f"   {chain} search failed: {e}")
                continue
        
        # Also try direct search
        for target_name, search_terms in self.TARGET_TOKENS.items():
            for term in search_terms[:2]:  # Only first 2 terms
                try:
                    url = f"{self.GECKO_BASE}/search/pools"
                    params = {"query": term, "network": "eth"}
                    
                    async with session.get(url, params=params, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pools = data.get("data", [])
                            
                            for pool in pools[:10]:
                                token = self._parse_gecko_pool(pool, "eth")
                                if token and self._is_target_token(token["symbol"]):
                                    logger.info(f"   ✅ Found {token['symbol']} via search!")
                                    return token
                    
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.debug(f"Search '{term}' failed: {e}")
                    continue
        
        return None
    
    def _is_target_token(self, symbol: str) -> bool:
        """Check if symbol matches our targets"""
        symbol_upper = symbol.upper()
        
        # Exact match
        if symbol_upper in ["CAT", "DOGS", "DOG"]:
            return True
        
        # Check all search terms
        for target_terms in self.TARGET_TOKENS.values():
            if symbol_upper in [t.upper() for t in target_terms]:
                return True
        
        return False
    
    def _parse_gecko_pool(self, pool: Dict, network: str) -> Optional[Dict]:
        """Parse CoinGecko pool data"""
        try:
            attributes = pool.get("attributes", {})
            relationships = pool.get("relationships", {})
            
            # Get base token
            base_token_data = relationships.get("base_token", {}).get("data", {})
            base_token_id = base_token_data.get("id", "")
            
            if not base_token_id:
                return None
            
            # Parse token address from ID (format: network_address)
            parts = base_token_id.split("_")
            if len(parts) < 2:
                return None
            
            address = parts[1]
            
            # Get symbol from pool name
            name = attributes.get("name", "")
            symbol = name.split("/")[0].strip() if "/" in name else ""
            
            if not symbol:
                return None
            
            # Map network names
            chain_map = {
                "eth": "ethereum",
                "base": "base",
                "solana": "solana",
                "polygon_pos": "polygon",
                "arbitrum_one": "arbitrum",
                "bsc": "bsc"
            }
            chain = chain_map.get(network, network)
            
            # Get metrics
            price = float(attributes.get("base_token_price_usd", 0))
            liquidity = float(attributes.get("reserve_in_usd", 0))
            volume = float(attributes.get("volume_usd", {}).get("h24", 0))
            
            if price <= 0 or liquidity < self.MIN_LIQUIDITY:
                return None
            
            return {
                "symbol": symbol.upper(),
                "address": address.lower(),
                "chain": chain,
                "price": price,
                "liquidity": liquidity,
                "volume": volume
            }
            
        except Exception as e:
            logger.debug(f"Parse error: {e}")
            return None
    
    async def _execute_trade(
        self,
        from_address: str,
        from_chain: str,
        to_address: str,
        to_chain: str,
        amount: float,
        symbol: str
    ) -> bool:
        """Execute the trade"""
        
        logger.info("=" * 70)
        logger.info(f"📤 EXECUTING TRADE")
        logger.info(f"   From: Stablecoin ({from_address[:10]}...) on {from_chain}")
        logger.info(f"   To: {symbol} ({to_address[:10]}...) on {to_chain}")
        logger.info(f"   Amount: ${amount:,.2f}")
        logger.info("=" * 70)
        
        try:
            amount_str = f"{amount:.6f}"
            
            result = await self.client.execute_trade(
                competition_id=self.competition_id,
                from_token=from_address,
                to_token=to_address,
                amount=amount_str,
                reason=f"🐱🐶 Buying {symbol} - Following top agents!",
                from_chain=from_chain,
                to_chain=to_chain
            )
            
            if result.get("success"):
                logger.info("✅ TRADE SUCCESSFUL!")
                return True
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"❌ Trade failed: {error}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Trade error: {e}")
            return False
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("🛑 Shutting down...")
        self._running = False
        self._shutdown_event.set()
        
        if self._session and not self._session.closed:
            await self._session.close()
        
        await self.client.close()
        
        logger.info("=" * 80)
        logger.info("📊 FINAL STATS")
        logger.info("=" * 80)
        logger.info(f"   Total Trades: {self.trades_executed}")
        logger.info("=" * 80)


async def main():
    """Main entry point"""
    trader = CatDogsTrader()
    
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("📡 Received shutdown signal")
        asyncio.create_task(trader.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass
    
    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt")
    finally:
        if trader._running:
            await trader.shutdown()


if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║          🐱🐶 CAT & DOGS TOKEN TRADER 🐶🐱                     ║
    ║                                                              ║
    ║   Using CoinGecko Terminal API (Free)                      ║
    ║   Searches all chains for CAT/DOGS tokens                  ║
    ║   Trades: Up to 10,000 USDC per trade                      ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"💀 Fatal error: {e}", exc_info=True)
        sys.exit(1)