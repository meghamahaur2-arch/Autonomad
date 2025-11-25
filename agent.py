"""
Recall Network Paper Trading Agent - FIXED VERSION
Key fixes:
1. Removed /api prefix from endpoints (it's already in base_url)
2. Fixed endpoint paths
3. Added better error handling
"""

import os
import sys
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict

import aiohttp
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

class PaperConfig:
    """Configuration for Recall Paper Trading"""
    
    RECALL_API_KEY = os.getenv("RECALL_API_KEY", "")
    USE_SANDBOX = os.getenv("RECALL_USE_SANDBOX", "true")
    
    # Fixed: Base URLs already include /api
    SANDBOX_URL = "https://api.sandbox.competitions.recall.network/api"
    PRODUCTION_URL = "https://api.competitions.recall.network/api"
    
    @property
    def base_url(self):
        return self.SANDBOX_URL if self.USE_SANDBOX else self.PRODUCTION_URL
    
    # LLM Configuration
    GAIA_API_KEY = os.getenv("GAIA_API_KEY")
    GAIA_NODE_URL = os.getenv("GAIA_NODE_URL", "https://qwen7b.gaia.domains/v1")
    GAIA_MODEL_NAME = os.getenv("GAIA_MODEL_NAME")
    
    # Trading Configuration
    TRADING_INTERVAL = int(os.getenv("TRADING_INTERVAL_SECONDS", "900"))
    MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "100"))
    MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.30"))
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "4"))
    
    # Supported tokens (Ethereum mainnet addresses)
    TOKENS = {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    }

config = PaperConfig()

# ============================================================================
# RECALL API CLIENT
# ============================================================================

class RecallAPIClient:
    """Client for Recall Network Competition API"""
    
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        env = "SANDBOX" if "sandbox" in base_url else "PRODUCTION"
        logger.info(f"✅ Recall API Client initialized")
        logger.info(f"   Environment: {env}")
        logger.info(f"   Base URL: {base_url}")
    
    async def _request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request"""
        # Fixed: Don't add /api prefix since it's in base_url
        url = f"{self.base_url}{endpoint}"
        
        logger.debug(f"Request: {method} {url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.request(
                    method, url, headers=self.headers, **kwargs
                ) as response:
                    text = await response.text()
                    
                    # Log response for debugging
                    if response.status >= 400:
                        logger.error(f"API Error ({response.status}): {text}")
                    
                    response.raise_for_status()
                    
                    # Try to parse JSON
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON response: {text}")
                        raise
                        
            except aiohttp.ClientError as e:
                logger.error(f"API request failed: {method} {endpoint} - {e}")
                raise
    
    async def get_portfolio(self) -> Dict:
        """Get current portfolio"""
        # Fixed: Correct endpoint path
        return await self._request("GET", "/agent/balances")
    
    async def get_token_price(self, token_address: str, chain: str = "evm") -> float:
        """Get token price"""
        result = await self._request(
            "GET",
            f"/price?token={token_address}&chain={chain}&specificChain=eth"
        )
        return result.get("price", 0.0)
    
    async def execute_trade(
        self,
        from_token: str,
        to_token: str,
        amount: str,
        reason: str = "AI trading decision"
    ) -> Dict:
        """Execute a token swap"""
        payload = {
            "fromToken": from_token,
            "toToken": to_token,
            "amount": amount,
            "reason": reason
        }
        
        logger.info(f"🔄 Executing trade:")
        logger.info(f"   From: {from_token[:10]}...")
        logger.info(f"   To: {to_token[:10]}...")
        logger.info(f"   Amount: {amount}")
        
        # Fixed: Correct endpoint path
        return await self._request("POST", "/trade/execute", json=payload)
    
    async def get_leaderboard(self) -> Dict:
        """Get competition leaderboard"""
        return await self._request("GET", "/competition/leaderboard")

# ============================================================================
# TECHNICAL ANALYSIS
# ============================================================================

class TechnicalAnalysis:
    """Simple technical analysis for paper trading"""
    
    @staticmethod
    def analyze_momentum(price_change_24h: float) -> tuple:
        """Analyze momentum from 24h price change"""
        
        if price_change_24h > 8:
            return "STRONG_BULLISH", "HIGH", f"Strong upward momentum (+{price_change_24h:.1f}%)"
        elif price_change_24h > 4:
            return "BULLISH", "MEDIUM", f"Bullish momentum (+{price_change_24h:.1f}%)"
        elif price_change_24h < -8:
            return "STRONG_BEARISH", "HIGH", f"Strong downward momentum ({price_change_24h:.1f}%)"
        elif price_change_24h < -4:
            return "BEARISH", "MEDIUM", f"Bearish momentum ({price_change_24h:.1f}%)"
        else:
            return "NEUTRAL", "LOW", f"Neutral momentum ({price_change_24h:.1f}%)"

# ============================================================================
# MARKET DATA
# ============================================================================

class MarketData:
    """Fetch market data from external sources"""
    
    COINGECKO_IDS = {
        "WETH": "ethereum",
        "WBTC": "bitcoin",
        "UNI": "uniswap",
        "LINK": "chainlink",
        "DAI": "dai",
        "USDC": "usd-coin"
    }
    
    async def get_prices_and_changes(self) -> Dict:
        """Get current prices and 24h changes from CoinGecko"""
        try:
            ids = ",".join(self.COINGECKO_IDS.values())
            url = f"https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": ids,
                "vs_currencies": "usd",
                "include_24hr_change": "true"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                    
                    prices = {}
                    for symbol, cg_id in self.COINGECKO_IDS.items():
                        if cg_id in data:
                            prices[symbol] = {
                                "price": data[cg_id]["usd"],
                                "change_24h": data[cg_id].get("usd_24h_change", 0.0)
                            }
                    
                    return prices
        except Exception as e:
            logger.error(f"Failed to fetch market data: {e}")
            return {}

# ============================================================================
# PAPER TRADING AGENT
# ============================================================================

class PaperTradingAgent:
    """AI Trading Agent for Paper Trading Competitions"""
    
    def __init__(self):
        if not config.RECALL_API_KEY:
            raise ValueError(
                "❌ RECALL_API_KEY not set!\n"
                "Get your API key from: https://register.recall.network/\n"
                "Then add it to your .env file: RECALL_API_KEY=your_key_here"
            )
        
        self.client = RecallAPIClient(config.RECALL_API_KEY, config.base_url)
        self.llm = OpenAI(
            api_key=config.GAIA_API_KEY,
            base_url=config.GAIA_NODE_URL
        )
        self.model = config.GAIA_MODEL_NAME
        self.market_data = MarketData()
        self.tokens = config.TOKENS
        
        logger.info("🤖 Paper Trading Agent initialized")
        logger.info(f"   Trading tokens: {', '.join(self.tokens.keys())}")
    
    async def get_portfolio_analysis(self) -> Dict:
        """Get and analyze current portfolio"""
        try:
            portfolio = await self.client.get_portfolio()
            market_prices = await self.market_data.get_prices_and_changes()
            
            total_value = portfolio.get("totalValue", 0)
            tokens = portfolio.get("tokens", [])
            
            holdings = {}
            for token in tokens:
                symbol = token.get("symbol", "")
                if symbol in self.tokens:
                    holdings[symbol] = {
                        "amount": token.get("amount", 0),
                        "value": token.get("value", 0),
                        "price": token.get("price", 0),
                        "pct": (token.get("value", 0) / total_value * 100) if total_value > 0 else 0
                    }
            
            return {
                "total_value": total_value,
                "holdings": holdings,
                "market_prices": market_prices,
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio: {e}")
            return {}
    
    def build_context(self, analysis: Dict) -> str:
        """Build context for LLM"""
        total_value = analysis.get("total_value", 0)
        holdings = analysis.get("holdings", {})
        market_prices = analysis.get("market_prices", {})
        
        context = f"""# Portfolio Status
- **Total Value**: ${total_value:,.2f}
- **Environment**: {'SANDBOX (Testing)' if config.USE_SANDBOX else 'PRODUCTION (Live Competition)'}
- **Max Positions**: {config.MAX_POSITIONS}

## Current Holdings:
"""
        
        if holdings:
            for symbol, data in holdings.items():
                context += (
                    f"- **{symbol}**: {data['amount']:.6f} @ ${data['price']:,.2f} = "
                    f"${data['value']:,.2f} ({data['pct']:.1f}%)\n"
                )
        else:
            context += "- No token holdings (100% USDC)\n"
        
        context += f"\n## Market Analysis:\n"
        
        for symbol, data in market_prices.items():
            if symbol == "USDC":
                continue
            
            price = data["price"]
            change = data["change_24h"]
            momentum, conviction, reason = TechnicalAnalysis.analyze_momentum(change)
            
            context += (
                f"- **{symbol}**: ${price:,.2f} | 24h: {change:+.2f}% | "
                f"Signal: {momentum} ({conviction})\n"
            )
        
        context += f"""
## Trading Rules:
- This is SPOT TRADING (no leverage, no shorts)
- To go bullish: BUY token with USDC
- To go bearish: SELL token back to USDC
- USDC is your base currency (like cash)
- Min trade: ${config.MIN_TRADE_SIZE}
- Max per position: {config.MAX_POSITION_PCT*100:.0f}% of portfolio
"""
        
        return context
    
    async def get_trading_decision(self, context: str) -> Optional[Dict]:
        """Get trading decision from LLM"""
        
        system_prompt = """You are a paper trading bot competing in Recall Network competitions.

**IMPORTANT: SPOT TRADING ONLY**
- To go LONG: BUY token with USDC (e.g., USDC → WETH)
- To go SHORT: SELL token to USDC (e.g., WETH → USDC)
- USDC = cash position

**Strategy:**
1. Rotate between high-conviction tokens and USDC
2. Take profits when momentum weakens
3. Buy when strong bullish momentum
4. Sell when strong bearish momentum
5. Hold USDC when uncertain

**Response Format (JSON only):**
{
  "action": "BUY" or "SELL" or "HOLD",
  "from_token": "USDC" or token symbol,
  "to_token": "WETH" or "USDC" or token symbol,
  "amount_usd": 500,
  "conviction": "HIGH" or "MEDIUM" or "LOW",
  "reason": "Brief explanation"
}

Examples:
- Bullish on ETH: {"action": "BUY", "from_token": "USDC", "to_token": "WETH", "amount_usd": 1000, "conviction": "HIGH", "reason": "Strong upward momentum"}
- Take profit: {"action": "SELL", "from_token": "WBTC", "to_token": "USDC", "amount_usd": 800, "conviction": "MEDIUM", "reason": "Taking profits"}
- Wait: {"action": "HOLD", "reason": "Waiting for better setup"}

Return ONLY valid JSON, no markdown."""

        user_prompt = f"Analyze this portfolio and decide the next trade:\n\n{context}"
        
        try:
            response = self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=500
            )
            
            content = response.choices[0].message.content.strip()
            
            # Parse JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            decision = json.loads(content)
            logger.info(f"🤖 LLM Decision: {decision}")
            return decision
            
        except Exception as e:
            logger.error(f"LLM decision failed: {e}")
            return None
    
    async def execute_decision(self, decision: Dict, analysis: Dict) -> bool:
        """Execute trading decision"""
        action = decision.get("action", "HOLD").upper()
        
        if action == "HOLD":
            logger.info(f"⏸️  Holding: {decision.get('reason', 'No action')}")
            return False
        
        from_symbol = decision.get("from_token", "").upper()
        to_symbol = decision.get("to_token", "").upper()
        amount_usd = decision.get("amount_usd", 0)
        reason = decision.get("reason", "AI trading decision")
        
        if from_symbol not in self.tokens or to_symbol not in self.tokens:
            logger.error(f"Invalid tokens: {from_symbol} → {to_symbol}")
            return False
        
        from_address = self.tokens[from_symbol]
        to_address = self.tokens[to_symbol]
        
        # Get from_token balance
        holdings = analysis["holdings"]
        from_token_data = holdings.get(from_symbol)
        
        if not from_token_data:
            logger.error(f"No {from_symbol} balance")
            return False
        
        # Calculate amount in from_token units
        from_price = from_token_data["price"]
        amount_tokens = amount_usd / from_price
        
        # Check balance
        available = from_token_data["amount"]
        if amount_tokens > available:
            logger.warning(f"Insufficient {from_symbol}: need {amount_tokens:.6f}, have {available:.6f}")
            amount_tokens = available * 0.95  # Use 95%
        
        # Validate minimum trade size
        trade_value = amount_tokens * from_price
        if trade_value < config.MIN_TRADE_SIZE:
            logger.warning(f"Trade too small: ${trade_value:.2f} < ${config.MIN_TRADE_SIZE}")
            return False
        
        # Execute trade
        try:
            result = await self.client.execute_trade(
                from_token=from_address,
                to_token=to_address,
                amount=str(amount_tokens),
                reason=reason
            )
            
            if result.get("success"):
                tx = result.get("transaction", {})
                logger.info(f"✅ Trade executed: {from_symbol} → {to_symbol}")
                logger.info(f"   From: {tx.get('fromAmount', 0)} {from_symbol}")
                logger.info(f"   To: {tx.get('toAmount', 0)} {to_symbol}")
                logger.info(f"   Price: ${tx.get('price', 0):,.2f}")
                return True
            else:
                logger.error(f"❌ Trade failed: {result}")
                return False
                
        except Exception as e:
            logger.error(f"Trade execution error: {e}")
            return False
    
    async def run(self):
        """Main trading loop"""
        logger.info("="*80)
        logger.info("🚀 RECALL PAPER TRADING AGENT STARTED")
        logger.info("✅ ZERO RISK - Paper trading with virtual funds")
        logger.info(f"   Environment: {'SANDBOX' if config.USE_SANDBOX else 'PRODUCTION'}")
        logger.info("="*80)
        
        cycle = 0
        
        while True:
            cycle += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"Trading Cycle #{cycle} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"{'='*60}")
            
            try:
                # Get portfolio analysis
                analysis = await self.get_portfolio_analysis()
                
                if not analysis:
                    logger.warning("Failed to get portfolio")
                    await asyncio.sleep(config.TRADING_INTERVAL)
                    continue
                
                # Build context
                context = self.build_context(analysis)
                
                # Get decision
                decision = await self.get_trading_decision(context)
                
                if not decision:
                    logger.warning("Failed to get decision")
                    await asyncio.sleep(config.TRADING_INTERVAL)
                    continue
                
                # Execute decision
                executed = await self.execute_decision(decision, analysis)
                
                if executed:
                    logger.info("✅ Trade cycle completed")
                else:
                    logger.info("ℹ️  No trade executed")
                
                # Log leaderboard position
                try:
                    leaderboard = await self.client.get_leaderboard()
                    count = len(leaderboard.get('leaderboard', []))
                    logger.info(f"📈 Competition agents: {count}")
                except:
                    pass
                
            except Exception as e:
                logger.error(f"Error in trading cycle: {e}")
            
            # Wait for next cycle
            logger.info(f"⏳ Waiting {config.TRADING_INTERVAL} seconds...")
            await asyncio.sleep(config.TRADING_INTERVAL)

# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Main entry point"""
    logger.info("="*80)
    logger.info("📄 RECALL PAPER TRADING AGENT")
    logger.info("="*80)
    logger.info("This is PAPER TRADING with virtual funds.")
    logger.info("✅ Zero risk - perfect for testing!")
    logger.info("="*80)
    
    agent = PaperTradingAgent()
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n⏹️  Agent stopped by user")
    except Exception as e:
        logger.critical(f"❌ Fatal error: {e}")
        sys.exit(1)