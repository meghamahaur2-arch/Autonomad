
"""
API Verification Script
Tests all external APIs to verify they're working correctly
Run this BEFORE making any code changes
"""
import asyncio
import aiohttp
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# API Configuration
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "e4301d976b0b4e9cb649c9463c931d04")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJub25jZSI6ImJhNjAwNzNkLWY3YjYtNGNhYi04YmQ2LTYzNmYwOWU3ZjZhMyIsIm9yZ0lkIjoiNDg3MjE3IiwidXNlcklkIjoiNTAxMjczIiwidHlwZUlkIjoiZjMxNWMwMzMtYjc0ZC00YmI0LWJkOGItNmJmNGEzZTNjYThkIiwidHlwZSI6IlBST0pFQ1QiLCJpYXQiOjE3NjY0NzM2MTgsImV4cCI6NDkyMjIzMzYxOH0.k2HBAVtOeqlay7JjmO322_2GJsYGtGulDA1OR8OGNMc")

# API Endpoints
DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
BIRDEYE_BASE = "https://public-api.birdeye.so"
MORALIS_BASE = "https://deep-index.moralis.io/api/v2"


class APIVerifier:
    def __init__(self):
        self.results = {
            "dexscreener": {"status": "unknown", "data": None, "error": None},
            "geckoterminal": {"status": "unknown", "data": None, "error": None},
            "birdeye": {"status": "unknown", "data": None, "error": None},
            "moralis": {"status": "unknown", "data": None, "error": None},
        }
        self.session = None
    
    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
    # ========================================================================
    # DEXSCREENER TESTS
    # ========================================================================
    
    async def test_dexscreener_latest(self):
        """Test DexScreener latest pairs endpoint"""
        print("\n" + "="*70)
        print("🔍 TESTING DEXSCREENER - Latest Pairs")
        print("="*70)
        
        try:
            session = await self.get_session()
            url = f"{DEXSCREENER_BASE}/latest/dex/pairs/ethereum"
            
            print(f"📡 URL: {url}")
            
            async with session.get(url) as resp:
                print(f"📊 Status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    print(f"✅ SUCCESS - Found {len(pairs)} pairs")
                    
                    if pairs:
                        print("\n📝 Sample pair:")
                        sample = pairs[0]
                        print(f"   Symbol: {sample.get('baseToken', {}).get('symbol')}")
                        print(f"   Address: {sample.get('baseToken', {}).get('address')}")
                        print(f"   Chain: {sample.get('chainId')}")
                        print(f"   Price: ${float(sample.get('priceUsd', 0)):.6f}")
                        print(f"   Liquidity: ${float(sample.get('liquidity', {}).get('usd', 0)):,.0f}")
                        print(f"   Volume 24h: ${float(sample.get('volume', {}).get('h24', 0)):,.0f}")
                    
                    self.results["dexscreener"] = {
                        "status": "working",
                        "data": {"pairs_count": len(pairs), "sample": pairs[0] if pairs else None},
                        "error": None
                    }
                else:
                    error_text = await resp.text()
                    print(f"❌ FAILED - HTTP {resp.status}")
                    print(f"   Response: {error_text[:200]}")
                    self.results["dexscreener"] = {
                        "status": "error",
                        "data": None,
                        "error": f"HTTP {resp.status}: {error_text[:200]}"
                    }
        
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            self.results["dexscreener"] = {
                "status": "error",
                "data": None,
                "error": str(e)
            }
    
    async def test_dexscreener_search(self):
        """Test DexScreener search endpoint"""
        print("\n" + "="*70)
        print("🔍 TESTING DEXSCREENER - Search")
        print("="*70)
        
        try:
            session = await self.get_session()
            url = f"{DEXSCREENER_BASE}/latest/dex/search"
            params = {"q": "ETH"}
            
            print(f"📡 URL: {url}?q=ETH")
            
            async with session.get(url, params=params) as resp:
                print(f"📊 Status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    print(f"✅ SUCCESS - Found {len(pairs)} pairs for 'ETH' search")
                    
                    # Count by chain
                    chains = {}
                    for pair in pairs[:20]:
                        chain = pair.get("chainId", "unknown")
                        chains[chain] = chains.get(chain, 0) + 1
                    
                    print(f"\n📊 Distribution across chains:")
                    for chain, count in sorted(chains.items(), key=lambda x: x[1], reverse=True):
                        print(f"   {chain}: {count} pairs")
                else:
                    error_text = await resp.text()
                    print(f"❌ FAILED - HTTP {resp.status}")
                    print(f"   Response: {error_text[:200]}")
        
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
    
    # ========================================================================
    # GECKOTERMINAL TESTS
    # ========================================================================
    
    async def test_geckoterminal_trending(self):
        """Test GeckoTerminal trending pools"""
        print("\n" + "="*70)
        print("🦎 TESTING GECKOTERMINAL - Trending Pools")
        print("="*70)
        
        try:
            session = await self.get_session()
            url = f"{GECKOTERMINAL_BASE}/networks/eth/trending_pools"
            
            print(f"📡 URL: {url}")
            
            async with session.get(url) as resp:
                print(f"📊 Status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    pools = data.get("data", [])
                    
                    print(f"✅ SUCCESS - Found {len(pools)} trending pools")
                    
                    if pools:
                        print("\n📝 Sample pool:")
                        sample = pools[0]
                        attrs = sample.get("attributes", {})
                        print(f"   Name: {attrs.get('name')}")
                        print(f"   Address: {attrs.get('address')}")
                        print(f"   Liquidity: ${float(attrs.get('reserve_in_usd', 0)):,.0f}")
                        volume = attrs.get("volume_usd", {})
                        print(f"   Volume 24h: ${float(volume.get('h24', 0)):,.0f}")
                    
                    self.results["geckoterminal"] = {
                        "status": "working",
                        "data": {"pools_count": len(pools), "sample": pools[0] if pools else None},
                        "error": None
                    }
                else:
                    error_text = await resp.text()
                    print(f"❌ FAILED - HTTP {resp.status}")
                    print(f"   Response: {error_text[:200]}")
                    self.results["geckoterminal"] = {
                        "status": "error",
                        "data": None,
                        "error": f"HTTP {resp.status}: {error_text[:200]}"
                    }
        
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            self.results["geckoterminal"] = {
                "status": "error",
                "data": None,
                "error": str(e)
            }
    
    # ========================================================================
    # BIRDEYE TESTS
    # ========================================================================
    
    async def test_birdeye_trending(self):
        """Test Birdeye trending tokens"""
        print("\n" + "="*70)
        print("🐦 TESTING BIRDEYE - Trending Tokens")
        print("="*70)
        
        if not BIRDEYE_API_KEY or BIRDEYE_API_KEY == "your_key_here":
            print("⚠️ WARNING: BIRDEYE_API_KEY not configured")
            print("   Set BIRDEYE_API_KEY in your .env file")
            self.results["birdeye"] = {
                "status": "not_configured",
                "data": None,
                "error": "API key not set"
            }
            return
        
        try:
            session = await self.get_session()
            url = f"{BIRDEYE_BASE}/defi/v3/token/trending"
            headers = {"X-API-KEY": BIRDEYE_API_KEY}
            params = {
                "chain": "solana",
                "sort_by": "buy_volume_24h",
                "sort_type": "desc",
                "offset": 0,
                "limit": 10
            }
            
            print(f"📡 URL: {url}")
            print(f"🔑 API Key: {BIRDEYE_API_KEY[:10]}...{BIRDEYE_API_KEY[-4:]}")
            
            async with session.get(url, headers=headers, params=params) as resp:
                print(f"📊 Status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    
                    if data.get("success"):
                        items = data.get("data", {}).get("items", [])
                        print(f"✅ SUCCESS - Found {len(items)} trending tokens")
                        
                        if items:
                            print("\n📝 Sample token:")
                            sample = items[0]
                            print(f"   Symbol: {sample.get('symbol')}")
                            print(f"   Address: {sample.get('address')}")
                            print(f"   Price: ${float(sample.get('price', 0)):.6f}")
                            print(f"   Volume 24h: ${float(sample.get('volume_24h', 0)):,.0f}")
                            print(f"   Buy Volume: ${float(sample.get('buy_volume_24h', 0)):,.0f}")
                            print(f"   Sell Volume: ${float(sample.get('sell_volume_24h', 0)):,.0f}")
                            
                            buy_vol = float(sample.get('buy_volume_24h', 0))
                            sell_vol = float(sample.get('sell_volume_24h', 0))
                            if sell_vol > 0:
                                ratio = buy_vol / sell_vol
                                print(f"   Buy/Sell Ratio: {ratio:.2f}x")
                        
                        self.results["birdeye"] = {
                            "status": "working",
                            "data": {"tokens_count": len(items), "sample": items[0] if items else None},
                            "error": None
                        }
                    else:
                        print(f"❌ FAILED - API returned success=false")
                        print(f"   Response: {json.dumps(data, indent=2)[:300]}")
                        self.results["birdeye"] = {
                            "status": "error",
                            "data": None,
                            "error": "API returned success=false"
                        }
                
                elif resp.status == 401:
                    print(f"❌ FAILED - HTTP 401 Unauthorized")
                    print(f"   Your API key is invalid or expired")
                    print(f"   Get a new key at: https://birdeye.so")
                    self.results["birdeye"] = {
                        "status": "error",
                        "data": None,
                        "error": "Invalid API key (401)"
                    }
                
                elif resp.status == 429:
                    print(f"⚠️ WARNING - HTTP 429 Rate Limited")
                    print(f"   You've exceeded the rate limit")
                    self.results["birdeye"] = {
                        "status": "rate_limited",
                        "data": None,
                        "error": "Rate limited (429)"
                    }
                
                else:
                    error_text = await resp.text()
                    print(f"❌ FAILED - HTTP {resp.status}")
                    print(f"   Response: {error_text[:200]}")
                    self.results["birdeye"] = {
                        "status": "error",
                        "data": None,
                        "error": f"HTTP {resp.status}: {error_text[:200]}"
                    }
        
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            self.results["birdeye"] = {
                "status": "error",
                "data": None,
                "error": str(e)
            }
    
    # ========================================================================
    # MORALIS TESTS
    # ========================================================================
    
    async def test_moralis_transfers(self):
        """Test Moralis token transfers (whale tracking)"""
        print("\n" + "="*70)
        print("🔮 TESTING MORALIS - Token Transfers")
        print("="*70)
        
        if not MORALIS_API_KEY or MORALIS_API_KEY == "your_key_here":
            print("⚠️ WARNING: MORALIS_API_KEY not configured")
            print("   Set MORALIS_API_KEY in your .env file")
            print("   Get a free key at: https://moralis.io")
            self.results["moralis"] = {
                "status": "not_configured",
                "data": None,
                "error": "API key not set"
            }
            return
        
        try:
            session = await self.get_session()
            
            # Test with WETH on Ethereum
            weth_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            url = f"{MORALIS_BASE}/erc20/{weth_address}/transfers"
            headers = {"X-API-Key": MORALIS_API_KEY, "Accept": "application/json"}
            params = {
                "chain": "0x1",  # Ethereum mainnet
                "limit": 5
            }
            
            print(f"📡 URL: {url}")
            print(f"🔑 API Key: {MORALIS_API_KEY[:10]}...{MORALIS_API_KEY[-4:]}")
            print(f"   Testing with WETH token")
            
            async with session.get(url, headers=headers, params=params) as resp:
                print(f"📊 Status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    transfers = data.get("result", [])
                    
                    print(f"✅ SUCCESS - Found {len(transfers)} recent transfers")
                    
                    if transfers:
                        print("\n📝 Sample transfer:")
                        sample = transfers[0]
                        print(f"   From: {sample.get('from_address')[:10]}...")
                        print(f"   To: {sample.get('to_address')[:10]}...")
                        print(f"   Value: {sample.get('value')}")
                        print(f"   Block: {sample.get('block_number')}")
                        
                        # Check for whale-sized transfers
                        whale_count = 0
                        for transfer in transfers:
                            value = float(transfer.get("value", 0))
                            decimals = int(transfer.get("decimals", 18))
                            token_amount = value / (10 ** decimals)
                            # Assume ETH price ~$3500
                            usd_value = token_amount * 3500
                            if usd_value >= 50000:  # $50k+
                                whale_count += 1
                        
                        print(f"\n🐋 Whale transfers (>$50k): {whale_count}/{len(transfers)}")
                    
                    self.results["moralis"] = {
                        "status": "working",
                        "data": {"transfers_count": len(transfers), "sample": transfers[0] if transfers else None},
                        "error": None
                    }
                
                elif resp.status == 401:
                    print(f"❌ FAILED - HTTP 401 Unauthorized")
                    print(f"   Your API key is invalid or expired")
                    print(f"   Get a new key at: https://moralis.io")
                    self.results["moralis"] = {
                        "status": "error",
                        "data": None,
                        "error": "Invalid API key (401)"
                    }
                
                elif resp.status == 429:
                    print(f"⚠️ WARNING - HTTP 429 Rate Limited")
                    print(f"   You've exceeded the rate limit")
                    self.results["moralis"] = {
                        "status": "rate_limited",
                        "data": None,
                        "error": "Rate limited (429)"
                    }
                
                else:
                    error_text = await resp.text()
                    print(f"❌ FAILED - HTTP {resp.status}")
                    print(f"   Response: {error_text[:200]}")
                    self.results["moralis"] = {
                        "status": "error",
                        "data": None,
                        "error": f"HTTP {resp.status}: {error_text[:200]}"
                    }
        
        except Exception as e:
            print(f"❌ EXCEPTION: {e}")
            self.results["moralis"] = {
                "status": "error",
                "data": None,
                "error": str(e)
            }
    
    # ========================================================================
    # MAIN TEST RUNNER
    # ========================================================================
    
    async def run_all_tests(self):
        """Run all API tests"""
        print("\n")
        print("╔" + "="*68 + "╗")
        print("║" + " "*15 + "🔍 API VERIFICATION SUITE" + " "*29 + "║")
        print("║" + " "*68 + "║")
        print("║" + f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + " "*42 + "║")
        print("╚" + "="*68 + "╝")
        
        # Run tests sequentially
        await self.test_dexscreener_latest()
        await asyncio.sleep(1)  # Rate limiting
        
        await self.test_dexscreener_search()
        await asyncio.sleep(1)
        
        await self.test_geckoterminal_trending()
        await asyncio.sleep(1)
        
        await self.test_birdeye_trending()
        await asyncio.sleep(1)
        
        await self.test_moralis_transfers()
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary"""
        print("\n")
        print("╔" + "="*68 + "╗")
        print("║" + " "*22 + "📊 TEST SUMMARY" + " "*31 + "║")
        print("╚" + "="*68 + "╝")
        
        for api_name, result in self.results.items():
            status = result["status"]
            
            if status == "working":
                icon = "✅"
                status_text = "WORKING"
            elif status == "not_configured":
                icon = "⚠️"
                status_text = "NOT CONFIGURED"
            elif status == "rate_limited":
                icon = "⏳"
                status_text = "RATE LIMITED"
            elif status == "error":
                icon = "❌"
                status_text = "ERROR"
            else:
                icon = "❓"
                status_text = "UNKNOWN"
            
            print(f"\n{icon} {api_name.upper()}: {status_text}")
            
            if result["error"]:
                print(f"   Error: {result['error']}")
            
            if result["data"]:
                if "pairs_count" in result["data"]:
                    print(f"   Found: {result['data']['pairs_count']} pairs")
                elif "pools_count" in result["data"]:
                    print(f"   Found: {result['data']['pools_count']} pools")
                elif "tokens_count" in result["data"]:
                    print(f"   Found: {result['data']['tokens_count']} tokens")
                elif "transfers_count" in result["data"]:
                    print(f"   Found: {result['data']['transfers_count']} transfers")
        
        # Overall status
        working_count = sum(1 for r in self.results.values() if r["status"] == "working")
        total_count = len(self.results)
        
        print("\n" + "="*70)
        print(f"📊 OVERALL: {working_count}/{total_count} APIs working")
        print("="*70)
        
        # Recommendations
        print("\n💡 RECOMMENDATIONS:")
        
        if self.results["birdeye"]["status"] == "not_configured":
            print("   • Configure BIRDEYE_API_KEY for buy pressure detection")
            print("     Get free key at: https://birdeye.so")
        
        if self.results["moralis"]["status"] == "not_configured":
            print("   • Configure MORALIS_API_KEY for whale tracking")
            print("     Get free key at: https://moralis.io")
        
        if working_count == 0:
            print("   ⚠️ NO APIS WORKING - Check your internet connection")
        elif working_count < 2:
            print("   ⚠️ LIMITED DATA SOURCES - Bot will have reduced opportunities")
        elif working_count >= 2:
            print("   ✅ Sufficient APIs working - Bot should find opportunities")
        
        print("\n")


async def main():
    """Main entry point"""
    verifier = APIVerifier()
    
    try:
        await verifier.run_all_tests()
    finally:
        await verifier.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⌨️ Interrupted by user")
    except Exception as e:
        print(f"\n💀 Fatal error: {e}")
