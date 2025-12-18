"""
Token Address Validator - CRITICAL FIX
Validates token addresses before trading to prevent API errors
"""
import re
from typing import Optional, Dict, Tuple
from logging_manager import get_logger

logger = get_logger("TokenValidator")


class TokenValidator:
    """
    Validates token addresses and metadata before trades
    Prevents "Unable to determine price" errors
    """
    
    # Valid address patterns by chain
    ADDRESS_PATTERNS = {
        "ethereum": r"^0x[a-fA-F0-9]{40}$",
        "polygon": r"^0x[a-fA-F0-9]{40}$",
        "arbitrum": r"^0x[a-fA-F0-9]{40}$",
        "base": r"^0x[a-fA-F0-9]{40}$",
        "optimism": r"^0x[a-fA-F0-9]{40}$",
        "bsc": r"^0x[a-fA-F0-9]{40}$",
        "solana": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",  # Base58
        "svm": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",
    }
    
    # Known problematic patterns to filter
    INVALID_PATTERNS = [
        r"^0x0+$",  # All zeros
        r"^0x[fF]+$",  # All F's
        r"^0x0{39}[1-9a-fA-F]$",  # Almost all zeros
    ]
    
    def __init__(self):
        self._validation_cache: Dict[str, bool] = {}
        self._failed_addresses: Dict[str, int] = {}
        logger.info("🔍 Token Validator initialized")
    
    def validate_token(
        self,
        address: str,
        chain: str,
        symbol: str,
        price: float = 0,
        liquidity: float = 0
    ) -> Tuple[bool, str]:
        """
        Comprehensive token validation
        Returns: (is_valid, reason)
        """
        
        # Cache check
        cache_key = f"{address}_{chain}"
        if cache_key in self._validation_cache:
            return self._validation_cache[cache_key], "Cached"
        
        # Check if previously failed
        if address.lower() in self._failed_addresses:
            fail_count = self._failed_addresses[address.lower()]
            if fail_count >= 3:
                return False, f"Failed {fail_count} times"
        
        # Validation 1: Address format
        is_valid, reason = self._validate_address_format(address, chain)
        if not is_valid:
            self._mark_invalid(cache_key, address)
            return False, reason
        
        # Validation 2: Known bad patterns
        is_valid, reason = self._check_bad_patterns(address)
        if not is_valid:
            self._mark_invalid(cache_key, address)
            return False, reason
        
        # Validation 3: Symbol sanity
        is_valid, reason = self._validate_symbol(symbol)
        if not is_valid:
            self._mark_invalid(cache_key, address)
            return False, reason
        
        # Validation 4: Price sanity
        if price > 0:
            is_valid, reason = self._validate_price(price)
            if not is_valid:
                self._mark_invalid(cache_key, address)
                return False, reason
        
        # Validation 5: Liquidity sanity
        if liquidity > 0:
            is_valid, reason = self._validate_liquidity(liquidity)
            if not is_valid:
                self._mark_invalid(cache_key, address)
                return False, reason
        
        # All checks passed
        self._validation_cache[cache_key] = True
        logger.debug(f"✅ Valid token: {symbol} ({address[:10]}...)")
        return True, "Valid"
    
    def _validate_address_format(self, address: str, chain: str) -> Tuple[bool, str]:
        """Validate address format for chain"""
        if not address or not chain:
            return False, "Empty address or chain"
        
        # Normalize chain name
        chain = chain.lower()
        if chain not in self.ADDRESS_PATTERNS:
            return False, f"Unknown chain: {chain}"
        
        # Check pattern
        pattern = self.ADDRESS_PATTERNS[chain]
        if not re.match(pattern, address):
            return False, f"Invalid {chain} address format"
        
        return True, "Format OK"
    
    def _check_bad_patterns(self, address: str) -> Tuple[bool, str]:
        """Check for known bad address patterns"""
        for pattern in self.INVALID_PATTERNS:
            if re.match(pattern, address.lower()):
                return False, "Suspicious address pattern"
        
        return True, "Pattern OK"
    
    def _validate_symbol(self, symbol: str) -> Tuple[bool, str]:
        """Validate token symbol"""
        if not symbol:
            return False, "Empty symbol"
        
        # Length check
        if len(symbol) < 1 or len(symbol) > 15:
            return False, f"Symbol length {len(symbol)} invalid"
        
        # Suspicious symbols
        suspicious = ["test", "xxx", "scam", "rug", "fake", "bot"]
        if any(p in symbol.lower() for p in suspicious):
            return False, "Suspicious symbol"
        
        # Must be alphanumeric (with some exceptions)
        if not re.match(r"^[A-Za-z0-9_\-]+$", symbol):
            return False, "Invalid symbol characters"
        
        return True, "Symbol OK"
    
    def _validate_price(self, price: float) -> Tuple[bool, str]:
        """Validate price sanity"""
        if price <= 0:
            return False, "Price <= 0"
        
        if price > 100_000_000:
            return False, "Price too high"
        
        if price < 0.000000001:
            return False, "Price too low"
        
        return True, "Price OK"
    
    def _validate_liquidity(self, liquidity: float) -> Tuple[bool, str]:
        """Validate liquidity sanity"""
        if liquidity < 10_000:
            return False, "Liquidity too low"
        
        if liquidity > 1_000_000_000_000:
            return False, "Liquidity suspiciously high"
        
        return True, "Liquidity OK"
    
    def _mark_invalid(self, cache_key: str, address: str):
        """Mark token as invalid"""
        self._validation_cache[cache_key] = False
        addr_lower = address.lower()
        self._failed_addresses[addr_lower] = self._failed_addresses.get(addr_lower, 0) + 1
    
    def record_trade_failure(self, address: str, chain: str):
        """
        Record that a trade with this address failed
        Used to build blacklist
        """
        cache_key = f"{address}_{chain}"
        addr_lower = address.lower()
        
        self._failed_addresses[addr_lower] = self._failed_addresses.get(addr_lower, 0) + 1
        self._validation_cache[cache_key] = False
        
        fail_count = self._failed_addresses[addr_lower]
        logger.warning(f"❌ Trade failed for {address[:10]}... (fail count: {fail_count})")
        
        if fail_count >= 3:
            logger.error(f"🚫 Blacklisted: {address[:10]}... (3+ failures)")
    
    def is_blacklisted(self, address: str) -> bool:
        """Check if address is blacklisted"""
        return self._failed_addresses.get(address.lower(), 0) >= 3
    
    def get_stats(self) -> Dict:
        """Get validator statistics"""
        return {
            "cache_size": len(self._validation_cache),
            "blacklisted_count": sum(1 for c in self._failed_addresses.values() if c >= 3),
            "failed_addresses": len(self._failed_addresses)
        }


# Global validator instance
token_validator = TokenValidator()