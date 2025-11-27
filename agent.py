import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timezone, date, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# LOGGING AND SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    """Advanced configuration for competitive paper trading"""
    
    # API Configuration
    RECALL_API_KEY = os.getenv("RECALL_API_KEY", "")
    USE_SANDBOX = os.getenv("RECALL_USE_SANDBOX", "true").lower() == "true"
    COMPETITION_ID = os.getenv("COMPETITION_ID", "")
    
    SANDBOX_URL = "https://api.sandbox.competitions.recall.network"
    PRODUCTION_URL = "https://api.competitions.recall.network"
    
    # *** TIER 1 UPGRADE ***: Configurable Retry Count
    API_RETRY_COUNT = int(os.getenv("API_RETRY_COUNT", "5"))
    
    @property
    def base_url(self):
        return self.SANDBOX_URL if self.USE_SANDBOX else self.PRODUCTION_URL
    
    # Trading Strategy Configuration
    TRADING_INTERVAL = int(os.getenv("TRADING_INTERVAL_SECONDS", "300"))  # 5 min
    
    # Position Sizing (Following competition rules)
    MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "0.000001"))
    BASE_POSITION_SIZE = float(os.getenv("BASE_POSITION_SIZE", "300"))
    MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))  # 20%
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "8"))
    MIN_TRADES_PER_DAY = int(os.getenv("MIN_TRADES_PER_DAY", "3"))
    
    # Risk Management
    STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "-0.08"))  # -8%
    TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))  # +15%
    MAX_PORTFOLIO_RISK = float(os.getenv("MAX_PORTFOLIO_RISK", "0.60"))  # 60%
    MIN_POSITION_VALUE = float(os.getenv("MIN_POSITION_VALUE", "1.0"))  # $1 minimum
    
    # *** TIER 1 UPGRADE ***: New Strategy and Risk Filters
    VOLUME_SURGE_THRESHOLD = float(os.getenv("VOLUME_SURGE_THRESHOLD", "0.50")) # 50%
    MEAN_REVERSION_LOWER_BOUND = float(os.getenv("MEAN_REVERSION_LOWER_BOUND", "-0.10")) # -10% drop
    MEAN_REVERSION_UPPER_BOUND = float(os.getenv("MEAN_REVERSION_UPPER_BOUND", "-0.25")) # -25% drop
    CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD", "0.80")) # Correlated above 80%
    CORRELATED_PAIRS = [("WETH", "WBTC"), ("SNX", "AAVE")]
    
    # Strategy Settings
    STRATEGY_MODE = os.getenv("STRATEGY_MODE", "BALANCED")
    ENABLE_MEAN_REVERSION = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_MOMENTUM = os.getenv("ENABLE_MOMENTUM", "true").lower() == "true"
    
    # *** TIER 1 UPGRADE ***: Added SOL/BSC meme tokens
    TOKENS = {
        # === Ethereum Mainnet (EVM) ===
        "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "chain": "eth", "stable": True},
        "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "chain": "eth", "stable": False},
        "WBTC": {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "chain": "eth", "stable": False},
        "DAI": {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "chain": "eth", "stable": True},
        "UNI": {"address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "chain": "eth", "stable": False},
        "LINK": {"address": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "chain": "eth", "stable": False},
        "AAVE": {"address": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "chain": "eth", "stable": False},
        "SNX": {"address": "0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F", "chain": "eth", "stable": False},
        "CRV": {"address": "0xD533a949740bb3306d119CC777fa900bA034cd52", "chain": "eth", "stable": False},
        "PEPE": {"address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "chain": "eth", "stable": False},
        # === Solana & BSC Tokens (Assumed Addresses/Chains for competition) ===
        "BONK": {"address": "0x5e5bA2C08bC25220c02A7E8eF232CB2c6B109590", "chain": "sol", "stable": False},
        "WIF": {"address": "0x40D1B9fB7e2C64188b0F4f7626960F1F686C10a6", "chain": "sol", "stable": False},
        "FLOKI": {"address": "0xfB5B7eA09aE802d285816F4F5f968c9281a8F1B3", "chain": "bsc", "stable": False},
    }

config = Config()

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class TrackedPosition:
    """Track positions with actual entry data (used for PnL calculation)"""
    symbol: str
    entry_price: float
    entry_amount: float
    entry_value_usd: float
    entry_timestamp: str
    
    # *** TIER 1 UPGRADE ***: Add method for JSON serialization
    def to_json(self):
        return self.__dict__
    
    @classmethod
    def from_json(cls, data):
        return cls(**data)

@dataclass
class Position:
    """Trading position snapshot for decision making"""
    symbol: str
    amount: float
    entry_price: float
    current_price: float
    value: float
    pnl_pct: float
    
    @property
    def is_profitable(self) -> bool:
        return self.pnl_pct > 0
    
    @property
    def should_stop_loss(self) -> bool:
        return self.pnl_pct < config.STOP_LOSS_PCT
    
    @property
    def should_take_profit(self) -> bool:
        return self.pnl_pct > config.TAKE_PROFIT_PCT

# ============================================================================
# PERSISTENCE MANAGER
# *** TIER 1 UPGRADE ***: New Class for State Persistence
# ============================================================================

class PersistenceManager:
    """Handles loading and saving agent state to prevent PnL reset"""
    
    STATE_FILE = "agent_state.json"
    
    @staticmethod
    def load_state() -> Dict:
        """Loads agent state from file"""
        if not os.path.exists(PersistenceManager.STATE_FILE):
            logger.info("💾 No state file found. Starting fresh.")
            return {}
        
        try:
            with open(PersistenceManager.STATE_FILE, 'r') as f:
                state = json.load(f)
            
            # Reconstruct tracked_positions from raw dict
            tracked_positions = {}
            for symbol, data in state.get("tracked_positions", {}).items():
                tracked_positions[symbol] = TrackedPosition.from_json(data)
            state["tracked_positions"] = tracked_positions
            
            logger.info(f"✅ Loaded agent state from {PersistenceManager.STATE_FILE}")
            return state
        except Exception as e:
            logger.error(f"❌ Failed to load state: {e}. Starting fresh.")
            return {}

    @staticmethod
    def save_state(agent):
        """Saves essential agent state to file"""
        try:
            # Prepare data for serialization
            state_data = {
                "tracked_positions": {
                    sym: pos.to_json() for sym, pos in agent.tracked_positions.items()
                },
                "position_history": agent.position_history,
                "trade_history": agent.trade_history,
                "trades_today": agent.trades_today,
                "last_trade_date": str(agent.last_trade_date) if agent.last_trade_date else None,
                # Add any other crucial state variables
            }
            
            with open(PersistenceManager.STATE_FILE, 'w') as f:
                json.dump(state_data, f, indent=4)
            logger.debug(f"💾 Saved agent state to {PersistenceManager.STATE_FILE}")
        except Exception as e:
            logger.error(f"❌ Failed to save state: {e}")

# ============================================================================
# RECALL API CLIENT
# ============================================================================

class RecallAPIClient:
    """Enhanced Recall API Client"""
    
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        env = "SANDBOX" if "sandbox" in base_url else "PRODUCTION"
        logger.info(f"✅ Recall API Client initialized")
        logger.info(f"   Environment: {env}")
        logger.info(f"   Base URL: {base_url}")
    
    # *** TIER 1 UPGRADE ***: Use configurable retry count
    async def _request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request with configurable retry logic and exponential backoff"""
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(config.API_RETRY_COUNT):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method, url, headers=self.headers, timeout=30, **kwargs
                    ) as response:
                        text = await response.text()
                        
                        if response.status >= 400:
                            logger.error(f"API Error ({response.status}): {text}")
                        
                        response.raise_for_status()
                        return json.loads(text)
                        
            except aiohttp.ClientError as e:
                if attempt == config.API_RETRY_COUNT - 1:
                    logger.error(f"API request failed after {config.API_RETRY_COUNT} attempts: {e}")
                    raise
                # Exponential backoff
                await asyncio.sleep(2 ** attempt)

    # ... (rest of RecallAPIClient methods remain the same)
    async def get_portfolio(self, competition_id: str) -> Dict:
        """Get agent balances"""
        return await self._request("GET", f"/api/agent/balances?competitionId={competition_id}")
    
    async def get_token_price(self, token_address: str, chain: str = "eth") -> float:
        """Get token price"""
        result = await self._request("GET", f"/api/price?token={token_address}&chain={chain}")
        return result.get("price", 0.0)
    
    async def execute_trade(
        self,
        competition_id: str,
        from_token: str,
        to_token: str,
        amount: str,
        reason: str = "AI trading decision",
        from_chain: str = None,
        to_chain: str = None
    ) -> Dict:
        """Execute a trade"""
        payload = {
            "competitionId": competition_id,
            "fromToken": from_token,
            "toToken": to_token,
            "amount": amount,
            "reason": reason
        }
        
        if from_chain:
            payload["fromChain"] = from_chain
        if to_chain:
            payload["toChain"] = to_chain
        
        return await self._request("POST", "/api/trade/execute", json=payload)
    
    async def get_trade_history(self, competition_id: str) -> Dict:
        """Get trade history"""
        return await self._request("GET", f"/api/agent/trades?competitionId={competition_id}")
    
    async def get_leaderboard(self, competition_id: str = None) -> Dict:
        """Get leaderboard"""
        if competition_id:
            return await self._request("GET", f"/api/leaderboard?competitionId={competition_id}")
        return await self._request("GET", "/api/leaderboard")
    
    async def get_competitions(self) -> Dict:
        """Get competitions"""
        return await self._request("GET", "/api/competitions")
    
    async def get_user_competitions(self) -> Dict:
        """Get user competitions"""
        return await self._request("GET", "/api/user/competitions")

# ============================================================================
# MARKET ANALYSIS
# ============================================================================

class MarketAnalyzer:
    """Advanced market analysis with multiple indicators and filters"""
    
    # *** TIER 1 UPGRADE ***: Updated CoinGecko IDs to include new tokens
    COINGECKO_IDS = {
        "WETH": "ethereum",
        "WBTC": "bitcoin",
        "UNI": "uniswap",
        "LINK": "chainlink",
        "DAI": "dai",
        "USDC": "usd-coin",
        "AAVE": "aave",
        "SNX": "synthetix-network-token",
        "CRV": "curve-dao-token",
        "PEPE": "pepe",
        "BONK": "bonk",
        "WIF": "dogwifhat",
        "FLOKI": "floki",
    }
    
    async def get_market_data(self, history_days: int = 1) -> Dict[str, Dict]:
        """
        Get comprehensive market data from CoinGecko. 
        Note: This mock only retrieves current data for simplicity; 
        real historical volume data would require a paid CoinGecko subscription or other API.
        """
        try:
            # The current implementation of MarketAnalyzer is focused on fetching 
            # *current* 24h data which includes price, 24h change, and 24h volume.
            # We will use the 24h volume increase to simulate the volume surge filter.
            ids = ",".join(self.COINGECKO_IDS.values())
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": ids,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
                "include_market_cap": "true"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=15) as response:
                    response.raise_for_status()
                    data = await response.json()
                    
                    market_data = {}
                    for symbol, cg_id in self.COINGECKO_IDS.items():
                        if cg_id in data and "usd" in data[cg_id]:
                            token_data = data[cg_id]
                            # *** TIER 1 UPGRADE ***: Mocking 24h Volume Change for Surge Filter
                            # To implement the surge filter, we need V_t and V_{t-24h}.
                            # Since CG `simple` endpoint only gives V_t, we mock a V_change for demo.
                            # In production, you'd need the historical volume.
                            market_data[symbol] = {
                                "price": token_data.get("usd", 0),
                                "change_24h_pct": token_data.get("usd_24h_change", 0.0),
                                "volume_24h": token_data.get("usd_24h_vol", 0.0),
                                "market_cap": token_data.get("usd_market_cap", 0.0),
                                "volume_change_24h_pct": (
                                    token_data.get("usd_24h_change", 0.0) * 0.5 + 50.0
                                ) # Placeholder: replace with real volume history
                            }
                    
                    return market_data
        except Exception as e:
            logger.error(f"Failed to fetch market data: {e}")
            return {}
    
    # ... (classify_signal remains the same for now)
    @staticmethod
    def classify_signal(change_24h: float, strategy: str) -> Tuple[str, str, str]:
        """Classify trading signal based on momentum"""
        
        if strategy == "AGGRESSIVE":
            thresholds = [(3, "STRONG_BULLISH", "HIGH"), (1, "BULLISH", "MEDIUM"), 
                          (0.3, "WEAK_BULLISH", "LOW"), (-0.3, "NEUTRAL", "LOW"),
                          (-1, "WEAK_BEARISH", "LOW"), (-3, "BEARISH", "MEDIUM")]
        elif strategy == "CONSERVATIVE":
            thresholds = [(5, "STRONG_BULLISH", "HIGH"), (3, "BULLISH", "MEDIUM"),
                          (1, "WEAK_BULLISH", "LOW"), (-1, "NEUTRAL", "LOW"),
                          (-3, "WEAK_BEARISH", "LOW"), (-5, "BEARISH", "MEDIUM")]
        else:  # BALANCED
            thresholds = [(4, "STRONG_BULLISH", "HIGH"), (2, "BULLISH", "MEDIUM"),
                          (0.5, "WEAK_BULLISH", "LOW"), (-0.5, "NEUTRAL", "LOW"),
                          (-2, "WEAK_BEARISH", "LOW"), (-4, "BEARISH", "MEDIUM")]
        
        for threshold, signal, conviction in thresholds:
            if change_24h > threshold:
                return signal, conviction, f"{signal.replace('_', ' ').title()} ({change_24h:+.2f}%)"
        
        return "STRONG_BEARISH", "HIGH", f"Strong Bearish ({change_24h:+.2f}%)"
    
    def find_opportunities(self, market_data: Dict, portfolio: Dict) -> List[Tuple[str, float, str]]:
        """Find trading opportunities with advanced filters"""
        opportunities = []
        
        for symbol, data in market_data.items():
            if symbol in ["USDC", "DAI"]:  # Skip stablecoins
                continue
            
            change_24h_pct = data["change_24h_pct"] / 100 # Convert to fraction
            volume_change_24h_pct = data["volume_change_24h_pct"] / 100 # Convert to fraction
            
            # 1. *** TIER 1 UPGRADE ***: Apply Volume Surge Filter
            if volume_change_24h_pct < config.VOLUME_SURGE_THRESHOLD:
                logger.debug(f"⏭️ {symbol}: Volume surge filter failed (Vol change: {volume_change_24h_pct*100:.1f}%)")
                continue

            # 2. *** TIER 1 UPGRADE ***: Apply Strategy Logic
            signal_type = None
            score = 0
            
            # --- Mean Reversion (Buy the dip with high volume) ---
            if config.ENABLE_MEAN_REVERSION and \
               config.MEAN_REVERSION_UPPER_BOUND <= change_24h_pct <= config.MEAN_REVERSION_LOWER_BOUND:
                
                signal_type = "MEAN_REVERSION"
                # Inverse score: bigger dip = higher score
                score = abs(change_24h_pct) * 20.0
                conviction = "HIGH"
                opportunities.append((symbol, score, signal_type, conviction))
                logger.debug(f"✅ MR Signal: {symbol} dipped {change_24h_pct*100:.1f}%, Score: {score:.1f}")
                
            # --- Momentum (Buy the pump with high volume) ---
            elif config.ENABLE_MOMENTUM and change_24h_pct > config.MEAN_REVERSION_LOWER_BOUND:
                
                signal, conviction, _ = self.classify_signal(change_24h_pct * 100, config.STRATEGY_MODE)
                if "BULLISH" in signal:
                    signal_type = "MOMENTUM"
                    score = abs(change_24h_pct) * 10.0 # Momentum is slightly less weighted than a good dip
                    if conviction == "HIGH": score *= 1.5
                    elif conviction == "MEDIUM": score *= 1.2
                    
                    opportunities.append((symbol, score, signal_type, conviction))
                    logger.debug(f"✅ Momentum Signal: {symbol} gained {change_24h_pct*100:.1f}%, Score: {score:.1f}")
            
            if signal_type:
                logger.info(f"✨ Opportunity found for {symbol} ({signal_type}): Score={score:.2f}")

        # Sort by score
        opportunities.sort(key=lambda x: x[1], reverse=True)
        # Dynamic filtering: return top 5 or up to 10 if portfolio is small
        limit = 10 if portfolio.get("total_value", 0) < 50000 else 5
        return opportunities[:limit]

# ============================================================================
# TRADING STRATEGY
# ============================================================================

class TradingStrategy:
    """Advanced trading strategy engine"""
    
    def __init__(self, analyzer: MarketAnalyzer):
        self.analyzer = analyzer
    
    def calculate_position_size(self, total_value: float, conviction: str) -> float:
        """Calculate position size based on conviction"""
        base_size = config.BASE_POSITION_SIZE
        
        # Adjust by conviction
        multipliers = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}
        size = base_size * multipliers.get(conviction, 1.0)
        
        # Cap at max position %
        max_size = total_value * config.MAX_POSITION_PCT
        size = min(size, max_size)
        
        # Respect minimum
        size = max(size, config.MIN_TRADE_SIZE)
        
        return size
    
    # ... (should_rebalance remains the same for now)
    def should_rebalance(self, positions: List[Position]) -> bool:
        """Check if portfolio needs rebalancing"""
        if not positions:
            return False
        
        # Check for positions that need stop-loss or take-profit
        for pos in positions:
            if pos.value < config.MIN_POSITION_VALUE:
                continue  # Skip dust
            if pos.should_stop_loss or pos.should_take_profit:
                return True
        
        # Check concentration risk
        if positions:
            max_position = max(pos.value for pos in positions)
            total_value = sum(pos.value for pos in positions)
            if total_value > 0 and max_position / total_value > 0.4:
                return True
        
        return False
    
    def check_correlation_guard(self, symbol: str, current_positions: List[Position]) -> bool:
        """
        *** TIER 1 UPGRADE ***: Prevents opening a position if a highly 
        correlated asset is already in the portfolio.
        """
        for pos in current_positions:
            pair = tuple(sorted((symbol, pos.symbol)))
            if pair in config.CORRELATED_PAIRS:
                logger.warning(f"🛡️ Correlation Guard: Skipping {symbol} because {pos.symbol} is held (Correlated Pair: {pair})")
                return False
        return True
    
    def generate_trade_decision(
        self,
        portfolio: Dict,
        market_data: Dict,
        opportunities: List[Tuple[str, float, str, str]] # Added conviction to tuple
    ) -> Dict:
        """Generate intelligent trade decision"""
        
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        # Calculate deployed capital
        deployed = sum(h["value"] for h in holdings.values() if h.get("symbol") != "USDC")
        deployed_pct = deployed / total_value if total_value > 0 else 0
        
        usdc_balance = holdings.get("USDC", {}).get("amount", 0)
        usdc_value = holdings.get("USDC", {}).get("value", 0)
        
        # 1. Risk Management: Check existing positions (EXIT LOGIC)
        for pos in positions:
            if pos.value < config.MIN_POSITION_VALUE:
                logger.debug(f"⏭️ Skipping dust position: {pos.symbol} ${pos.value:.4f}")
                continue
            
            if pos.should_stop_loss:
                return {
                    "action": "SELL",
                    "from_token": pos.symbol,
                    "to_token": "USDC",
                    "amount_usd": pos.value * 0.98,
                    "conviction": "HIGH",
                    "reason": f"Stop-loss triggered: {pos.pnl_pct:.1f}% loss on ${pos.value:.2f} position"
                }
            
            if pos.should_take_profit:
                return {
                    "action": "SELL",
                    "from_token": pos.symbol,
                    "to_token": "USDC",
                    "amount_usd": pos.value * 0.5, # Sell half for partial take-profit
                    "conviction": "MEDIUM",
                    "reason": f"Take-profit: {pos.pnl_pct:.1f}% gain on ${pos.value:.2f} position (Partial Exit)"
                }
        
        # 2. Capital and Risk Checks (ENTRY GUARDS)
        if deployed_pct >= config.MAX_PORTFOLIO_RISK:
            return {
                "action": "HOLD",
                "reason": f"Max risk deployed ({deployed_pct*100:.0f}%). Monitoring positions."
            }
        
        if usdc_value < config.MIN_TRADE_SIZE * 1.5:
            return {
                "action": "HOLD",
                "reason": f"Insufficient USDC (${usdc_value:.0f}). Waiting for liquidity."
            }
        
        # 3. Find best opportunity (ENTRY LOGIC)
        if not opportunities:
            return {
                "action": "HOLD",
                "reason": "No clear opportunities with sufficient volume surge. Market is quiet."
            }
        
        existing_symbols = set(pos.symbol for pos in positions if pos.value >= config.MIN_POSITION_VALUE)
        
        for symbol, score, signal_type, conviction in opportunities:
            
            # Skip if we already hold a large position
            if symbol in existing_symbols:
                logger.debug(f"⏭️ Already holding {symbol}.")
                continue
            
            # Skip if maximum number of positions reached
            if len(existing_symbols) >= config.MAX_POSITIONS:
                logger.debug(f"⏭️ Max positions ({config.MAX_POSITIONS}) reached.")
                continue

            # *** TIER 1 UPGRADE ***: Apply Correlation Guard
            if not self.check_correlation_guard(symbol, positions):
                continue
            
            # 4. Execute new position
            token_data = market_data.get(symbol, {})
            change = token_data.get("change_24h_pct", 0) * 100
            
            position_size = self.calculate_position_size(total_value, conviction)
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            return {
                "action": "BUY",
                "from_token": "USDC",
                "to_token": symbol,
                "amount_usd": position_size,
                "conviction": conviction,
                "reason": f"New Entry ({signal_type}): {change:+.2f}% change, Score: {score:.1f}, Conviction: {conviction}"
            }
            
        # If loop finishes without an action
        return {
            "action": "HOLD",
            "reason": "Top opportunities are already held, or correlation guard is active."
        }

# ============================================================================
# ADVANCED TRADING AGENT
# ============================================================================

class AdvancedTradingAgent:
    """Production-ready AI trading agent with proper position tracking and persistence"""
    
    def __init__(self):
        if not config.RECALL_API_KEY:
            raise ValueError("❌ RECALL_API_KEY not set!")
        
        self.client = RecallAPIClient(config.RECALL_API_KEY, config.base_url)
        self.analyzer = MarketAnalyzer()
        self.strategy = TradingStrategy(self.analyzer)
        self.competition_id = None
        
        # *** TIER 1 UPGRADE ***: Load state on startup
        initial_state = PersistenceManager.load_state()
        
        # State variables
        self.tracked_positions: Dict[str, TrackedPosition] = initial_state.get("tracked_positions", {})
        self.position_history = initial_state.get("position_history", [])
        self.trade_history = initial_state.get("trade_history", [])
        self.trades_today = initial_state.get("trades_today", 0)
        
        # Convert last_trade_date string back to date object
        last_date_str = initial_state.get("last_trade_date")
        self.last_trade_date = datetime.strptime(last_date_str, "%Y-%m-%d").date() if last_date_str else None
        
        logger.info("🤖 Advanced Trading Agent initialized")
        logger.info(f"   Total tracked positions loaded: {len(self.tracked_positions)}")
    
    # ... (select_competition remains the same)
    async def select_competition(self) -> str:
        """Select competition"""
        if config.COMPETITION_ID:
            logger.info(f"✅ Using configured competition: {config.COMPETITION_ID}")
            return config.COMPETITION_ID
        
        try:
            logger.info("🔍 Fetching available competitions...")
            user_comps = await self.client.get_user_competitions()
            competitions = user_comps.get("competitions", [])
            
            if not competitions:
                all_comps = await self.client.get_competitions()
                competitions = all_comps.get("competitions", [])
            
            if not competitions:
                raise ValueError("❌ No competitions found!")
            
            active = [c for c in competitions if c.get("status") == "active"]
            comp = active[0] if active else competitions[0]
            comp_id = comp.get("id")
            
            logger.info(f"✅ Auto-selected competition:")
            logger.info(f"   ID: {comp_id}")
            logger.info(f"   Name: {comp.get('name', 'N/A')}")
            return comp_id
            
        except Exception as e:
            raise ValueError(f"❌ Could not select competition: {e}")
    
    # ... (get_portfolio_state remains the same, except for market data call)
    async def get_portfolio_state(self) -> Dict:
        """Get comprehensive portfolio state with FIXED PnL calculation"""
        try:
            portfolio = await self.client.get_portfolio(self.competition_id)
            # Fetch market data once
            market_data = await self.analyzer.get_market_data() 
            
            balances = portfolio.get("balances", [])
            total_value = 0
            holdings = {}
            positions = []
            
            for balance in balances:
                symbol = balance.get("symbol", "")
                amount = float(balance.get("amount", 0))
                current_price = float(balance.get("price", 0))
                value = amount * current_price
                total_value += value
                
                if symbol in config.TOKENS:
                    holdings[symbol] = {
                        "symbol": symbol,
                        "amount": amount,
                        "value": value,
                        "price": current_price,
                        "pct": 0
                    }
                    
                    if not config.TOKENS[symbol]["stable"] and amount > 0:
                        if symbol in self.tracked_positions:
                            entry_pos = self.tracked_positions[symbol]
                            entry_price = entry_pos.entry_price
                            
                            market_price = market_data.get(symbol, {}).get("price", current_price)
                            
                            pnl_pct = ((market_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                            
                            positions.append(Position(
                                symbol=symbol,
                                amount=amount,
                                entry_price=entry_price,
                                current_price=market_price,
                                value=value,
                                pnl_pct=pnl_pct
                            ))
                        else:
                            logger.debug(f"⚠️ {symbol} position not tracked (pre-existing), skipping PnL")
            
            for symbol in holdings:
                if total_value > 0:
                    holdings[symbol]["pct"] = (holdings[symbol]["value"] / total_value * 100)
            
            return {
                "total_value": total_value,
                "holdings": holdings,
                "positions": positions,
                "market_data": market_data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio: {e}")
            return {}
    
    # ... (execute_trade remains the same, but calls PersistenceManager.save_state)
    async def execute_trade(self, decision: Dict, portfolio: Dict) -> bool:
        """Execute trade with FIXED position tracking and persistence"""
        action = decision.get("action", "HOLD").upper()
        
        if action == "HOLD":
            logger.info(f"⏸️ {decision.get('reason', 'Holding position')}")
            # *** TIER 1 UPGRADE ***: Save state even on HOLD to update last_trade_date/trades_today
            PersistenceManager.save_state(self)
            return False
        
        from_symbol = decision.get("from_token", "").upper()
        to_symbol = decision.get("to_token", "").upper()
        amount_usd = decision.get("amount_usd", 0)
        reason = decision.get("reason", "AI trading decision")
        
        if from_symbol not in config.TOKENS or to_symbol not in config.TOKENS:
            logger.error(f"❌ Invalid tokens: {from_symbol} → {to_symbol}")
            return False
        
        from_token = config.TOKENS[from_symbol]
        to_token = config.TOKENS[to_symbol]
        
        holdings = portfolio["holdings"]
        from_holding = holdings.get(from_symbol)
        
        if not from_holding or from_holding["amount"] <= 0:
            logger.error(f"❌ No {from_symbol} balance")
            return False
        
        available = from_holding["amount"]
        from_price = from_holding["price"]
        total_portfolio_value = portfolio["total_value"]
        
        # Safety checks and sizing logic (left out for brevity, assuming original logic is sound)
        # ... [Trade Sizing and Safety Checks] ...
        max_trade_value = total_portfolio_value * 0.25
        max_position_value = total_portfolio_value * config.MAX_POSITION_PCT
        max_allowed = min(max_trade_value, max_position_value)
        
        if amount_usd > max_allowed:
             amount_usd = max_allowed * 0.95
        
        amount_tokens = amount_usd / from_price
        
        if amount_tokens > available:
             amount_tokens = available * 0.98
             amount_usd = amount_tokens * from_price
        
        if amount_tokens < 0.000001:
             logger.warning(f"❌ Trade too small: {amount_tokens:.10f} tokens")
             return False

        # Execute
        try:
            logger.info(f"📤 {action}: {from_symbol} → {to_symbol}")
            logger.info(f"   Amount: {amount_tokens:.6f} {from_symbol} (${amount_usd:.2f})")
            logger.info(f"   Reason: {reason}")
            
            result = await self.client.execute_trade(
                competition_id=self.competition_id,
                from_token=from_token["address"],
                to_token=to_token["address"],
                amount=str(amount_tokens),
                reason=reason,
                from_chain=from_token.get("chain"),
                to_chain=to_token.get("chain")
            )
            
            if result.get("success"):
                logger.info(f"✅ Trade executed successfully!")
                
                timestamp = datetime.now(timezone.utc).isoformat()
                market_data = portfolio.get("market_data", {})
                
                if action == "BUY":
                    to_price = market_data.get(to_symbol, {}).get("price", 0)
                    if to_price == 0:
                         to_price = amount_usd / amount_tokens if amount_tokens > 0 else 0

                    estimated_receive_amount = amount_usd / to_price if to_price > 0 else 0
                    
                    # Track new position
                    # *** TIER 1 UPGRADE ***: Use TrackedPosition dataclass
                    self.tracked_positions[to_symbol] = TrackedPosition(
                        symbol=to_symbol,
                        entry_price=to_price,
                        entry_amount=estimated_receive_amount,
                        entry_timestamp=timestamp,
                        entry_value_usd=amount_usd
                    )
                    logger.info(f"📍 Tracked entry: {to_symbol} @ ${to_price:.2f}")
                    
                elif action == "SELL":
                    # Track position exit
                    if from_symbol in self.tracked_positions:
                        entry_pos = self.tracked_positions[from_symbol]
                        exit_price = market_data.get(from_symbol, {}).get("price", from_price)
                        actual_pnl_pct = ((exit_price - entry_pos.entry_price) / entry_pos.entry_price * 100) if entry_pos.entry_price > 0 else 0
                        
                        logger.info(f"📍 Exit: {from_symbol} | PnL: {actual_pnl_pct:+.2f}%")
                        
                        # Archive to history
                        self.position_history.append({
                            "symbol": from_symbol,
                            "entry_price": entry_pos.entry_price,
                            "exit_price": exit_price,
                            "pnl_pct": actual_pnl_pct,
                            "entry_timestamp": entry_pos.entry_timestamp,
                            "exit_timestamp": timestamp
                        })
                        
                        # Remove from active tracking (NOTE: This assumes a full exit. For partial exit, a weighted average update is needed)
                        # For now, we assume a full exit or a partial exit that leaves the original entry price tracked.
                        if "Partial Exit" not in reason:
                            del self.tracked_positions[from_symbol]

                # Track daily trades
                today = datetime.now(timezone.utc).date()
                if self.last_trade_date != today:
                    self.trades_today = 0
                    self.last_trade_date = today
                self.trades_today += 1
                
                self.trade_history.append({
                    "timestamp": timestamp,
                    "action": action,
                    "from": from_symbol,
                    "to": to_symbol,
                    "amount": amount_usd,
                    "reason": reason
                })

                # *** TIER 1 UPGRADE ***: Save state after every successful trade
                PersistenceManager.save_state(self)

                logger.info(f"   Daily trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
                return True
            else:
                logger.error(f"❌ Trade failed: {result.get('error', 'Unknown')}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Trade error: {e}")
            return False
    
    async def run(self):
        """Main trading loop"""
        logger.info("="*80)
        logger.info("🚀 ADVANCED PAPER TRADING AGENT - TIER 1 ONLINE 🚀")
        logger.info("="*80)
        
        try:
            self.competition_id = await self.select_competition()
        except ValueError as e:
            logger.error(f"Fatal error: {e}")
            return
            
        while True:
            logger.info("\n" + "~"*50)
            logger.info(f"Iteration start: {datetime.now(timezone.utc).isoformat()}")
            
            # 1. Get Portfolio State
            portfolio = await self.get_portfolio_state()
            if not portfolio or portfolio.get("total_value", 0) == 0:
                logger.error("🚫 Cannot retrieve portfolio state. Skipping this cycle.")
                await asyncio.sleep(config.TRADING_INTERVAL)
                continue
            
            logger.info(f"💰 Portfolio Value: ${portfolio['total_value']:.2f}")
            logger.info(f"📊 Active Positions: {len(portfolio['positions'])}/{config.MAX_POSITIONS}")
            
            # 2. Get Market Opportunities
            opportunities = self.analyzer.find_opportunities(
                portfolio["market_data"], portfolio
            )
            
            # 3. Generate Trade Decision
            decision = self.strategy.generate_trade_decision(
                portfolio, portfolio["market_data"], opportunities
            )
            
            # 4. Execute Trade
            await self.execute_trade(decision, portfolio)
            
            # 5. Wait for next interval
            logger.info(f"💤 Sleeping for {config.TRADING_INTERVAL} seconds...")
            await asyncio.sleep(config.TRADING_INTERVAL)

if __name__ == "__main__":
    if not config.RECALL_API_KEY and config.USE_SANDBOX:
        logger.warning("⚠️ RECALL_API_KEY is not set. Using SANDBOX mode with mock key. Trades may fail.")
        # Mock key for demo/sandbox if not set
        config.RECALL_API_KEY = "mock_key" 

    agent = AdvancedTradingAgent()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Agent stopped by user.")
        PersistenceManager.save_state(agent)
        logger.info("Final state saved.")
    except Exception as e:
        logger.error(f"Critical error in main loop: {e}")
        PersistenceManager.save_state(agent)
        logger.info("Final state saved due to critical error.")