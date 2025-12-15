"""
Data Models and Enums
All data structures used across the trading agent
"""
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from datetime import datetime
from typing import Dict, Optional
from collections import deque


# ============================================================================
# ENUMS
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
    LIQUIDITY_SURGE = "LIQUIDITY_SURGE"
    SOCIAL_SENTIMENT = "SOCIAL_SENTIMENT"


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
# POSITION TRACKING
# ============================================================================

@dataclass
class TrackedPosition:
    """
    Tracks a position with weighted average entry for partial fills/exits
    """
    symbol: str
    entry_price: float
    entry_amount: float
    entry_value_usd: float
    entry_timestamp: str
    token_address: str = ""  # ✅ ADDED: For feedback loop to scanner
    chain: str = ""  # ✅ ADDED: For multi-chain tracking
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
        
        self.entry_price = new_total_cost / new_total_amount if new_total_amount > 0 else price
        self.entry_amount = new_total_amount
        self.total_cost_basis = new_total_cost
        self.entry_value_usd = new_total_cost
    
    def update_for_partial_exit(self, amount_sold: float):
        """Update position for partial exit"""
        DUST_THRESHOLD = 0.000001
        remaining_amount = self.entry_amount - amount_sold
        
        if remaining_amount <= DUST_THRESHOLD:
            return None  # Full exit
        
        ratio_remaining = remaining_amount / self.entry_amount
        self.entry_amount = remaining_amount
        self.total_cost_basis *= ratio_remaining
        self.entry_value_usd = self.total_cost_basis
        
        return self
    
    def update_price_tracking(self, current_price: float):
        """Update highest and lowest price tracking"""
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price_since_entry:
            self.lowest_price_since_entry = current_price
    
    def get_trailing_stop_price(self, trailing_stop_pct: float) -> float:
        """Calculate trailing stop price"""
        return self.highest_price * (1 - trailing_stop_pct)
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        if data['lowest_price_since_entry'] == float('inf'):
            data['lowest_price_since_entry'] = None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TrackedPosition':
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
    
    def should_stop_loss(self, stop_loss_pct: float) -> bool:
        return self.pnl_pct <= stop_loss_pct * 100
    
    def should_take_profit(self, take_profit_pct: float) -> bool:
        return self.pnl_pct >= take_profit_pct * 100
    
    def should_trailing_stop(self, trailing_stop_pct: float) -> bool:
        if self.highest_price <= 0:
            return False
        trailing_stop_price = self.highest_price * (1 - trailing_stop_pct)
        return self.current_price <= trailing_stop_price and self.is_profitable


# ============================================================================
# TRADE DECISIONS
# ============================================================================

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


# ============================================================================
# METRICS
# ============================================================================

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
        
        import statistics
        
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
# TOKEN DISCOVERY (NEW)
# ============================================================================

@dataclass
class DiscoveredToken:
    """
    Represents a token discovered by the self-thinking bot
    """
    symbol: str
    address: str
    chain: str
    price: float
    liquidity_usd: float
    volume_24h: float
    change_24h_pct: float
    market_cap: Optional[float] = None
    opportunity_score: float = 0.0
    discovery_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'DiscoveredToken':
        return cls(**data)


# ============================================================================
# EXCEPTIONS
# ============================================================================

class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open"""
    pass


class InsufficientLiquidityError(Exception):
    """Raised when token has insufficient liquidity"""
    pass


class InvalidTokenError(Exception):
    """Raised when token is invalid or not found"""
    pass