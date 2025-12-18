"""
Configuration Management Module
Handles all configuration, validation, and token registry
"""
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple
from dotenv import load_dotenv

load_dotenv()


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
    Centralized configuration with environment variable support
    """
    
    # =========================================================================
    # API Configuration
    # =========================================================================
    RECALL_API_KEY: str = os.getenv("RECALL_API_KEY", "")
    USE_SANDBOX: bool = os.getenv("RECALL_USE_SANDBOX", "false").lower() == "true"
    COMPETITION_ID: str = os.getenv("COMPETITION_ID", "")
    SANDBOX_URL: str = "https://api.sandbox.competitions.recall.network"
    PRODUCTION_URL: str = "https://api.competitions.recall.network"
    
    # =========================================================================
    # Retry and Resilience
    # =========================================================================
    API_RETRY_COUNT: int = int(os.getenv("API_RETRY_COUNT", "5"))
    API_TIMEOUT_SECONDS: int = int(os.getenv("API_TIMEOUT_SECONDS", "30"))
    API_BACKOFF_BASE: float = float(os.getenv("API_BACKOFF_BASE", "2.0"))
    API_BACKOFF_MAX: float = float(os.getenv("API_BACKOFF_MAX", "60.0"))
    
    # Circuit Breaker
    CIRCUIT_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
    CIRCUIT_RECOVERY_TIMEOUT: int = int(os.getenv("CIRCUIT_RECOVERY_TIMEOUT", "60"))
    
    # =========================================================================
    # Trading Intervals
    # =========================================================================
    TRADING_INTERVAL: int = int(os.getenv("TRADING_INTERVAL_SECONDS", "300"))
    MARKET_DATA_CACHE_TTL: int = int(os.getenv("MARKET_DATA_CACHE_TTL", "60"))
    MARKET_SCAN_DEPTH: int = int(os.getenv("MARKET_SCAN_DEPTH", "50"))
    
    # =========================================================================
    # Position Sizing
    # =========================================================================
    MIN_TRADE_SIZE: float = float(os.getenv("MIN_TRADE_SIZE", "0.000001"))
    BASE_POSITION_SIZE: float = float(os.getenv("BASE_POSITION_SIZE", "300"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "1000"))
    MIN_TRADES_PER_DAY: int = int(os.getenv("MIN_TRADES_PER_DAY", "3"))
    MIN_POSITION_VALUE: float = float(os.getenv("MIN_POSITION_VALUE", "1.0"))
    
    # =========================================================================
    # Risk Management
    # =========================================================================
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "-0.08"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))
    TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.05"))
    MAX_PORTFOLIO_RISK: float = float(os.getenv("MAX_PORTFOLIO_RISK", "0.70"))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "-0.10"))
    
    # =========================================================================
    # Self-Thinking Bot Parameters
    # =========================================================================
    AUTO_DISCOVERY_MODE: bool = os.getenv("AUTO_DISCOVERY_MODE", "true").lower() == "true"
    MIN_LIQUIDITY_USD: float = float(os.getenv("MIN_LIQUIDITY_USD", "50000"))
    MIN_VOLUME_24H_USD: float = float(os.getenv("MIN_VOLUME_24H_USD", "100000"))
    OPPORTUNITY_SCORE_THRESHOLD: float = float(os.getenv("OPPORTUNITY_SCORE_THRESHOLD", "5.0"))
    
    # =========================================================================
    # Strategy Filters
    # =========================================================================
    VOLUME_SURGE_THRESHOLD: float = float(os.getenv("VOLUME_SURGE_THRESHOLD", "0.20"))
    MEAN_REVERSION_LOWER_BOUND: float = float(os.getenv("MEAN_REVERSION_LOWER_BOUND", "-0.03"))
    MEAN_REVERSION_UPPER_BOUND: float = float(os.getenv("MEAN_REVERSION_UPPER_BOUND", "-0.15"))
    MOMENTUM_THRESHOLD: float = float(os.getenv("MOMENTUM_THRESHOLD", "0.01"))
    BREAKOUT_THRESHOLD: float = float(os.getenv("BREAKOUT_THRESHOLD", "0.05"))
    
    # Strategy Toggles
    STRATEGY_MODE: str = os.getenv("STRATEGY_MODE", "BALANCED")
    ENABLE_MEAN_REVERSION: bool = os.getenv("ENABLE_MEAN_REVERSION", "true").lower() == "true"
    ENABLE_MOMENTUM: bool = os.getenv("ENABLE_MOMENTUM", "true").lower() == "true"
    ENABLE_TRAILING_STOP: bool = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
    ENABLE_BREAKOUT: bool = os.getenv("ENABLE_BREAKOUT", "true").lower() == "true"
    ENABLE_VOLUME_SPIKE: bool = os.getenv("ENABLE_VOLUME_SPIKE", "true").lower() == "true"
    ENABLE_ADAPTIVE_MODE: bool = os.getenv("ENABLE_ADAPTIVE_MODE", "true").lower() == "true"
    
    # =========================================================================
    # Persistence
    # =========================================================================
    STATE_FILE: str = os.getenv("STATE_FILE", "agent_state.json")
    STATE_BACKUP_COUNT: int = int(os.getenv("STATE_BACKUP_COUNT", "5"))
    
    # =========================================================================
    # LLM Configuration (ELITE HYBRID MODE)
    # =========================================================================
    ENABLE_LLM_BRAIN: bool = os.getenv("ENABLE_LLM_BRAIN", "true").lower() == "true"
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen72b")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "500"))
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))
    
    # =========================================================================
    # Logging (Memory Only)
    # =========================================================================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_MAX_LINES: int = int(os.getenv("LOG_MAX_LINES", "1000"))
    
    # =========================================================================
    # Token Registry - Multi-Chain Support
    # =========================================================================
    TOKENS: Dict[str, TokenConfig] = {
        # Stablecoins (Multiple Chains)
        "USDC": TokenConfig("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "eth", True, 6),
        "DAI": TokenConfig("0x6B175474E89094C44Da98b954EedeAC495271d0F", "eth", True, 18),
        "USDC_POLYGON": TokenConfig("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "polygon", True, 6),
        "USDC_ARBITRUM": TokenConfig("0xaf88d065e77c8cc2239327c5edb3a432268e5831", "arbitrum", True, 6),
        "USDC_OPTIMISM": TokenConfig("0x7f5c764cbc14f9669b88837ca1490cca17c31607", "optimism", True, 6),
        "USDBC_BASE": TokenConfig("0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", "base", True, 6),
        "USDC_SOLANA": TokenConfig("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "svm", True, 6),
        
        # Trading Tokens (Ethereum Mainnet)
        "WETH": TokenConfig("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "eth", False, 18),
        "WBTC": TokenConfig("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "eth", False, 8),
        "UNI": TokenConfig("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "eth", False, 18),
        "LINK": TokenConfig("0x514910771AF9Ca656af840dff83E8264EcF986CA", "eth", False, 18),
        "AAVE": TokenConfig("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9", "eth", False, 18),
        "SNX": TokenConfig("0xC011a73ee8576Fb46F5E1c5751cA3B9Fe0af2a6F", "eth", False, 18),
        "CRV": TokenConfig("0xD533a949740bb3306d119CC777fa900bA034cd52", "eth", False, 18),
        "MKR": TokenConfig("0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2", "eth", False, 18),
        
        # Meme Tokens
        "BONK": TokenConfig("0x1151CB3d861920e07a38e03eead12c32178567F6", "eth", False, 5),
        "FLOKI": TokenConfig("0xcf0C122c6b73ff809C693DB761e7BaeBe62b6a2E", "eth", False, 9),
        "PEPE": TokenConfig("0x6982508145454Ce325dDbE47a25d4ec3d2311933", "eth", False, 18),
        "SHIB": TokenConfig("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", "eth", False, 18),
        
        # Popular tokens on Polygon
        "WETH_POLYGON": TokenConfig("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", "polygon", False, 18),
        "WBTC_POLYGON": TokenConfig("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", "polygon", False, 8),
        "LINK_POLYGON": TokenConfig("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", "polygon", False, 18),
        
        # Arbitrum
        "WETH_ARBITRUM": TokenConfig("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "arbitrum", False, 18),
        "WBTC_ARBITRUM": TokenConfig("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "arbitrum", False, 8),
        "LINK_ARBITRUM": TokenConfig("0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", "arbitrum", False, 18),
        
        # Base
        "WETH_BASE": TokenConfig("0x4200000000000000000000000000000000000006", "base", False, 18),
        "RECALL": TokenConfig("0x1f16e03C1a5908818F47f6EE7bB16690b40D0671", "base", False, 18),
        
        # Solana
        "SOL": TokenConfig("So11111111111111111111111111111111111111112", "svm", False, 9),
        "BONK_SOLANA": TokenConfig("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "svm", False, 5),
    }
    
    # Map display symbols to their chain variants
    SYMBOL_TO_CHAINS: Dict[str, List[str]] = {
        "USDC": ["USDC", "USDC_POLYGON", "USDC_ARBITRUM", "USDC_OPTIMISM", "USDBC_BASE", "USDC_SOLANA"],
        "WETH": ["WETH", "WETH_POLYGON", "WETH_ARBITRUM", "WETH_BASE"],
        "WBTC": ["WBTC", "WBTC_POLYGON", "WBTC_ARBITRUM"],
        "LINK": ["LINK", "LINK_POLYGON", "LINK_ARBITRUM"],
        "BONK": ["BONK", "BONK_SOLANA"],
        "RECALL": ["RECALL"]
    }
    
    # Correlated pairs to avoid concentration
    CORRELATED_PAIRS: List[Tuple[str, str]] = [
        ("WETH", "WBTC"),
        ("SNX", "AAVE"),
        ("UNI", "AAVE"),
        ("BONK", "FLOKI"),
        ("BONK", "WIF"),
        ("FLOKI", "WIF"),
    ]
    
    @property
    def base_url(self) -> str:
        return self.SANDBOX_URL if self.USE_SANDBOX else self.PRODUCTION_URL
    
    def validate(self) -> List[str]:
        """
        ✅ ENHANCED: Validate configuration and return list of errors
        """
        errors = []
        
        # API Configuration
        if not self.RECALL_API_KEY:
            errors.append("RECALL_API_KEY is required")
        
        # ✅ ADDED: LLM Configuration Validation
        if self.ENABLE_LLM_BRAIN:
            if not self.LLM_API_KEY:
                errors.append("LLM_API_KEY required when ENABLE_LLM_BRAIN=true")
            if not self.LLM_BASE_URL:
                errors.append("LLM_BASE_URL required when ENABLE_LLM_BRAIN=true")
            if not self.LLM_MODEL:
                errors.append("LLM_MODEL required when ENABLE_LLM_BRAIN=true")
            if self.LLM_TEMPERATURE < 0 or self.LLM_TEMPERATURE > 2:
                errors.append("LLM_TEMPERATURE must be between 0 and 2")
            if self.LLM_MAX_TOKENS < 1:
                errors.append("LLM_MAX_TOKENS must be positive")
        
        # Position Sizing
        if self.MAX_POSITION_PCT <= 0 or self.MAX_POSITION_PCT > 1:
            errors.append("MAX_POSITION_PCT must be between 0 and 1")
        if self.MAX_PORTFOLIO_RISK <= 0 or self.MAX_PORTFOLIO_RISK > 1:
            errors.append("MAX_PORTFOLIO_RISK must be between 0 and 1")
        
        # Risk Management
        if self.STOP_LOSS_PCT >= 0:
            errors.append("STOP_LOSS_PCT must be negative")
        if self.TAKE_PROFIT_PCT <= 0:
            errors.append("TAKE_PROFIT_PCT must be positive")
        if self.TRAILING_STOP_PCT <= 0 or self.TRAILING_STOP_PCT >= 1:
            errors.append("TRAILING_STOP_PCT must be between 0 and 1")
        if self.MAX_DAILY_LOSS_PCT >= 0:
            errors.append("MAX_DAILY_LOSS_PCT must be negative")
        
        # Trading Intervals
        if self.TRADING_INTERVAL < 60:
            errors.append("TRADING_INTERVAL should be at least 60 seconds")
        
        # Discovery Parameters
        if self.AUTO_DISCOVERY_MODE:
            if self.MIN_LIQUIDITY_USD < 0:
                errors.append("MIN_LIQUIDITY_USD must be non-negative")
            if self.MIN_VOLUME_24H_USD < 0:
                errors.append("MIN_VOLUME_24H_USD must be non-negative")
            if self.OPPORTUNITY_SCORE_THRESHOLD < 0:
                errors.append("OPPORTUNITY_SCORE_THRESHOLD must be non-negative")
        
        return errors


# Global config instance
config = Config()