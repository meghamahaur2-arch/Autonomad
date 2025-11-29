# 🚀 Advanced Paper Trading Agent - Production Ready (Fixed & Optimized)
#!/usr/bin/env python3
"""
Advanced Paper Trading Agent - Production Ready (v3.0)
=======================================================
A robust, fault-tolerant trading agent for the Recall Network competition.

Features:
- Persistent state management with atomic writes
- Circuit breaker pattern for API resilience
- Weighted position tracking for partial exits
- Advanced risk management with correlation guards
- Comprehensive metrics and observability
- Graceful shutdown handling
- FIXED: Volume surge calculation
- FIXED: Mean reversion bounds
- FIXED: Opportunity detection sensitivity
- ADDED: Enhanced debugging and diagnostics
- ADDED: Adaptive thresholds based on market conditions
"""

import os
import sys
import json
import logging
import asyncio
import signal
import hashlib
import tempfile
import shutil
from datetime import datetime, timezone, date, timedelta
from typing import Optional, Dict, List, Tuple, Any, Set
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from contextlib import asynccontextmanager
from collections import deque
from decimal import Decimal, ROUND_DOWN
import traceback
import statistics

import aiohttp
from aiohttp import ClientTimeout, TCPConnector
from dotenv import load_dotenv

load_dotenv()


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with rotation and formatting"""
    logger = logging.getLogger("TradingAgent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(console_handler)
    
    # File handler for persistence
    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"trading_{date.today().isoformat()}.log"),
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)
    
    return logger


logger = setup_logging(os.getenv("LOG_LEVEL", "INFO"))


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class TradingAction(Enum):
    BUY = auto()
    SELL = auto()
    HOLD = auto()


class SignalType(Enum):
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    REBALANCE = "REBALANCE"
    BREAKOUT = "BREAKOUT"
    VOLUME_SPIKE = "VOLUME_SPIKE"


class Conviction(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CircuitState(Enum):
    CLOSED = auto()    # Normal operation
    OPEN = auto()      # Failing, reject requests
    HALF_OPEN = auto() # Testing if service recovered


class MarketRegime(Enum):
    """Market condition classification"""
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class TokenConfig:
    """Immutable token configuration"""
    address: str
    chain: str
    stable: bool
    decimals: int = 18
    min_trade_size: float = 0.000001


class Config:
    """
    Advanced configuration with validation and environment variable support.
    All monetary values are in USD unless otherwise specified.
    
    OPTIMIZED: Adjusted thresholds for better opportunity detection
    """
    
    # API Configuration
    RECALL_API_KEY: str = os.getenv("RECALL_API_KEY", "")
    USE_SANDBOX: bool = os.getenv("RECALL_USE_SANDBOX", "true").lower() == "true"
    COMPETITION_ID: str = os.getenv("COMPETITION_ID", "")
    SANDBOX_URL: str = "https://api.sandbox.competitions.recall.network"
    PRODUCTION_URL: str = "https://api.competitions.recall.network"
    
    # Retry and Resilience
    API_RETRY_COUNT: int = int(os.getenv("API_RETRY_COUNT", "5"))
    API_TIMEOUT_SECONDS: int = int(os.getenv("API_TIMEOUT_SECONDS", "30"))
    API_BACKOFF_BASE: float = float(os.getenv("API_BACKOFF_BASE", "2.0"))
    API_BACKOFF_MAX: float = float(os.getenv("API_BACKOFF_MAX", "60.0"))
    
    # Circuit Breaker
    CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
    CIRCUIT_RECOVERY_TIMEOUT: int = int(os.getenv("CIRCUIT_RECOVERY_TIMEOUT", "60"))
    
    # Trading Intervals
    TRADING_INTERVAL: int = int(os.getenv("TRADING_INTERVAL_SECONDS", "300"))
    MARKET_DATA_CACHE_TTL: int = int(os.getenv("MARKET_DATA_CACHE_TTL", "60"))
    
    # Position Sizing
    MIN_TRADE_SIZE: float = float(os.getenv("MIN_TRADE_SIZE", "0.000001"))
    BASE_POSITION_SIZE: float = float(os.getenv("BASE_POSITION_SIZE", "300"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "8"))
    MIN_TRADES_PER_DAY: int = int(os.getenv("MIN_TRADES_PER_DAY", "3"))
    MIN_POSITION_VALUE: float = float(os.getenv("MIN_POSITION_VALUE", "1.0"))
    
    # Risk Management
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "-0.08"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))
    TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.05"))
    MAX_PORTFOLIO_RISK: float = float(os.getenv("MAX_PORTFOLIO_RISK", "0.70"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "-0.10"))
    
    # =========================================================================
    # FIXED: Strategy Filters - More permissive thresholds
    # =========================================================================
    VOLUME_SURGE_THRESHOLD: float = float(os.getenv("VOLUME_SURGE_THRESHOLD", "0.20"))  # Lowered from 0.50
    MEAN_REVERSION_LOWER_BOUND: float = float(os.getenv("MEAN_REVERSION_LOWER_BOUND", "-0.03"))  # -3%
    MEAN_REVERSION_UPPER_BOUND: float = float(os.getenv("MEAN_REVERSION_UPPER_BOUND", "-0.15"))  # -15%
    MOMENTUM_THRESHOLD: float = float(os.getenv("MOMENTUM_THRESHOLD", "0.01"))  # Lowered from 0.02
    BREAKOUT_THRESHOLD: float = float(os.getenv("BREAKOUT_THRESHOLD", "0.05"))  # 5% for breakout
    
    # Strategy Mode
    STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "BALANCED")
    ENABLE_MEAN_REVERSION: bool = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_MOMENTUM: bool = os.getenv("ENABLE_MOMENTUM", "true").lower() == "true"
    ENABLE_TRAILING_STOP: bool = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
    ENABLE_BREAKOUT: bool = os.getenv("ENABLE_BREAKOUT", "true").lower() == "true"
    ENABLE_VOLUME_SPIKE: bool = os.getenv("ENABLE_VOLUME_SPIKE", "true").lower() == "true"
    
    # Adaptive Mode - adjusts thresholds based on market conditions
    ENABLE_ADAPTIVE_MODE: bool = os.getenv("ENABLE_ADAPTIVE_MODE", "true").lower() == "true"
    
    # Persistence
    STATE_FILE: str = os.getenv("STATE_FILE", "agent_state.json")
    STATE_BACKUP_COUNT: int = int(os.getenv("STATE_BACKUP_COUNT", "5"))
    
    # Correlated pairs to avoid concentration
    CORRELATED_PAIRS: List[Tuple[str, str]] = [
        ("WETH", "WBTC"),
        ("SNX", "AAVE"),
        ("UNI", "AAVE"),
        ("BONK", "FLOKI"),
        ("BONK", "WIF"),
        ("FLOKI", "WIF"),
    ]
    
    # Token Registry - FIXED addresses
    TOKENS: Dict[str, TokenConfig] = {
        # Ethereum Mainnet Stablecoins
        "USDC": TokenConfig("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "eth", True, 6),
        "DAI": TokenConfig("0x6B175474E89094C44Da98b954EedeAC495271d0F", "eth", True, 18),
        
        # Ethereum Mainnet Blue Chips
        "WETH": TokenConfig("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "eth", False, 18),
        "WBTC": TokenConfig("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "eth", False, 8),
        
        # DeFi Tokens
        "UNI": TokenConfig("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "eth", False, 18),
        "LINK": TokenConfig("0x514910771AF9Ca656af840dff83E8264EcF986CA", "eth", False, 18),
        "AAVE": TokenConfig("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "eth", False, 18),
        "SNX": TokenConfig("0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F", "eth", False, 18),
        
        # Meme Tokens (ERC-20 versions)
        "BONK": TokenConfig("0x1151CB3d861920e07a38e03eEAD12C32178567F6", "eth", False, 5),
        "FLOKI": TokenConfig("0xcf0C122c6b73ff809C693DB761e7BaeBe62b6a2E", "eth", False, 9),
        "PEPE": TokenConfig("0x6982508145454Ce325dDbE47a25d4ec3d2311933", "eth", False, 18),
        "SHIB": TokenConfig("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", "eth", False, 18),
    }
    
    @property
    def base_url(self) -> str:
        return self.SANDBOX_URL if self.USE_SANDBOX else self.PRODUCTION_URL
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors"""
        errors = []
        
        if not self.RECALL_API_KEY:
            errors.append("RECALL_API_KEY is required")
        if self.MAX_POSITION_PCT <= 0 or self.MAX_POSITION_PCT > 1:
            errors.append("MAX_POSITION_PCT must be between 0 and 1")
        if self.MAX_PORTFOLIO_RISK <= 0 or self.MAX_PORTFOLIO_RISK > 1:
            errors.append("MAX_PORTFOLIO_RISK must be between 0 and 1")
        if self.STOP_LOSS_PCT >= 0:
            errors.append("STOP_LOSS_PCT must be negative")
        if self.TAKE_PROFIT_PCT <= 0:
            errors.append("TAKE_PROFIT_PCT must be positive")
        if self.TRADING_INTERVAL < 60:
            errors.append("TRADING_INTERVAL should be at least 60 seconds")
        
        return errors


config = Config()


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class TrackedPosition:
    """
    Tracks a position with weighted average entry for partial fills/exits.
    Supports serialization for persistence.
    """
    symbol: str
    entry_price: float
    entry_amount: float
    entry_value_usd: float
    entry_timestamp: str
    highest_price: float = 0.0
    lowest_price_since_entry: float = float('inf')
    total_cost_basis: float = 0.0
    
    def __post_init__(self):
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price
        if self.lowest_price_since_entry == float('inf'):
            self.lowest_price_since_entry = self.entry_price
        if self.total_cost_basis == 0.0:
            self.total_cost_basis = self.entry_value_usd
    
    def update_for_add(self, amount: float, price: float, value_usd: float):
        """Update position when adding (averaging in)"""
        new_total_amount = self.entry_amount + amount
        new_total_cost = self.total_cost_basis + value_usd
        
        # Weighted average entry price
        self.entry_price = new_total_cost / new_total_amount if new_total_amount > 0 else price
        self.entry_amount = new_total_amount
        self.total_cost_basis = new_total_cost
        self.entry_value_usd = new_total_cost
    
    def update_for_partial_exit(self, amount_sold: float):
        """Update position for partial exit (maintains entry price)"""
        if amount_sold >= self.entry_amount:
            return None  # Full exit
        
        ratio_remaining = (self.entry_amount - amount_sold) / self.entry_amount
        self.entry_amount -= amount_sold
        self.total_cost_basis *= ratio_remaining
        self.entry_value_usd = self.total_cost_basis
        return self
    
    def update_price_tracking(self, current_price: float):
        """Update highest and lowest price tracking"""
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price_since_entry:
            self.lowest_price_since_entry = current_price
    
    def get_trailing_stop_price(self) -> float:
        """Calculate trailing stop price"""
        return self.highest_price * (1 - config.TRAILING_STOP_PCT)
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        # Handle infinity for JSON serialization
        if data['lowest_price_since_entry'] == float('inf'):
            data['lowest_price_since_entry'] = None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TrackedPosition':
        # Handle None for infinity
        if data.get('lowest_price_since_entry') is None:
            data['lowest_price_since_entry'] = float('inf')
        return cls(**data)


@dataclass
class Position:
    """Current position snapshot for decision making"""
    symbol: str
    amount: float
    entry_price: float
    current_price: float
    value: float
    pnl_pct: float
    highest_price: float = 0.0
    lowest_price: float = 0.0
    
    @property
    def is_profitable(self) -> bool:
        return self.pnl_pct > 0
    
    @property
    def should_stop_loss(self) -> bool:
        return self.pnl_pct <= config.STOP_LOSS_PCT * 100
    
    @property
    def should_take_profit(self) -> bool:
        return self.pnl_pct >= config.TAKE_PROFIT_PCT * 100
    
    @property
    def should_trailing_stop(self) -> bool:
        if not config.ENABLE_TRAILING_STOP or self.highest_price <= 0:
            return False
        trailing_stop_price = self.highest_price * (1 - config.TRAILING_STOP_PCT)
        return self.current_price <= trailing_stop_price and self.is_profitable


@dataclass
class TradeDecision:
    """Structured trade decision"""
    action: TradingAction
    from_token: str = ""
    to_token: str = ""
    amount_usd: float = 0.0
    conviction: Conviction = Conviction.MEDIUM
    signal_type: SignalType = SignalType.MOMENTUM
    reason: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class TradingMetrics:
    """Performance metrics for monitoring"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    trades_today: int = 0
    daily_pnl_usd: float = 0.0
    peak_portfolio_value: float = 0.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingMetrics':
        # Handle missing fields for backward compatibility
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


@dataclass
class MarketSnapshot:
    """Snapshot of market conditions for adaptive strategies"""
    avg_change_24h: float = 0.0
    volatility: float = 0.0
    bullish_count: int = 0
    bearish_count: int = 0
    regime: MarketRegime = MarketRegime.SIDEWAYS
    
    @classmethod
    def from_market_data(cls, market_data: Dict[str, Dict]) -> 'MarketSnapshot':
        """Calculate market snapshot from market data"""
        if not market_data:
            return cls()
        
        changes = []
        bullish = 0
        bearish = 0
        
        for symbol, data in market_data.items():
            change = data.get("change_24h_pct", 0)
            changes.append(change)
            if change > 1:
                bullish += 1
            elif change < -1:
                bearish += 1
        
        if not changes:
            return cls()
        
        avg_change = statistics.mean(changes)
        volatility = statistics.stdev(changes) if len(changes) > 1 else 0
        
        # Determine regime
        if volatility > 5:
            regime = MarketRegime.HIGH_VOLATILITY
        elif avg_change > 2 and bullish > bearish:
            regime = MarketRegime.BULL
        elif avg_change < -2 and bearish > bullish:
            regime = MarketRegime.BEAR
        else:
            regime = MarketRegime.SIDEWAYS
        
        return cls(
            avg_change_24h=avg_change,
            volatility=volatility,
            bullish_count=bullish,
            bearish_count=bearish,
            regime=regime
        )


# ============================================================================
# CIRCUIT BREAKER
# ============================================================================

class CircuitBreaker:
    """
    Circuit breaker pattern for API resilience.
    Prevents cascading failures by temporarily blocking requests after repeated failures.
    """
    
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
                    logger.info(f"🔄 Circuit breaker '{self.name}' entering HALF_OPEN state")
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


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


# ============================================================================
# PERSISTENCE MANAGER
# ============================================================================

class PersistenceManager:
    """
    Handles atomic state persistence with backup rotation.
    Ensures data integrity even during crashes.
    """
    
    def __init__(self, state_file: str, backup_count: int = 5):
        self.state_file = state_file
        self.backup_count = backup_count
        self._lock = asyncio.Lock()
    
    async def load_state(self) -> Dict:
        """Load state with fallback to backups"""
        async with self._lock:
            # Try main state file
            state = self._try_load_file(self.state_file)
            if state is not None:
                logger.info(f"✅ Loaded state from {self.state_file}")
                return self._deserialize_state(state)
            
            # Try backups in order
            for i in range(self.backup_count):
                backup_file = f"{self.state_file}.backup.{i}"
                state = self._try_load_file(backup_file)
                if state is not None:
                    logger.warning(f"⚠️ Loaded state from backup: {backup_file}")
                    return self._deserialize_state(state)
            
            logger.info("💾 No state file found. Starting fresh.")
            return {}
    
    def _try_load_file(self, filepath: str) -> Optional[Dict]:
        """Attempt to load and validate a state file"""
        if not os.path.exists(filepath):
            return None
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate checksum if present
            if 'checksum' in data:
                stored_checksum = data.pop('checksum')
                computed_checksum = self._compute_checksum(data)
                if stored_checksum != computed_checksum:
                    logger.warning(f"⚠️ Checksum mismatch in {filepath}")
                    return None
            
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"⚠️ Failed to load {filepath}: {e}")
            return None
    
    async def save_state(self, state: Dict):
        """Atomically save state with backup rotation"""
        async with self._lock:
            serialized = self._serialize_state(state)
            
            # Add checksum
            serialized['checksum'] = self._compute_checksum(serialized)
            serialized['saved_at'] = datetime.now(timezone.utc).isoformat()
            
            # Rotate backups
            self._rotate_backups()
            
            # Atomic write using temp file
            temp_file = None
            try:
                dir_name = os.path.dirname(self.state_file) or '.'
                os.makedirs(dir_name, exist_ok=True)
                
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    dir=dir_name,
                    suffix='.tmp',
                    delete=False,
                    encoding='utf-8'
                ) as f:
                    temp_file = f.name
                    json.dump(serialized, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                
                # Atomic rename
                shutil.move(temp_file, self.state_file)
                logger.debug(f"💾 State saved to {self.state_file}")
                
            except Exception as e:
                logger.error(f"❌ Failed to save state: {e}")
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)
                raise
    
    def _rotate_backups(self):
        """Rotate backup files"""
        # Remove oldest backup
        oldest = f"{self.state_file}.backup.{self.backup_count - 1}"
        if os.path.exists(oldest):
            os.unlink(oldest)
        
        # Shift backups
        for i in range(self.backup_count - 2, -1, -1):
            old_name = f"{self.state_file}.backup.{i}"
            new_name = f"{self.state_file}.backup.{i + 1}"
            if os.path.exists(old_name):
                shutil.move(old_name, new_name)
        
        # Current state becomes backup.0
        if os.path.exists(self.state_file):
            shutil.copy2(self.state_file, f"{self.state_file}.backup.0")
    
    def _compute_checksum(self, data: Dict) -> str:
        """Compute SHA256 checksum of data"""
        json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()[:16]
    
    def _serialize_state(self, state: Dict) -> Dict:
        """Serialize state for JSON storage"""
        serialized = {}
        
        # Tracked positions
        if 'tracked_positions' in state:
            serialized['tracked_positions'] = {
                sym: pos.to_dict() if isinstance(pos, TrackedPosition) else pos
                for sym, pos in state['tracked_positions'].items()
            }
        
        # Metrics
        if 'metrics' in state:
            metrics = state['metrics']
            serialized['metrics'] = metrics.to_dict() if isinstance(metrics, TradingMetrics) else metrics
        
        # Price history
        if 'price_history' in state:
            serialized['price_history'] = state['price_history']
        
        # Simple types
        for key in ['position_history', 'trade_history', 'trades_today', 'daily_start_value']:
            if key in state:
                serialized[key] = state[key]
        
        # Date handling
        if 'last_trade_date' in state:
            val = state['last_trade_date']
            serialized['last_trade_date'] = val.isoformat() if isinstance(val, date) else val
        
        return serialized
    
    def _deserialize_state(self, data: Dict) -> Dict:
        """Deserialize state from JSON storage"""
        state = {}
        
        # Tracked positions
        if 'tracked_positions' in data:
            state['tracked_positions'] = {
                sym: TrackedPosition.from_dict(pos_data)
                for sym, pos_data in data['tracked_positions'].items()
            }
        
        # Metrics
        if 'metrics' in data:
            state['metrics'] = TradingMetrics.from_dict(data['metrics'])
        
        # Price history
        if 'price_history' in data:
            state['price_history'] = data['price_history']
        
        # Simple types
        for key in ['position_history', 'trade_history', 'trades_today', 'daily_start_value']:
            if key in data:
                state[key] = data[key]
        
        # Date handling
        if 'last_trade_date' in data and data['last_trade_date']:
            try:
                state['last_trade_date'] = date.fromisoformat(data['last_trade_date'])
            except (ValueError, TypeError):
                state['last_trade_date'] = None
        
        return state


# ============================================================================
# HTTP CLIENT WITH RESILIENCE
# ============================================================================

class ResilientHTTPClient:
    """
    HTTP client with retry logic, circuit breaker, and connection pooling.
    """
    
    def __init__(
        self,
        base_url: str,
        headers: Dict[str, str],
        retry_count: int = 5,
        timeout: int = 30,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        self.base_url = base_url.rstrip('/')
        self.headers = headers
        self.retry_count = retry_count
        self.timeout = ClientTimeout(total=timeout)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name="http_client")
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[TCPConnector] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with connection pooling"""
        if self._session is None or self._session.closed:
            self._connector = TCPConnector(
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
                enable_cleanup_closed=True
            )
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=self.timeout,
                headers=self.headers
            )
        return self._session
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector:
            await self._connector.close()
    
    async def request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict:
        """Make HTTP request with retry and circuit breaker"""
        return await self.circuit_breaker.call(
            self._request_with_retry, method, endpoint, **kwargs
        )
    
    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict:
        """Internal request with exponential backoff retry"""
        url = f"{self.base_url}{endpoint}"
        last_error = None
        
        for attempt in range(self.retry_count):
            try:
                session = await self._get_session()
                
                async with session.request(method, url, **kwargs) as response:
                    text = await response.text()
                    
                    if response.status == 429:  # Rate limited
                        retry_after = int(response.headers.get('Retry-After', 60))
                        logger.warning(f"⏳ Rate limited. Waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue
                    
                    if response.status >= 500:  # Server error, retry
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=text
                        )
                    
                    if response.status >= 400:  # Client error, don't retry
                        logger.error(f"API Error ({response.status}): {text[:500]}")
                        response.raise_for_status()
                    
                    return json.loads(text) if text else {}
                    
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                last_error = e
                if attempt < self.retry_count - 1:
                    delay = min(
                        config.API_BACKOFF_BASE ** attempt,
                        config.API_BACKOFF_MAX
                    )
                    logger.warning(
                        f"⚠️ Request failed (attempt {attempt + 1}/{self.retry_count}): {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
        
        raise last_error or Exception("Request failed after all retries")


# ============================================================================
# RECALL API CLIENT
# ============================================================================

class RecallAPIClient:
    """
    Recall Network API client with full endpoint coverage.
    """
    
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TradingAgent/3.0"
        }
        
        self.http = ResilientHTTPClient(
            base_url=base_url,
            headers=headers,
            retry_count=config.API_RETRY_COUNT,
            timeout=config.API_TIMEOUT_SECONDS,
            circuit_breaker=CircuitBreaker(
                failure_threshold=config.CIRCUIT_FAILURE_THRESHOLD,
                recovery_timeout=config.CIRCUIT_RECOVERY_TIMEOUT,
                name="recall_api"
            )
        )
        
        env = "SANDBOX" if "sandbox" in base_url else "PRODUCTION"
        logger.info(f"✅ Recall API Client initialized ({env})")
    
    async def close(self):
        """Close client connections"""
        await self.http.close()
    
    async def get_portfolio(self, competition_id: str) -> Dict:
        """Get agent balances"""
        return await self.http.request(
            "GET",
            f"/api/agent/balances?competitionId={competition_id}"
        )
    
    async def get_token_price(self, token_address: str, chain: str = "eth") -> float:
        """Get token price"""
        result = await self.http.request(
            "GET",
            f"/api/price?token={token_address}&chain={chain}"
        )
        return float(result.get("price", 0.0))
    
    async def execute_trade(
        self,
        competition_id: str,
        from_token: str,
        to_token: str,
        amount: str,
        reason: str = "AI trading decision",
        from_chain: Optional[str] = None,
        to_chain: Optional[str] = None
    ) -> Dict:
        """Execute a trade"""
        payload = {
            "competitionId": competition_id,
            "fromToken": from_token,
            "toToken": to_token,
            "amount": amount,
            "reason": reason[:500]
        }
        
        if from_chain:
            payload["fromChain"] = from_chain
        if to_chain:
            payload["toChain"] = to_chain
        
        return await self.http.request("POST", "/api/trade/execute", json=payload)
    
    async def get_trade_history(self, competition_id: str) -> Dict:
        """Get trade history"""
        return await self.http.request(
            "GET",
            f"/api/agent/trades?competitionId={competition_id}"
        )
    
    async def get_leaderboard(self, competition_id: Optional[str] = None) -> Dict:
        """Get leaderboard"""
        endpoint = "/api/leaderboard"
        if competition_id:
            endpoint += f"?competitionId={competition_id}"
        return await self.http.request("GET", endpoint)
    
    async def get_competitions(self) -> Dict:
        """Get all competitions"""
        return await self.http.request("GET", "/api/competitions")
    
    async def get_user_competitions(self) -> Dict:
        """Get user's competitions"""
        return await self.http.request("GET", "/api/user/competitions")


# ============================================================================
# MARKET DATA PROVIDER
# ============================================================================

class MarketDataProvider:
    """
    Market data provider with caching and multiple source support.
    FIXED: Better volume estimation and data handling
    """
    
    COINGECKO_IDS = {
        "WETH": "ethereum",
        "WBTC": "bitcoin",
        "UNI": "uniswap",
        "LINK": "chainlink",
        "DAI": "dai",
        "USDC": "usd-coin",
        "AAVE": "aave",
        "SNX": "synthetix-network-token",
        "BONK": "bonk",
        "FLOKI": "floki",
        "PEPE": "pepe",
        "SHIB": "shiba-inu",
    }
    
    # Historical average volumes for volume surge calculation (in millions USD)
    HISTORICAL_AVG_VOLUMES = {
        "WETH": 15000,
        "WBTC": 8000,
        "UNI": 200,
        "LINK": 400,
        "AAVE": 150,
        "SNX": 50,
        "BONK": 300,
        "FLOKI": 100,
        "PEPE": 500,
        "SHIB": 300,
    }
    
    def __init__(self):
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_ttl = timedelta(seconds=config.MARKET_DATA_CACHE_TTL)
        self._session: Optional[aiohttp.ClientSession] = None
        self._volume_history: Dict[str, List[float]] = {}  # Track volume history
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=15)
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_market_data(self) -> Dict[str, Dict]:
        """
        Get market data with caching.
        Returns dict of symbol -> market data.
        """
        cache_key = "market_data"
        
        # Check cache
        if cache_key in self._cache:
            data, timestamp = self._cache[cache_key]
            if datetime.now(timezone.utc) - timestamp < self._cache_ttl:
                logger.debug("📊 Using cached market data")
                return data
        
        # Fetch fresh data
        try:
            data = await self._fetch_coingecko_data()
            self._cache[cache_key] = (data, datetime.now(timezone.utc))
            return data
        except Exception as e:
            logger.error(f"❌ Failed to fetch market data: {e}")
            # Return stale cache if available
            if cache_key in self._cache:
                logger.warning("⚠️ Using stale cached data")
                return self._cache[cache_key][0]
            return {}
    
    async def _fetch_coingecko_data(self) -> Dict[str, Dict]:
        """Fetch data from CoinGecko API"""
        ids = ",".join(self.COINGECKO_IDS.values())
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
            "include_market_cap": "true"
        }
        
        session = await self._get_session()
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
        
        market_data = {}
        for symbol, cg_id in self.COINGECKO_IDS.items():
            if cg_id in data and "usd" in data[cg_id]:
                token_data = data[cg_id]
                
                price = token_data.get("usd", 0)
                change_24h = token_data.get("usd_24h_change", 0.0) or 0.0
                volume_24h = token_data.get("usd_24h_vol", 0.0) or 0.0
                market_cap = token_data.get("usd_market_cap", 0.0) or 0.0
                
                # Calculate volume surge score
                volume_surge = self._calculate_volume_surge(symbol, volume_24h)
                
                # Update volume history
                self._update_volume_history(symbol, volume_24h)
                
                market_data[symbol] = {
                    "price": price,
                    "change_24h_pct": change_24h,
                    "volume_24h": volume_24h,
                    "market_cap": market_cap,
                    "volume_surge_score": volume_surge,
                    "volatility_score": self._estimate_volatility(change_24h),
                }
        
        logger.info(f"📊 Fetched market data for {len(market_data)} tokens")
        return market_data
    
    def _calculate_volume_surge(self, symbol: str, current_volume: float) -> float:
        """
        Calculate volume surge score (0.0 to 1.0+).
        FIXED: More accurate volume surge calculation
        """
        if current_volume <= 0:
            return 0.0
        
        # Get historical average (in millions, convert to actual)
        hist_avg = self.HISTORICAL_AVG_VOLUMES.get(symbol, 100) * 1_000_000
        
        # Also check our tracked history
        if symbol in self._volume_history and len(self._volume_history[symbol]) >= 3:
            recent_avg = statistics.mean(self._volume_history[symbol][-10:])
            # Use the more conservative of the two
            hist_avg = min(hist_avg, recent_avg) if recent_avg > 0 else hist_avg
        
        # Calculate surge ratio
        if hist_avg > 0:
            surge_ratio = current_volume / hist_avg
        else:
            surge_ratio = 0.5
        
        # Normalize to 0-1 scale (1.0 = normal, >1.0 = surge)
        # A 50% increase in volume = 0.5 score, 100% = 1.0, 200% = 1.5
        normalized_score = max(0, surge_ratio - 0.5)
        
        return min(normalized_score, 2.0)  # Cap at 2.0
    
    def _update_volume_history(self, symbol: str, volume: float):
        """Track volume history for better surge detection"""
        if symbol not in self._volume_history:
            self._volume_history[symbol] = []
        
        self._volume_history[symbol].append(volume)
        
        # Keep only last 24 data points
        if len(self._volume_history[symbol]) > 24:
            self._volume_history[symbol] = self._volume_history[symbol][-24:]
    
    def _estimate_volatility(self, change_24h: float) -> float:
        """Estimate volatility score based on price change"""
        abs_change = abs(change_24h)
        if abs_change > 10:
            return 1.0
        elif abs_change > 5:
            return 0.7
        elif abs_change > 2:
            return 0.4
        else:
            return 0.2


# ============================================================================
# MARKET ANALYZER
# ============================================================================

class MarketAnalyzer:
    """
    Advanced market analysis with signal generation.
    FIXED: Better opportunity detection with adaptive thresholds
    """
    
    def __init__(self, data_provider: MarketDataProvider):
        self.data_provider = data_provider
        self._market_snapshot: Optional[MarketSnapshot] = None
    
    async def get_market_data(self) -> Dict[str, Dict]:
        """Get current market data"""
        data = await self.data_provider.get_market_data()
        self._market_snapshot = MarketSnapshot.from_market_data(data)
        return data
    
    def get_market_snapshot(self) -> MarketSnapshot:
        """Get current market snapshot"""
        return self._market_snapshot or MarketSnapshot()
    
    def get_adaptive_thresholds(self) -> Dict[str, float]:
        """
        Get adaptive thresholds based on market conditions.
        ADDED: Dynamic threshold adjustment
        """
        snapshot = self.get_market_snapshot()
        
        # Base thresholds
        thresholds = {
            "volume_surge": config.VOLUME_SURGE_THRESHOLD,
            "momentum": config.MOMENTUM_THRESHOLD,
            "mean_reversion_lower": config.MEAN_REVERSION_LOWER_BOUND,
            "mean_reversion_upper": config.MEAN_REVERSION_UPPER_BOUND,
        }
        
        if not config.ENABLE_ADAPTIVE_MODE:
            return thresholds
        
        # Adjust based on market regime
        if snapshot.regime == MarketRegime.HIGH_VOLATILITY:
            # More permissive in volatile markets
            thresholds["volume_surge"] *= 0.7
            thresholds["momentum"] *= 1.5
            thresholds["mean_reversion_lower"] *= 1.5
        elif snapshot.regime == MarketRegime.BULL:
            # Focus on momentum in bull markets
            thresholds["momentum"] *= 0.8
            thresholds["mean_reversion_lower"] *= 0.7
        elif snapshot.regime == MarketRegime.BEAR:
            # Focus on mean reversion in bear markets
            thresholds["momentum"] *= 1.5
            thresholds["mean_reversion_lower"] *= 1.3
        elif snapshot.regime == MarketRegime.SIDEWAYS:
            # Lower thresholds in sideways markets to find opportunities
            thresholds["volume_surge"] *= 0.8
            thresholds["momentum"] *= 0.7
        
        return thresholds
    
    def classify_signal(
        self,
        change_24h: float,
        strategy: str
    ) -> Tuple[str, Conviction, str]:
        """
        Classify trading signal based on price momentum.
        Returns (signal_type, conviction, description)
        """
        thresholds = self._get_thresholds(strategy)
        
        for threshold, signal, conviction in thresholds:
            if change_24h > threshold:
                desc = f"{signal.replace('_', ' ').title()} ({change_24h:+.2f}%)"
                return signal, conviction, desc
        
        return "STRONG_BEARISH", Conviction.HIGH, f"Strong Bearish ({change_24h:+.2f}%)"
    
    def _get_thresholds(self, strategy: str) -> List[Tuple[float, str, Conviction]]:
        """Get signal thresholds based on strategy"""
        if strategy == "AGGRESSIVE":
            return [
                (3, "STRONG_BULLISH", Conviction.HIGH),
                (1, "BULLISH", Conviction.MEDIUM),
                (0.3, "WEAK_BULLISH", Conviction.LOW),
                (-0.3, "NEUTRAL", Conviction.LOW),
                (-1, "WEAK_BEARISH", Conviction.LOW),
                (-3, "BEARISH", Conviction.MEDIUM),
            ]
        elif strategy == "CONSERVATIVE":
            return [
                (5, "STRONG_BULLISH", Conviction.HIGH),
                (3, "BULLISH", Conviction.MEDIUM),
                (1, "WEAK_BULLISH", Conviction.LOW),
                (-1, "NEUTRAL", Conviction.LOW),
                (-3, "WEAK_BEARISH", Conviction.LOW),
                (-5, "BEARISH", Conviction.MEDIUM),
            ]
        else:  # BALANCED
            return [
                (4, "STRONG_BULLISH", Conviction.HIGH),
                (2, "BULLISH", Conviction.MEDIUM),
                (0.5, "WEAK_BULLISH", Conviction.LOW),
                (-0.5, "NEUTRAL", Conviction.LOW),
                (-2, "WEAK_BEARISH", Conviction.LOW),
                (-4, "BEARISH", Conviction.MEDIUM),
            ]
    
    def find_opportunities(
        self,
        market_data: Dict[str, Dict],
        existing_positions: Set[str]
    ) -> List[Tuple[str, float, SignalType, Conviction]]:
        """
        Find trading opportunities with scoring.
        FIXED: More permissive filtering, better opportunity detection
        Returns list of (symbol, score, signal_type, conviction)
        """
        opportunities = []
        thresholds = self.get_adaptive_thresholds()
        
        logger.info(f"🔍 Analyzing {len(market_data)} tokens for opportunities...")
        logger.info(f"   Adaptive thresholds: vol_surge={thresholds['volume_surge']:.2f}, "
                   f"momentum={thresholds['momentum']*100:.1f}%, "
                   f"mean_rev={thresholds['mean_reversion_lower']*100:.1f}%")
        
        for symbol, data in market_data.items():
            # Skip stablecoins
            token_config = config.TOKENS.get(symbol)
            if not token_config or token_config.stable:
                continue
            
            change_24h_pct = data.get("change_24h_pct", 0) / 100  # Convert to decimal
            volume_surge = data.get("volume_surge_score", 0)
            volatility = data.get("volatility_score", 0)
            
            # Debug logging for each token
            logger.debug(
                f"   📊 {symbol}: change={change_24h_pct*100:+.2f}%, "
                f"vol_surge={volume_surge:.2f}, volatility={volatility:.2f}"
            )
            
            # FIXED: More lenient volume filter with fallback
            volume_ok = volume_surge >= thresholds["volume_surge"]
            
            # Allow through if price movement is significant even with lower volume
            significant_move = abs(change_24h_pct) > 0.03  # 3% move
            
            if not volume_ok and not significant_move:
                logger.debug(f"   ⏭️ {symbol}: Filtered (vol={volume_surge:.2f}, change={change_24h_pct*100:.1f}%)")
                continue
            
            opportunity = self._evaluate_opportunity(
                symbol,
                change_24h_pct,
                volume_surge,
                volatility,
                existing_positions,
                thresholds
            )
            
            if opportunity:
                opportunities.append(opportunity)
                logger.info(
                    f"   ✅ {symbol}: {opportunity[2].value} opportunity, "
                    f"score={opportunity[1]:.2f}, conviction={opportunity[3].value}"
                )
        
        # Sort by score descending
        opportunities.sort(key=lambda x: x[1], reverse=True)
        
        # Return top opportunities
        max_opportunities = 10 if len(existing_positions) < 3 else 5
        
        if opportunities:
            logger.info(f"🎯 Found {len(opportunities)} total opportunities, returning top {min(len(opportunities), max_opportunities)}")
        else:
            logger.info("📭 No opportunities found matching criteria")
            self._log_market_summary(market_data)
        
        return opportunities[:max_opportunities]
    
    def _evaluate_opportunity(
        self,
        symbol: str,
        change_24h: float,
        volume_surge: float,
        volatility: float,
        existing_positions: Set[str],
        thresholds: Dict[str, float]
    ) -> Optional[Tuple[str, float, SignalType, Conviction]]:
        """
        Evaluate a single opportunity.
        FIXED: Better scoring and signal detection
        """
        
        # Base score multiplier from volume
        volume_multiplier = 1 + (volume_surge * 0.5)
        
        # =====================================================================
        # Strategy 1: Mean Reversion (Buy the dip)
        # =====================================================================
        if config.ENABLE_MEAN_REVERSION:
            lower = thresholds["mean_reversion_lower"]
            upper = thresholds["mean_reversion_upper"]
            
            # Check if in mean reversion range (e.g., -3% to -15%)
            if upper <= change_24h <= lower:
                # Bigger dip = higher score
                dip_magnitude = abs(change_24h)
                score = dip_magnitude * 15.0 * volume_multiplier
                
                # Conviction based on dip size
                if change_24h < -0.10:
                    conviction = Conviction.HIGH
                elif change_24h < -0.05:
                    conviction = Conviction.MEDIUM
                else:
                    conviction = Conviction.LOW
                
                return (symbol, score, SignalType.MEAN_REVERSION, conviction)
        
        # =====================================================================
        # Strategy 2: Momentum (Ride the trend)
        # =====================================================================
        if config.ENABLE_MOMENTUM:
            momentum_threshold = thresholds["momentum"]
            
            if change_24h > momentum_threshold:
                signal, conviction, _ = self.classify_signal(
                    change_24h * 100,
                    config.STRATEGY_MODE
                )
                
                if "BULLISH" in signal:
                    score = change_24h * 12.0 * volume_multiplier
                    
                    # Boost for strong momentum
                    if change_24h > 0.05:
                        score *= 1.3
                    
                    return (symbol, score, SignalType.MOMENTUM, conviction)
        
        # =====================================================================
        # Strategy 3: Breakout (High volatility + volume)
        # =====================================================================
        if config.ENABLE_BREAKOUT:
            if change_24h > config.BREAKOUT_THRESHOLD and volume_surge > 0.5:
                score = change_24h * 10.0 * (1 + volume_surge)
                conviction = Conviction.HIGH if change_24h > 0.08 else Conviction.MEDIUM
                
                return (symbol, score, SignalType.BREAKOUT, conviction)
        
        # =====================================================================
        # Strategy 4: Volume Spike (Unusual activity)
        # =====================================================================
        if config.ENABLE_VOLUME_SPIKE:
            if volume_surge > 1.0 and abs(change_24h) > 0.01:
                # High volume with any movement
                score = volume_surge * 8.0 * (1 + abs(change_24h) * 5)
                
                # Direction determines signal type
                if change_24h > 0:
                    conviction = Conviction.MEDIUM
                    return (symbol, score, SignalType.VOLUME_SPIKE, conviction)
        
        return None
    
    def _log_market_summary(self, market_data: Dict[str, Dict]):
        """Log market summary when no opportunities found"""
        logger.info("📊 Market Summary:")
        
        sorted_tokens = sorted(
            market_data.items(),
            key=lambda x: x[1].get("change_24h_pct", 0),
            reverse=True
        )
        
        for symbol, data in sorted_tokens[:5]:
            change = data.get("change_24h_pct", 0)
            vol = data.get("volume_surge_score", 0)
            logger.info(f"   {symbol}: {change:+.2f}% (vol_surge: {vol:.2f})")


# ============================================================================
# TRADING STRATEGY
# ============================================================================

class TradingStrategy:
    """
    Advanced trading strategy with risk management.
    """
    
    def __init__(self, analyzer: MarketAnalyzer):
        self.analyzer = analyzer
    
    def calculate_position_size(
        self,
        total_value: float,
        conviction: Conviction,
        existing_position_value: float = 0,
        signal_type: SignalType = SignalType.MOMENTUM
    ) -> float:
        """
        Calculate position size based on conviction, signal type, and portfolio.
        IMPROVED: Better position sizing logic
        """
        base_size = config.BASE_POSITION_SIZE
        
        # Conviction multiplier
        conviction_multipliers = {
            Conviction.HIGH: 1.5,
            Conviction.MEDIUM: 1.0,
            Conviction.LOW: 0.6
        }
        
        # Signal type multiplier (some signals warrant larger positions)
        signal_multipliers = {
            SignalType.MEAN_REVERSION: 1.2,  # Higher confidence in reversions
            SignalType.MOMENTUM: 1.0,
            SignalType.BREAKOUT: 1.3,
            SignalType.VOLUME_SPIKE: 0.8,  # More cautious
            SignalType.STOP_LOSS: 1.0,
            SignalType.TAKE_PROFIT: 1.0,
            SignalType.REBALANCE: 1.0,
        }
        
        size = base_size
        size *= conviction_multipliers.get(conviction, 1.0)
        size *= signal_multipliers.get(signal_type, 1.0)
        
        # Cap at max position percentage
        max_position_value = total_value * config.MAX_POSITION_PCT
        max_additional = max_position_value - existing_position_value
        size = min(size, max(0, max_additional))
        
        # Ensure minimum trade size
        if size < config.MIN_TRADE_SIZE:
            return 0
        
        return size
    
    def check_correlation_guard(
        self,
        symbol: str,
        current_positions: List[Position]
    ) -> bool:
        """
        Check if adding this position would create correlation risk.
        Returns True if safe to add, False if blocked.
        """
        held_symbols = {
            pos.symbol for pos in current_positions
            if pos.value >= config.MIN_POSITION_VALUE
        }
        
        for pos_symbol in held_symbols:
            pair = tuple(sorted((symbol, pos_symbol)))
            if pair in [tuple(sorted(p)) for p in config.CORRELATED_PAIRS]:
                logger.info(
                    f"🛡️ Correlation guard: Blocking {symbol} "
                    f"(correlated with held {pos_symbol})"
                )
                return False
        
        return True
    
    def generate_exit_decision(
        self,
        position: Position,
        market_data: Dict[str, Dict]
    ) -> Optional[TradeDecision]:
        """Generate exit decision for a position"""
        if position.value < config.MIN_POSITION_VALUE:
            return None  # Skip dust
        
        # Stop Loss - highest priority
        if position.should_stop_loss:
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,  # Leave small buffer
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"🛑 Stop-loss triggered: {position.pnl_pct:.1f}% loss on ${position.value:.2f}"
            )
        
        # Trailing Stop
        if position.should_trailing_stop:
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"📉 Trailing stop: Price dropped from peak ${position.highest_price:.2f} to ${position.current_price:.2f}"
            )
        
        # Take Profit (partial exit to lock in gains)
        if position.should_take_profit:
            # Sell 50% to lock in gains, let the rest ride
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.5,
                conviction=Conviction.MEDIUM,
                signal_type=SignalType.TAKE_PROFIT,
                reason=f"🎯 Take-profit: {position.pnl_pct:.1f}% gain (partial exit 50%)"
            )
        
        # Check for bearish reversal on profitable positions
        token_data = market_data.get(position.symbol, {})
        change_24h = token_data.get("change_24h_pct", 0)
        
        if position.pnl_pct > 5 and change_24h < -5:
            # Profitable but momentum reversing - take some profit
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.3,
                conviction=Conviction.LOW,
                signal_type=SignalType.REBALANCE,
                reason=f"⚠️ Momentum reversal: {change_24h:.1f}% drop, taking partial profit"
            )
        
        return None
    
    def generate_entry_decision(
        self,
        portfolio: Dict,
        market_data: Dict[str, Dict],
        opportunities: List[Tuple[str, float, SignalType, Conviction]]
    ) -> TradeDecision:
        """Generate entry decision"""
        total_value = portfolio.get("total_value", 0)
        holdings = portfolio.get("holdings", {})
        positions = portfolio.get("positions", [])
        
        # Calculate deployed capital
        deployed = sum(
            h["value"] for sym, h in holdings.items()
            if sym != "USDC" and not config.TOKENS.get(sym, TokenConfig("", "", True)).stable
        )
        deployed_pct = deployed / total_value if total_value > 0 else 0
        
        usdc_holding = holdings.get("USDC", {})
        usdc_value = usdc_holding.get("value", 0)
        
        # Risk check: max deployment
        if deployed_pct >= config.MAX_PORTFOLIO_RISK:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"⚠️ Max risk deployed ({deployed_pct*100:.0f}% >= {config.MAX_PORTFOLIO_RISK*100:.0f}%)"
            )
        
        # Liquidity check
        min_required = config.BASE_POSITION_SIZE * 0.5
        if usdc_value < min_required:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"💰 Insufficient USDC (${usdc_value:.0f} < ${min_required:.0f} required)"
            )
        
        # No opportunities
        if not opportunities:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="📭 No opportunities meeting criteria"
            )
        
        # Find best opportunity
        existing_symbols = {
            pos.symbol for pos in positions
            if pos.value >= config.MIN_POSITION_VALUE
        }
        
        for symbol, score, signal_type, conviction in opportunities:
            # Skip if already holding
            if symbol in existing_symbols:
                logger.debug(f"⏭️ {symbol}: Already holding position")
                continue
            
            # Max positions check
            if len(existing_symbols) >= config.MAX_POSITIONS:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"📊 Max positions ({config.MAX_POSITIONS}) reached"
                )
            
            # Correlation guard
            if not self.check_correlation_guard(symbol, positions):
                continue
            
            # Calculate position size
            existing_value = holdings.get(symbol, {}).get("value", 0)
            position_size = self.calculate_position_size(
                total_value,
                conviction,
                existing_value,
                signal_type
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                logger.debug(f"⏭️ {symbol}: Position size too small")
                continue
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            token_data = market_data.get(symbol, {})
            change = token_data.get("change_24h_pct", 0)
            volume = token_data.get("volume_surge_score", 0)
            
            return TradeDecision(
                action=TradingAction.BUY,
                from_token="USDC",
                to_token=symbol,
                amount_usd=position_size,
                conviction=conviction,
                signal_type=signal_type,
                reason=f"🎯 {signal_type.value}: {symbol} {change:+.2f}% | Score: {score:.1f} | Vol: {volume:.2f}",
                metadata={
                    "score": score,
                    "change_24h": change,
                    "volume_surge": volume
                }
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason="🔍 All opportunities filtered out (correlation/position limits)"
        )
    
    def generate_trade_decision(
        self,
        portfolio: Dict,
        market_data: Dict[str, Dict],
        opportunities: List[Tuple[str, float, SignalType, Conviction]]
    ) -> TradeDecision:
        """Generate comprehensive trade decision"""
        positions = portfolio.get("positions", [])
        
        # Priority 1: Check for exits (risk management first!)
        for position in positions:
            exit_decision = self.generate_exit_decision(position, market_data)
            if exit_decision:
                return exit_decision
        
        # Priority 2: Look for entries
        return self.generate_entry_decision(portfolio, market_data, opportunities)


# ============================================================================
# TRADING AGENT
# ============================================================================

class TradingAgent:
    """
    Production-ready trading agent with full lifecycle management.
    """
    
    def __init__(self):
        # Validate configuration
        errors = config.validate()
        if errors:
            for error in errors:
                logger.error(f"❌ Config error: {error}")
            raise ValueError("Invalid configuration")
        
        # Initialize components
        self.client = RecallAPIClient(config.RECALL_API_KEY, config.base_url)
        self.data_provider = MarketDataProvider()
        self.analyzer = MarketAnalyzer(self.data_provider)
        self.strategy = TradingStrategy(self.analyzer)
        self.persistence = PersistenceManager(
            config.STATE_FILE,
            config.STATE_BACKUP_COUNT
        )
        
        # State
        self.competition_id: Optional[str] = None
        self.tracked_positions: Dict[str, TrackedPosition] = {}
        self.position_history: List[Dict] = []
        self.trade_history: List[Dict] = []
        self.metrics = TradingMetrics()
        self.trades_today: int = 0
        self.last_trade_date: Optional[date] = None
        self.daily_start_value: float = 0
        self.price_history: Dict[str, List[Dict]] = {}
        
        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._running = False
        
        logger.info("🤖 Trading Agent v3.0 initialized")
    
    async def initialize(self):
        """Initialize agent state"""
        # Load persisted state
        state = await self.persistence.load_state()
        
        self.tracked_positions = state.get("tracked_positions", {})
        self.position_history = state.get("position_history", [])
        self.trade_history = state.get("trade_history", [])
        self.metrics = state.get("metrics", TradingMetrics())
        self.trades_today = state.get("trades_today", 0)
        self.last_trade_date = state.get("last_trade_date")
        self.daily_start_value = state.get("daily_start_value", 0)
        self.price_history = state.get("price_history", {})
        
        # Select competition
        self.competition_id = await self._select_competition()
        
        logger.info(f"✅ Agent initialized with {len(self.tracked_positions)} tracked positions")
    
    async def _select_competition(self) -> str:
        """Select competition to participate in"""
        if config.COMPETITION_ID:
            logger.info(f"✅ Using configured competition: {config.COMPETITION_ID}")
            return config.COMPETITION_ID
        
        try:
            user_comps = await self.client.get_user_competitions()
            competitions = user_comps.get("competitions", [])
            
            if not competitions:
                all_comps = await self.client.get_competitions()
                competitions = all_comps.get("competitions", [])
            
            if not competitions:
                raise ValueError("No competitions found")
            
            # Prefer active competitions
            active = [c for c in competitions if c.get("status") == "active"]
            comp = active[0] if active else competitions[0]
            
            comp_id = comp.get("id")
            logger.info(f"✅ Selected competition: {comp.get('name', comp_id)}")
            return comp_id
            
        except Exception as e:
            raise ValueError(f"Failed to select competition: {e}")
    
    async def get_portfolio_state(self) -> Dict:
        """Get comprehensive portfolio state"""
        try:
            portfolio = await self.client.get_portfolio(self.competition_id)
            market_data = await self.analyzer.get_market_data()
            
            balances = portfolio.get("balances", [])
            total_value = 0
            holdings = {}
            positions = []
            
            for balance in balances:
                symbol = balance.get("symbol", "")
                amount = float(balance.get("amount", 0))
                current_price = float(balance.get("price", 0))
                
                # Use market data price if available (more accurate)
                if symbol in market_data:
                    current_price = market_data[symbol].get("price", current_price)
                
                value = amount * current_price
                total_value += value
                
                if symbol in config.TOKENS:
                    holdings[symbol] = {
                        "symbol": symbol,
                        "amount": amount,
                        "value": value,
                        "price": current_price,
                        "pct": 0  # Calculated below
                    }
                    
                    # Build position objects for non-stables
                    token_config = config.TOKENS[symbol]
                    if not token_config.stable and amount > 0 and value >= config.MIN_POSITION_VALUE:
                        tracked = self.tracked_positions.get(symbol)
                        
                        if tracked:
                            entry_price = tracked.entry_price
                            pnl_pct = (
                                (current_price - entry_price) / entry_price * 100
                                if entry_price > 0 else 0
                            )
                            
                            # Update price tracking for trailing stop
                            tracked.update_price_tracking(current_price)
                            
                            positions.append(Position(
                                symbol=symbol,
                                amount=amount,
                                entry_price=entry_price,
                                current_price=current_price,
                                value=value,
                                pnl_pct=pnl_pct,
                                highest_price=tracked.highest_price,
                                lowest_price=tracked.lowest_price_since_entry
                            ))
                        else:
                            # Untracked position (pre-existing) - create tracking
                            logger.info(f"📍 Creating tracker for existing position: {symbol}")
                            self.tracked_positions[symbol] = TrackedPosition(
                                symbol=symbol,
                                entry_price=current_price,
                                entry_amount=amount,
                                entry_value_usd=value,
                                entry_timestamp=datetime.now(timezone.utc).isoformat()
                            )
                            
                            positions.append(Position(
                                symbol=symbol,
                                amount=amount,
                                entry_price=current_price,
                                current_price=current_price,
                                value=value,
                                pnl_pct=0,
                                highest_price=current_price,
                                lowest_price=current_price
                            ))
            
            # Calculate percentages
            for symbol in holdings:
                if total_value > 0:
                    holdings[symbol]["pct"] = holdings[symbol]["value"] / total_value * 100
            
            # Update daily tracking
            today = date.today()
            if self.last_trade_date != today:
                logger.info(f"📅 New trading day: {today}")
                self.daily_start_value = total_value
                self.trades_today = 0
                self.last_trade_date = today
                self.metrics.daily_pnl_usd = 0
            
            # Update peak for drawdown calculation
            if total_value > self.metrics.peak_portfolio_value:
                self.metrics.peak_portfolio_value = total_value
            
            # Calculate current drawdown
            if self.metrics.peak_portfolio_value > 0:
                current_drawdown = (
                    (self.metrics.peak_portfolio_value - total_value) 
                    / self.metrics.peak_portfolio_value
                )
                if current_drawdown > self.metrics.max_drawdown_pct:
                    self.metrics.max_drawdown_pct = current_drawdown
            
            # Store price history for analysis
            self._update_price_history(market_data)
            
            return {
                "total_value": total_value,
                "holdings": holdings,
                "positions": positions,
                "market_data": market_data,
                "market_snapshot": self.analyzer.get_market_snapshot(),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get portfolio: {e}")
            logger.debug(traceback.format_exc())
            return {}
    
    def _update_price_history(self, market_data: Dict[str, Dict]):
        """Update price history for trend analysis"""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        for symbol, data in market_data.items():
            if symbol not in self.price_history:
                self.price_history[symbol] = []
            
            self.price_history[symbol].append({
                "timestamp": timestamp,
                "price": data.get("price", 0),
                "change_24h": data.get("change_24h_pct", 0),
                "volume_surge": data.get("volume_surge_score", 0)
            })
            
            # Keep only last 288 entries (24 hours at 5-minute intervals)
            if len(self.price_history[symbol]) > 288:
                self.price_history[symbol] = self.price_history[symbol][-288:]
    
    async def execute_trade(self, decision: TradeDecision, portfolio: Dict) -> bool:
        """Execute a trade decision"""
        if decision.action == TradingAction.HOLD:
            logger.info(f"⏸️ HOLD: {decision.reason}")
            return False
        
        from_symbol = decision.from_token.upper()
        to_symbol = decision.to_token.upper()
        amount_usd = decision.amount_usd
        
        # Validate tokens
        if from_symbol not in config.TOKENS or to_symbol not in config.TOKENS:
            logger.error(f"❌ Invalid tokens: {from_symbol} → {to_symbol}")
            return False
        
        from_token = config.TOKENS[from_symbol]
        to_token = config.TOKENS[to_symbol]
        
        # Get holdings
        holdings = portfolio.get("holdings", {})
        from_holding = holdings.get(from_symbol, {})
        
        if not from_holding or from_holding.get("amount", 0) <= 0:
            logger.error(f"❌ No {from_symbol} balance")
            return False
        
        available = from_holding["amount"]
        from_price = from_holding["price"]
        total_value = portfolio["total_value"]
        
        # Position sizing safety checks
        max_trade_value = total_value * 0.25  # Max 25% per trade
        max_position_value = total_value * config.MAX_POSITION_PCT
        amount_usd = min(amount_usd, max_trade_value, max_position_value)
        
        # Calculate token amount
        amount_tokens = amount_usd / from_price if from_price > 0 else 0
        
        # Don't exceed available
        if amount_tokens > available:
            amount_tokens = available * 0.98
            amount_usd = amount_tokens * from_price
        
        # Minimum check
        if amount_tokens < config.MIN_TRADE_SIZE:
            logger.warning(f"❌ Trade too small: {amount_tokens:.10f} tokens (min: {config.MIN_TRADE_SIZE})")
            return False
        
        # Format amount with appropriate precision
        decimals = from_token.decimals
        amount_str = f"{amount_tokens:.{min(decimals, 10)}f}"
        
        try:
            # Log trade details
            logger.info("=" * 60)
            logger.info(f"📤 EXECUTING {decision.action.name}")
            logger.info(f"   Pair: {from_symbol} → {to_symbol}")
            logger.info(f"   Amount: {amount_tokens:.6f} {from_symbol} (${amount_usd:.2f})")
            logger.info(f"   Signal: {decision.signal_type.value}")
            logger.info(f"   Conviction: {decision.conviction.value}")
            logger.info(f"   Reason: {decision.reason}")
            logger.info("=" * 60)
            
            result = await self.client.execute_trade(
                competition_id=self.competition_id,
                from_token=from_token.address,
                to_token=to_token.address,
                amount=amount_str,
                reason=decision.reason,
                from_chain=from_token.chain,
                to_chain=to_token.chain
            )
            
            if result.get("success"):
                logger.info("✅ Trade executed successfully!")
                await self._record_trade(decision, portfolio, amount_usd, amount_tokens)
                return True
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"❌ Trade failed: {error}")
                return False
                
        except CircuitBreakerOpenError as e:
            logger.warning(f"⚠️ Circuit breaker open: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Trade execution error: {e}")
            logger.debug(traceback.format_exc())
            return False
    
    async def _record_trade(
        self,
        decision: TradeDecision,
        portfolio: Dict,
        amount_usd: float,
        amount_tokens: float
    ):
        """Record trade in state"""
        timestamp = datetime.now(timezone.utc).isoformat()
        market_data = portfolio.get("market_data", {})
        
        if decision.action == TradingAction.BUY:
            to_symbol = decision.to_token
            to_price = market_data.get(to_symbol, {}).get("price", 0)
            
            if to_price == 0:
                # Estimate from holdings if available
                to_holding = portfolio.get("holdings", {}).get(to_symbol, {})
                to_price = to_holding.get("price", 0)
            
            estimated_receive = amount_usd / to_price if to_price > 0 else 0
            
            # Update or create tracked position
            if to_symbol in self.tracked_positions:
                self.tracked_positions[to_symbol].update_for_add(
                    estimated_receive, to_price, amount_usd
                )
                logger.info(f"📍 Updated position: {to_symbol} (averaged in at ${to_price:.4f})")
            else:
                self.tracked_positions[to_symbol] = TrackedPosition(
                    symbol=to_symbol,
                    entry_price=to_price,
                    entry_amount=estimated_receive,
                    entry_value_usd=amount_usd,
                    entry_timestamp=timestamp
                )
                logger.info(f"📍 New position: {to_symbol} @ ${to_price:.4f}")
        
        elif decision.action == TradingAction.SELL:
            from_symbol = decision.from_token
            
            if from_symbol in self.tracked_positions:
                tracked = self.tracked_positions[from_symbol]
                exit_price = market_data.get(from_symbol, {}).get("price", 0)
                
                if exit_price == 0:
                    exit_price = portfolio["holdings"].get(from_symbol, {}).get("price", 0)
                
                # Calculate PnL
                pnl_pct = (
                    (exit_price - tracked.entry_price) / tracked.entry_price * 100
                    if tracked.entry_price > 0 else 0
                )
                pnl_usd = amount_usd * (pnl_pct / 100)
                
                logger.info(f"📍 Exit: {from_symbol}")
                logger.info(f"   Entry: ${tracked.entry_price:.4f} → Exit: ${exit_price:.4f}")
                logger.info(f"   PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
                
                # Update metrics
                self.metrics.total_pnl_usd += pnl_usd
                self.metrics.daily_pnl_usd += pnl_usd
                
                if pnl_pct > 0:
                    self.metrics.winning_trades += 1
                    self.metrics.consecutive_wins += 1
                    self.metrics.consecutive_losses = 0
                else:
                    self.metrics.losing_trades += 1
                    self.metrics.consecutive_losses += 1
                    self.metrics.consecutive_wins = 0
                
                # Archive to history
                self.position_history.append({
                    "symbol": from_symbol,
                    "entry_price": tracked.entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_usd": pnl_usd,
                    "entry_timestamp": tracked.entry_timestamp,
                    "exit_timestamp": timestamp,
                    "signal_type": decision.signal_type.value,
                    "hold_duration_hours": self._calculate_hold_duration(tracked.entry_timestamp, timestamp)
                })
                
                # Handle partial vs full exit
                sold_amount = amount_usd / exit_price if exit_price > 0 else 0
                updated = tracked.update_for_partial_exit(sold_amount)
                
                if updated is None:
                    del self.tracked_positions[from_symbol]
                    logger.info(f"📍 Position fully closed: {from_symbol}")
                else:
                    logger.info(f"📍 Partial exit: {from_symbol} ({sold_amount:.4f} sold, {tracked.entry_amount:.4f} remaining)")
        
        # Update trade counters
        self.trades_today += 1
        self.metrics.total_trades += 1
        self.metrics.trades_today = self.trades_today
        
        # Record in history
        self.trade_history.append({
            "timestamp": timestamp,
            "action": decision.action.name,
            "from": decision.from_token,
            "to": decision.to_token,
            "amount_usd": amount_usd,
            "amount_tokens": amount_tokens,
            "signal_type": decision.signal_type.value,
            "conviction": decision.conviction.value,
            "reason": decision.reason,
            "metadata": decision.metadata
        })
        
        # Persist state
        await self._save_state()
        
        logger.info(f"📊 Daily trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
    
    def _calculate_hold_duration(self, entry_timestamp: str, exit_timestamp: str) -> float:
        """Calculate hold duration in hours"""
        try:
            entry = datetime.fromisoformat(entry_timestamp.replace('Z', '+00:00'))
            exit = datetime.fromisoformat(exit_timestamp.replace('Z', '+00:00'))
            duration = (exit - entry).total_seconds() / 3600
            return round(duration, 2)
        except Exception:
            return 0.0
    
    async def _save_state(self):
        """Save current state"""
        state = {
            "tracked_positions": self.tracked_positions,
            "position_history": self.position_history[-500:],  # Keep last 500
            "trade_history": self.trade_history[-1000:],  # Keep last 1000
            "metrics": self.metrics,
            "trades_today": self.trades_today,
            "last_trade_date": self.last_trade_date,
            "daily_start_value": self.daily_start_value,
            "price_history": {k: v[-100:] for k, v in self.price_history.items()}  # Keep last 100 per token
        }
        await self.persistence.save_state(state)
    
    def _log_portfolio_summary(self, portfolio: Dict):
        """Log portfolio summary"""
        total = portfolio.get("total_value", 0)
        positions = portfolio.get("positions", [])
        holdings = portfolio.get("holdings", {})
        market_snapshot = portfolio.get("market_snapshot", MarketSnapshot())
        
        logger.info("=" * 70)
        logger.info(f"💰 PORTFOLIO SUMMARY")
        logger.info("=" * 70)
        logger.info(f"   Total Value: ${total:,.2f}")
        logger.info(f"   Active Positions: {len(positions)}/{config.MAX_POSITIONS}")
        logger.info(f"   Daily Trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
        
        # Daily P&L
        if self.daily_start_value > 0:
            daily_pnl_pct = ((total - self.daily_start_value) / self.daily_start_value) * 100
            daily_emoji = "📈" if daily_pnl_pct >= 0 else "📉"
            logger.info(f"   Daily P&L: {daily_emoji} {daily_pnl_pct:+.2f}% (${total - self.daily_start_value:+,.2f})")
        
        # Market regime
        logger.info(f"   Market Regime: {market_snapshot.regime.value}")
        logger.info(f"   Market Volatility: {market_snapshot.volatility:.2f}%")
        
        # Overall metrics
        if self.metrics.total_trades > 0:
            logger.info("-" * 70)
            logger.info(f"📊 PERFORMANCE METRICS")
            logger.info(f"   Win Rate: {self.metrics.win_rate*100:.1f}% ({self.metrics.winning_trades}W / {self.metrics.losing_trades}L)")
            logger.info(f"   Total P&L: ${self.metrics.total_pnl_usd:+,.2f}")
            logger.info(f"   Max Drawdown: {self.metrics.max_drawdown_pct*100:.2f}%")
            logger.info(f"   Consecutive Wins: {self.metrics.consecutive_wins} | Losses: {self.metrics.consecutive_losses}")
        
        # Position details
        if positions:
            logger.info("-" * 70)
            logger.info(f"📋 POSITIONS")
            
            for pos in sorted(positions, key=lambda p: p.value, reverse=True):
                if pos.value >= config.MIN_POSITION_VALUE:
                    if pos.pnl_pct > 5:
                        emoji = "🟢"
                    elif pos.pnl_pct > 0:
                        emoji = "🔵"
                    elif pos.pnl_pct > -5:
                        emoji = "🟡"
                    else:
                        emoji = "🔴"
                    
                    pct_of_portfolio = (pos.value / total * 100) if total > 0 else 0
                    logger.info(
                        f"   {emoji} {pos.symbol:6} | ${pos.value:>10,.2f} ({pct_of_portfolio:>5.1f}%) | "
                        f"P&L: {pos.pnl_pct:>+7.2f}% | Entry: ${pos.entry_price:.4f}"
                    )
        
        # USDC balance
        usdc = holdings.get("USDC", {})
        if usdc:
            usdc_pct = (usdc.get("value", 0) / total * 100) if total > 0 else 0
            logger.info(f"   💵 USDC   | ${usdc.get('value', 0):>10,.2f} ({usdc_pct:>5.1f}%) | Available for trading")
        
        logger.info("=" * 70)
    
    def _log_market_overview(self, market_data: Dict[str, Dict]):
        """Log market overview"""
        logger.info("-" * 70)
        logger.info("📈 MARKET OVERVIEW (Top Movers)")
        
        # Sort by absolute change
        sorted_tokens = sorted(
            [(s, d) for s, d in market_data.items() if s not in ["USDC", "DAI"]],
            key=lambda x: abs(x[1].get("change_24h_pct", 0)),
            reverse=True
        )
        
        for symbol, data in sorted_tokens[:6]:
            change = data.get("change_24h_pct", 0)
            price = data.get("price", 0)
            vol_surge = data.get("volume_surge_score", 0)
            
            if change > 2:
                emoji = "🚀"
            elif change > 0:
                emoji = "📗"
            elif change > -2:
                emoji = "📕"
            else:
                emoji = "💥"
            
            logger.info(
                f"   {emoji} {symbol:6} | ${price:>12,.4f} | {change:>+7.2f}% | Vol: {vol_surge:.2f}"
            )
    
    async def run_cycle(self):
        """Run a single trading cycle"""
        cycle_start = datetime.now(timezone.utc)
        
        logger.info(f"\n{'~' * 70}")
        logger.info(f"🔄 TRADING CYCLE: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        logger.info(f"{'~' * 70}")
        
        # Get portfolio state
        portfolio = await self.get_portfolio_state()
        
        if not portfolio or portfolio.get("total_value", 0) == 0:
            logger.error("🚫 Cannot retrieve portfolio. Skipping cycle.")
            return
        
        # Log summaries
        self._log_portfolio_summary(portfolio)
        self._log_market_overview(portfolio.get("market_data", {}))
        
        # Check daily loss limit
        if self.daily_start_value > 0:
            daily_pnl_pct = (
                (portfolio["total_value"] - self.daily_start_value)
                / self.daily_start_value
            )
            if daily_pnl_pct <= config.MAX_DAILY_LOSS_PCT:
                logger.warning(
                    f"⛔ Daily loss limit reached ({daily_pnl_pct*100:.1f}% <= {config.MAX_DAILY_LOSS_PCT*100:.1f}%). "
                    "Trading paused for today."
                )
                return
        
        # Check consecutive losses - reduce risk
        if self.metrics.consecutive_losses >= 3:
            logger.warning(
                f"⚠️ {self.metrics.consecutive_losses} consecutive losses. "
                "Reducing position sizes by 50%."
            )
        
        # Find opportunities
        existing_positions = {
            pos.symbol for pos in portfolio["positions"]
            if pos.value >= config.MIN_POSITION_VALUE
        }
        
        opportunities = self.analyzer.find_opportunities(
            portfolio["market_data"],
            existing_positions
        )
        
        if opportunities:
            logger.info(f"\n🎯 TOP OPPORTUNITIES:")
            for i, (sym, score, sig_type, conv) in enumerate(opportunities[:5], 1):
                token_data = portfolio["market_data"].get(sym, {})
                change = token_data.get("change_24h_pct", 0)
                logger.info(
                    f"   {i}. {sym:6} | Score: {score:>6.2f} | {sig_type.value:15} | "
                    f"{conv.value:6} | {change:>+6.2f}%"
                )
        
        # Generate and execute decision
        decision = self.strategy.generate_trade_decision(
            portfolio,
            portfolio["market_data"],
            opportunities
        )
        
        trade_executed = await self.execute_trade(decision, portfolio)
        
        # Log cycle duration
        cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(f"\n⏱️ Cycle completed in {cycle_duration:.2f}s")
        
        # If we haven't hit minimum trades and have opportunities, try to trade more
        if (
            trade_executed 
            and self.trades_today < config.MIN_TRADES_PER_DAY 
            and len(opportunities) > 1
        ):
            logger.info("🔄 Attempting additional trade to meet daily minimum...")
            
            # Refresh portfolio after trade
            await asyncio.sleep(2)  # Brief pause for API consistency
            portfolio = await self.get_portfolio_state()
            
            if portfolio:
                existing_positions = {
                    pos.symbol for pos in portfolio.get("positions", [])
                    if pos.value >= config.MIN_POSITION_VALUE
                }
                
                # Get remaining opportunities
                remaining_opportunities = [
                    opp for opp in opportunities
                    if opp[0] not in existing_positions
                ]
                
                if remaining_opportunities:
                    decision = self.strategy.generate_trade_decision(
                        portfolio,
                        portfolio["market_data"],
                        remaining_opportunities[1:]  # Skip the one we just traded
                    )
                    await self.execute_trade(decision, portfolio)
    
    async def run(self):
        """Main trading loop"""
        logger.info("=" * 80)
        logger.info("🚀 ADVANCED PAPER TRADING AGENT v3.0 - PRODUCTION READY 🚀")
        logger.info("=" * 80)
        logger.info(f"   Environment: {'SANDBOX' if config.USE_SANDBOX else 'PRODUCTION'}")
        logger.info(f"   Strategy Mode: {config.STRATEGY_MODE}")
        logger.info(f"   Adaptive Mode: {'ENABLED' if config.ENABLE_ADAPTIVE_MODE else 'DISABLED'}")
        logger.info(f"   Trading Interval: {config.TRADING_INTERVAL}s")
        logger.info(f"   Max Positions: {config.MAX_POSITIONS}")
        logger.info(f"   Base Position Size: ${config.BASE_POSITION_SIZE}")
        logger.info("=" * 80)
        
        try:
            await self.initialize()
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            logger.debug(traceback.format_exc())
            return
        
        self._running = True
        
        while self._running and not self._shutdown_event.is_set():
            try:
                await self.run_cycle()
            except CircuitBreakerOpenError as e:
                logger.warning(f"⚠️ Circuit breaker open: {e}")
            except Exception as e:
                logger.error(f"❌ Cycle error: {e}")
                logger.debug(traceback.format_exc())
            
            # Wait for next cycle or shutdown
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=config.TRADING_INTERVAL
                )
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue loop
        
        logger.info("🛑 Trading loop stopped")
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("🛑 Initiating graceful shutdown...")
        self._running = False
        self._shutdown_event.set()
        
        # Save final state
        try:
            await self._save_state()
            logger.info("💾 Final state saved")
        except Exception as e:
            logger.error(f"❌ Failed to save final state: {e}")
        
        # Close connections
        try:
            await self.client.close()
            await self.data_provider.close()
            logger.info("🔌 Connections closed")
        except Exception as e:
            logger.error(f"❌ Error closing connections: {e}")
        
        # Log final statistics
        logger.info("=" * 80)
        logger.info("📊 FINAL SESSION STATISTICS")
        logger.info("=" * 80)
        logger.info(f"   Total Trades: {self.metrics.total_trades}")
        logger.info(f"   Win Rate: {self.metrics.win_rate*100:.1f}%")
        logger.info(f"   Total P&L: ${self.metrics.total_pnl_usd:+,.2f}")
        logger.info(f"   Max Drawdown: {self.metrics.max_drawdown_pct*100:.2f}%")
        logger.info("=" * 80)
        logger.info("✅ Shutdown complete")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

async def main():
    """Main entry point with signal handling"""
    agent = TradingAgent()
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("📡 Received shutdown signal")
        asyncio.create_task(agent.shutdown())
    
    # Register signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt received")
    finally:
        if agent._running:
            await agent.shutdown()


if __name__ == "__main__":
    # Print banner
    print("""
    ╔══════════════════════════════════════════════════════════════════╗
    ║       🤖 ADVANCED PAPER TRADING AGENT v3.0 🤖                    ║
    ║                                                                  ║
    ║   Production-Ready • Fault-Tolerant • Adaptive Strategies       ║
    ╚══════════════════════════════════════════════════════════════════╝
    """)
    
    # Validate config before starting
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"❌ Configuration error: {error}")
        sys.exit(1)
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"💀 Fatal error: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
        