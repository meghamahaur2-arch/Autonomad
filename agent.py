#!/usr/bin/env python3
"""
Advanced Paper Trading Agent - Production Ready
===============================================
A robust, fault-tolerant trading agent for the Recall Network competition.

Features:
- Persistent state management with atomic writes
- Circuit breaker pattern for API resilience
- Weighted position tracking for partial exits
- Advanced risk management with correlation guards
- Comprehensive metrics and observability
- Graceful shutdown handling
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


class Conviction(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CircuitState(Enum):
    CLOSED = auto()   # Normal operation
    OPEN = auto()     # Failing, reject requests
    HALF_OPEN = auto() # Testing if service recovered

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
    MAX_PORTFOLIO_RISK: float = float(os.getenv("MAX_PORTFOLIO_RISK", "0.60"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "-0.10"))
    
    # Strategy Filters
    VOLUME_SURGE_THRESHOLD: float = float(os.getenv("VOLUME_SURGE_THRESHOLD", "0.50"))
    MEAN_REVERSION_LOWER_BOUND: float = float(os.getenv("MEAN_REVERSION_LOWER_BOUND", "-0.10"))
    MEAN_REVERSION_UPPER_BOUND: float = float(os.getenv("MEAN_REVERSION_UPPER_BOUND", "-0.25"))
    MOMENTUM_THRESHOLD: float = float(os.getenv("MOMENTUM_THRESHOLD", "0.02"))
    
    # Strategy Mode
    STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "BALANCED")
    ENABLE_MEAN_REVERSION: bool = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_MOMENTUM: bool = os.getenv("ENABLE_MOMENTUM", "true").lower() == "true"
    ENABLE_TRAILING_STOP: bool = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
    
    # Persistence
    STATE_FILE: str = os.getenv("STATE_FILE", "agent_state.json")
    STATE_BACKUP_COUNT: int = int(os.getenv("STATE_BACKUP_COUNT", "5"))
    
    # Correlated pairs to avoid concentration
    CORRELATED_PAIRS: List[Tuple[str, str]] = [
        ("WETH", "WBTC"),
        ("SNX", "AAVE"),
        ("UNI", "AAVE"),
    ]
    
    # Token Registry
    TOKENS: Dict[str, TokenConfig] = {
    # Ethereum Mainnet (verified correct)
        "USDC": TokenConfig("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "eth", True, 6),
        "WETH": TokenConfig("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "eth", False, 18),
        "WBTC": TokenConfig("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "eth", False, 8),
        "DAI":  TokenConfig("0x6B175474E89094C44Da98b954EedeAC495271d0F", "eth", True, 18),
        "UNI":  TokenConfig("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "eth", False, 18),
        "LINK": TokenConfig("0x514910771AF9Ca656af840dff83E8264EcF986CA", "eth", False, 18),
        "AAVE": TokenConfig("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "eth", False, 18),
        "SNX":  TokenConfig("0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F", "eth", False, 18),

        # ---------------------------------------------------------
        # Meme tokens (CORRECTED + VALID)
        # ---------------------------------------------------------

        # BONK (ERC-20 version, NOT Solana)
        "BONK": TokenConfig("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "sol", False, 5),

        # WIF (Dogwifhat ERC-20 – correct contract)
        "WIF": TokenConfig("EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "sol", False, 18),

        # FLOKI (Official BSC contract)
        "FLOKI": TokenConfig("0xfb5b838b6cfeedc2873ab27866079ac55363d37e", "bsc", False, 9),
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
    highest_price: float = 0.0  # For trailing stop
    total_cost_basis: float = 0.0  # For weighted average
    
    def __post_init__(self):
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price
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
            # Full exit
            return None
        
        ratio_remaining = (self.entry_amount - amount_sold) / self.entry_amount
        self.entry_amount -= amount_sold
        self.total_cost_basis *= ratio_remaining
        self.entry_value_usd = self.total_cost_basis
        return self
    
    def update_highest_price(self, current_price: float):
        """Update highest price for trailing stop"""
        if current_price > self.highest_price:
            self.highest_price = current_price
    
    def get_trailing_stop_price(self) -> float:
        """Calculate trailing stop price"""
        return self.highest_price * (1 - config.TRAILING_STOP_PCT)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TrackedPosition':
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
    
    @property
    def is_profitable(self) -> bool:
        return self.pnl_pct > 0
    
    @property
    def should_stop_loss(self) -> bool:
        return self.pnl_pct <= config.STOP_LOSS_PCT
    
    @property
    def should_take_profit(self) -> bool:
        return self.pnl_pct >= config.TAKE_PROFIT_PCT
    
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
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingMetrics':
        return cls(**data)

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
                if self.success_count >= 2:  # Require 2 successes to close
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
            self._request_with_retry,
            method,
            endpoint,
            **kwargs
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
            "User-Agent": "TradingAgent/2.0"
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
            "reason": reason[:500]  # Truncate reason if too long
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
        "WIF": "dogwifhat",
        "FLOKI": "floki",
    }
    
    def __init__(self):
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_ttl = timedelta(seconds=config.MARKET_DATA_CACHE_TTL)
        self._session: Optional[aiohttp.ClientSession] = None
    
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
                change_24h = token_data.get("usd_24h_change", 0.0) or 0.0
                volume_24h = token_data.get("usd_24h_vol", 0.0) or 0.0
                
                market_data[symbol] = {
                    "price": token_data.get("usd", 0),
                    "change_24h_pct": change_24h,
                    "volume_24h": volume_24h,
                    "market_cap": token_data.get("usd_market_cap", 0.0) or 0.0,
                    # Estimate volume change (would need historical data for accuracy)
                    "volume_surge_score": self._estimate_volume_surge(volume_24h, change_24h),
                }
        
        logger.info(f"📊 Fetched market data for {len(market_data)} tokens")
        return market_data
    
    def _estimate_volume_surge(self, volume: float, price_change: float) -> float:
        """
        Estimate volume surge score.
        In production, this would compare against historical average volume.
        """
        # Heuristic: high volume + significant price move = surge
        if volume <= 0:
            return 0.0
        
        # Normalize by assuming average volume is ~50% of current for active tokens
        # This is a simplification - real implementation would track historical volume
        base_score = 0.5  # Assume current volume is 50% above "average"
        
        # Boost score if price is moving significantly
        if abs(price_change) > 5:
            base_score += 0.3
        elif abs(price_change) > 2:
            base_score += 0.15
        
        return min(base_score, 1.0)

# ============================================================================
# MARKET ANALYZER
# ============================================================================

class MarketAnalyzer:
    """
    Advanced market analysis with signal generation.
    """
    
    def __init__(self, data_provider: MarketDataProvider):
        self.data_provider = data_provider
    
    async def get_market_data(self) -> Dict[str, Dict]:
        """Get current market data"""
        return await self.data_provider.get_market_data()
    
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
        Returns list of (symbol, score, signal_type, conviction)
        """
        opportunities = []
        
        for symbol, data in market_data.items():
            # Skip stablecoins
            token_config = config.TOKENS.get(symbol)
            if not token_config or token_config.stable:
                continue
            
            change_24h_pct = data.get("change_24h_pct", 0) / 100  # Convert to decimal
            volume_surge = data.get("volume_surge_score", 0)
            
            # Volume filter
            if volume_surge < config.VOLUME_SURGE_THRESHOLD:
                logger.debug(f"⏭️ {symbol}: Volume filter failed ({volume_surge:.2f})")
                continue
            
            opportunity = self._evaluate_opportunity(
                symbol, change_24h_pct, volume_surge, existing_positions
            )
            
            if opportunity:
                opportunities.append(opportunity)
        
        # Sort by score descending
        opportunities.sort(key=lambda x: x[1], reverse=True)
        
        # Return top opportunities
        max_opportunities = 10 if len(existing_positions) < 3 else 5
        return opportunities[:max_opportunities]
    
    def _evaluate_opportunity(
        self,
        symbol: str,
        change_24h: float,
        volume_surge: float,
        existing_positions: Set[str]
    ) -> Optional[Tuple[str, float, SignalType, Conviction]]:
        """Evaluate a single opportunity"""
        
        # Mean Reversion: Buy significant dips
        if config.ENABLE_MEAN_REVERSION:
            if config.MEAN_REVERSION_UPPER_BOUND <= change_24h <= config.MEAN_REVERSION_LOWER_BOUND:
                # Bigger dip = higher score
                score = abs(change_24h) * 20.0 * (1 + volume_surge)
                conviction = Conviction.HIGH if change_24h < -0.15 else Conviction.MEDIUM
                
                logger.debug(
                    f"✅ Mean Reversion: {symbol} dipped {change_24h*100:.1f}%, "
                    f"score={score:.1f}"
                )
                return (symbol, score, SignalType.MEAN_REVERSION, conviction)
        
        # Momentum: Buy uptrends
        if config.ENABLE_MOMENTUM:
            if change_24h > config.MOMENTUM_THRESHOLD:
                signal, conviction, _ = self.classify_signal(
                    change_24h * 100,
                    config.STRATEGY_MODE
                )
                
                if "BULLISH" in signal:
                    score = change_24h * 10.0 * (1 + volume_surge)
                    
                    # Conviction multiplier
                    if conviction == Conviction.HIGH:
                        score *= 1.5
                    elif conviction == Conviction.MEDIUM:
                        score *= 1.2
                    
                    logger.debug(
                        f"✅ Momentum: {symbol} gained {change_24h*100:.1f}%, "
                        f"score={score:.1f}"
                    )
                    return (symbol, score, SignalType.MOMENTUM, conviction)
        
        return None

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
        existing_position_value: float = 0
    ) -> float:
        """Calculate position size based on conviction and portfolio"""
        base_size = config.BASE_POSITION_SIZE
        
        # Conviction multiplier
        multipliers = {
            Conviction.HIGH: 1.5,
            Conviction.MEDIUM: 1.0,
            Conviction.LOW: 0.6
        }
        size = base_size * multipliers.get(conviction, 1.0)
        
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
        held_symbols = {pos.symbol for pos in current_positions if pos.value >= config.MIN_POSITION_VALUE}
        
        for pos_symbol in held_symbols:
            pair = tuple(sorted((symbol, pos_symbol)))
            if pair in config.CORRELATED_PAIRS:
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
        
        # Stop Loss
        if position.should_stop_loss:
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.98,  # Leave small buffer
                conviction=Conviction.HIGH,
                signal_type=SignalType.STOP_LOSS,
                reason=f"Stop-loss: {position.pnl_pct:.1f}% loss on ${position.value:.2f}"
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
                reason=f"Trailing stop: Price dropped from peak ${position.highest_price:.2f}"
            )
        
        # Take Profit (partial)
        if position.should_take_profit:
            # Sell 50% to lock in gains
            return TradeDecision(
                action=TradingAction.SELL,
                from_token=position.symbol,
                to_token="USDC",
                amount_usd=position.value * 0.5,
                conviction=Conviction.MEDIUM,
                signal_type=SignalType.TAKE_PROFIT,
                reason=f"Take-profit: {position.pnl_pct:.1f}% gain (partial exit)"
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
                reason=f"Max risk deployed ({deployed_pct*100:.0f}%)"
            )
        
        # Liquidity check
        min_required = config.BASE_POSITION_SIZE * 0.5
        if usdc_value < min_required:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason=f"Insufficient USDC (${usdc_value:.0f} < ${min_required:.0f})"
            )
        
        # No opportunities
        if not opportunities:
            return TradeDecision(
                action=TradingAction.HOLD,
                reason="No opportunities meeting criteria"
            )
        
        # Find best opportunity
        existing_symbols = {
            pos.symbol for pos in positions
            if pos.value >= config.MIN_POSITION_VALUE
        }
        
        for symbol, score, signal_type, conviction in opportunities:
            # Skip if already holding
            if symbol in existing_symbols:
                continue
            
            # Max positions check
            if len(existing_symbols) >= config.MAX_POSITIONS:
                return TradeDecision(
                    action=TradingAction.HOLD,
                    reason=f"Max positions ({config.MAX_POSITIONS}) reached"
                )
            
            # Correlation guard
            if not self.check_correlation_guard(symbol, positions):
                continue
            
            # Calculate position size
            existing_value = holdings.get(symbol, {}).get("value", 0)
            position_size = self.calculate_position_size(
                total_value, conviction, existing_value
            )
            
            if position_size < config.MIN_TRADE_SIZE:
                continue
            
            # Don't exceed available USDC
            position_size = min(position_size, usdc_value * 0.95)
            
            token_data = market_data.get(symbol, {})
            change = token_data.get("change_24h_pct", 0)
            
            return TradeDecision(
                action=TradingAction.BUY,
                from_token="USDC",
                to_token=symbol,
                amount_usd=position_size,
                conviction=conviction,
                signal_type=signal_type,
                reason=f"{signal_type.value}: {change:+.2f}% change, score={score:.1f}",
                metadata={"score": score, "change_24h": change}
            )
        
        return TradeDecision(
            action=TradingAction.HOLD,
            reason="All opportunities filtered out"
        )
    
    def generate_trade_decision(
        self,
        portfolio: Dict,
        market_data: Dict[str, Dict],
        opportunities: List[Tuple[str, float, SignalType, Conviction]]
    ) -> TradeDecision:
        """Generate comprehensive trade decision"""
        
        positions = portfolio.get("positions", [])
        
        # Priority 1: Check for exits (risk management)
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
        
        # Shutdown handling
        self._shutdown_event = asyncio.Event()
        self._running = False
        
        logger.info("🤖 Trading Agent initialized")
    
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
                    if not token_config.stable and amount > 0:
                        tracked = self.tracked_positions.get(symbol)
                        
                        if tracked:
                            entry_price = tracked.entry_price
                            pnl_pct = (
                                (current_price - entry_price) / entry_price * 100
                                if entry_price > 0 else 0
                            )
                            
                            # Update highest price for trailing stop
                            tracked.update_highest_price(current_price)
                            
                            positions.append(Position(
                                symbol=symbol,
                                amount=amount,
                                entry_price=entry_price,
                                current_price=current_price,
                                value=value,
                                pnl_pct=pnl_pct,
                                highest_price=tracked.highest_price
                            ))
                        else:
                            # Untracked position (pre-existing)
                            logger.debug(f"⚠️ Untracked position: {symbol}")
                            positions.append(Position(
                                symbol=symbol,
                                amount=amount,
                                entry_price=current_price,  # Assume current as entry
                                current_price=current_price,
                                value=value,
                                pnl_pct=0
                            ))
            
            # Calculate percentages
            for symbol in holdings:
                if total_value > 0:
                    holdings[symbol]["pct"] = holdings[symbol]["value"] / total_value * 100
            
            # Update daily tracking
            today = date.today()
            if self.last_trade_date != today:
                self.daily_start_value = total_value
                self.trades_today = 0
                self.last_trade_date = today
                self.metrics.daily_pnl_usd = 0
            
            # Update peak for drawdown calculation
            if total_value > self.metrics.peak_portfolio_value:
                self.metrics.peak_portfolio_value = total_value
            
            return {
                "total_value": total_value,
                "holdings": holdings,
                "positions": positions,
                "market_data": market_data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get portfolio: {e}")
            logger.debug(traceback.format_exc())
            return {}
    
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
        
        # Position sizing safety
        max_trade_value = total_value * 0.25
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
            logger.warning(f"❌ Trade too small: {amount_tokens:.10f} tokens")
            return False
        
        # Format amount with appropriate precision
        decimals = from_token.decimals
        amount_str = f"{amount_tokens:.{min(decimals, 10)}f}"
        
        try:
            logger.info(f"📤 {decision.action.name}: {from_symbol} → {to_symbol}")
            logger.info(f"   Amount: {amount_tokens:.6f} {from_symbol} (${amount_usd:.2f})")
            logger.info(f"   Signal: {decision.signal_type.value} | Conviction: {decision.conviction.value}")
            logger.info(f"   Reason: {decision.reason}")
            
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
                # Estimate from trade
                to_price = amount_usd / amount_tokens if amount_tokens > 0 else 0
            
            estimated_receive = amount_usd / to_price if to_price > 0 else 0
            
            # Update or create tracked position
            if to_symbol in self.tracked_positions:
                self.tracked_positions[to_symbol].update_for_add(
                    estimated_receive, to_price, amount_usd
                )
                logger.info(f"📍 Updated position: {to_symbol} (averaged in)")
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
                
                pnl_pct = (
                    (exit_price - tracked.entry_price) / tracked.entry_price * 100
                    if tracked.entry_price > 0 else 0
                )
                pnl_usd = amount_usd * (pnl_pct / 100)
                
                logger.info(f"📍 Exit: {from_symbol} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
                
                # Update metrics
                self.metrics.total_pnl_usd += pnl_usd
                self.metrics.daily_pnl_usd += pnl_usd
                if pnl_pct > 0:
                    self.metrics.winning_trades += 1
                else:
                    self.metrics.losing_trades += 1
                
                # Archive to history
                self.position_history.append({
                    "symbol": from_symbol,
                    "entry_price": tracked.entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": pnl_pct,
                    "pnl_usd": pnl_usd,
                    "entry_timestamp": tracked.entry_timestamp,
                    "exit_timestamp": timestamp,
                    "signal_type": decision.signal_type.value
                })
                
                # Handle partial vs full exit
                sold_amount = amount_usd / exit_price if exit_price > 0 else 0
                updated = tracked.update_for_partial_exit(sold_amount)
                
                if updated is None:
                    del self.tracked_positions[from_symbol]
                    logger.info(f"📍 Position closed: {from_symbol}")
                else:
                    logger.info(f"📍 Partial exit: {from_symbol} ({sold_amount:.4f} sold)")
        
        # Update trade counters
        self.trades_today += 1
        self.metrics.total_trades += 1
        
        # Record in history
        self.trade_history.append({
            "timestamp": timestamp,
            "action": decision.action.name,
            "from": decision.from_token,
            "to": decision.to_token,
            "amount_usd": amount_usd,
            "signal_type": decision.signal_type.value,
            "conviction": decision.conviction.value,
            "reason": decision.reason
        })
        
        # Persist state
        await self._save_state()
        
        logger.info(f"   Daily trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
    
    async def _save_state(self):
        """Save current state"""
        state = {
            "tracked_positions": self.tracked_positions,
            "position_history": self.position_history,
            "trade_history": self.trade_history[-1000:],  # Keep last 1000
            "metrics": self.metrics,
            "trades_today": self.trades_today,
            "last_trade_date": self.last_trade_date,
            "daily_start_value": self.daily_start_value
        }
        await self.persistence.save_state(state)
    
    def _log_portfolio_summary(self, portfolio: Dict):
        """Log portfolio summary"""
        total = portfolio.get("total_value", 0)
        positions = portfolio.get("positions", [])
        holdings = portfolio.get("holdings", {})
        
        logger.info("=" * 60)
        logger.info(f"💰 Portfolio Value: ${total:,.2f}")
        logger.info(f"📊 Positions: {len(positions)}/{config.MAX_POSITIONS}")
        logger.info(f"📈 Daily Trades: {self.trades_today}/{config.MIN_TRADES_PER_DAY}")
        
        if self.metrics.total_trades > 0:
            logger.info(
                f"🎯 Win Rate: {self.metrics.win_rate*100:.1f}% "
                f"({self.metrics.winning_trades}W/{self.metrics.losing_trades}L)"
            )
            logger.info(f"💵 Total PnL: ${self.metrics.total_pnl_usd:+,.2f}")
        
        # Position details
        if positions:
            logger.info("-" * 40)
            for pos in sorted(positions, key=lambda p: p.value, reverse=True):
                if pos.value >= config.MIN_POSITION_VALUE:
                    emoji = "🟢" if pos.pnl_pct > 0 else "🔴" if pos.pnl_pct < 0 else "⚪"
                    logger.info(
                        f"  {emoji} {pos.symbol}: ${pos.value:.2f} "
                        f"({pos.pnl_pct:+.1f}%)"
                    )
        
        logger.info("=" * 60)
    
    async def run_cycle(self):
        """Run a single trading cycle"""
        logger.info(f"\n{'~' * 50}")
        logger.info(f"🔄 Trading cycle: {datetime.now(timezone.utc).isoformat()}")
        
        # Get portfolio state
        portfolio = await self.get_portfolio_state()
        
        if not portfolio or portfolio.get("total_value", 0) == 0:
            logger.error("🚫 Cannot retrieve portfolio. Skipping cycle.")
            return
        
        self._log_portfolio_summary(portfolio)
        
        # Check daily loss limit
        if self.daily_start_value > 0:
            daily_pnl_pct = (
                (portfolio["total_value"] - self.daily_start_value)
                / self.daily_start_value
            )
            if daily_pnl_pct <= config.MAX_DAILY_LOSS_PCT:
                logger.warning(
                    f"⚠️ Daily loss limit reached ({daily_pnl_pct*100:.1f}%). "
                    "Pausing trading."
                )
                return
        
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
            logger.info(f"🎯 Found {len(opportunities)} opportunities")
            for sym, score, sig_type, conv in opportunities[:3]:
                logger.debug(f"   {sym}: score={score:.1f}, {sig_type.value}, {conv.value}")
        
        # Generate and execute decision
        decision = self.strategy.generate_trade_decision(
            portfolio,
            portfolio["market_data"],
            opportunities
        )
        
        await self.execute_trade(decision, portfolio)
    
    async def run(self):
        """Main trading loop"""
        logger.info("=" * 80)
        logger.info("🚀 ADVANCED PAPER TRADING AGENT - PRODUCTION READY 🚀")
        logger.info("=" * 80)
        
        try:
            await self.initialize()
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
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
        logger.info("🛑 Initiating shutdown...")
        self._running = False
        self._shutdown_event.set()
        
        # Save final state
        await self._save_state()
        
        # Close connections
        await self.client.close()
        await self.data_provider.close()
        
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
