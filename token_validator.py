"""
Token Address Validator - FIXED: Better Solana validation
✅ More lenient Solana address pattern (accepts lowercase)
✅ Handles all valid Base58 addresses
"""
import re
from typing import Optional, Dict, Tuple, Set
from logging_manager import get_logger

logger = get_logger("TokenValidator")


class TokenValidator:
    """
    Validates token addresses and metadata before trades
    ✅ FIXED: Proper Solana Base58 validation
    """
    
    # Valid address patterns by chain
    ADDRESS_PATTERNS = {
        "ethereum": r"^0x[a-fA-F0-9]{40}$",
        "polygon": r"^0x[a-fA-F0-9]{40}$",
        "arbitrum": r"^0x[a-fA-F0-9]{40}$",
        "base": r"^0x[a-fA-F0-9]{40}$",
        "optimism": r"^0x[a-fA-F0-9]{40}$",
        "bsc": r"^0x[a-fA-F0-9]{40}$",
        # ✅ FIXED: More lenient Solana pattern (accepts all valid Base58)
        "solana": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",
        "svm": r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",
    }
    
    # Supported chains
    SUPPORTED_CHAINS = {
        "ethereum", "polygon", "arbitrum", "base", "optimism", "bsc", "solana", "svm", "eth"
    }
    
    # ✅ Whitelisted addresses (from config)
    WHITELISTED_ADDRESSES = {
        # Ethereum
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
        "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
        
        # Polygon
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC_POLYGON",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e_POLYGON",
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH_POLYGON",
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": "WBTC_POLYGON",
        
        # Arbitrum
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC_ARBITRUM",
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH_ARBITRUM",
        
        # Base
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": "USDBC_BASE",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC_BASE",
        "0x4200000000000000000000000000000000000006": "WETH_BASE",
        "0x1f16e03c1a5908818f47f6ee7bb16690b40d0671": "RECALL",
        
        # Optimism
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC_OPTIMISM",
        "0x4200000000000000000000000000000000000006": "WETH_OPTIMISM",
        
        # Solana (lowercase for matching)
        "epjfwdd5aufqssqem2qn1xzybape8g4wegegkzwytdt1v": "USDC_SOLANA",
        "so11111111111111111111111111111111111111112": "SOL",
    }
    
    # Known problematic patterns
    INVALID_PATTERNS = [
        r"^0x0+$",  # All zeros
        r"^0x[fF]+$",  # All F's
        r"^0x0{39}[1-9a-fA-F]$",  # Almost all zeros
        r"^0xff{5,}",  # Too many F's at start
    ]
    
    def __init__(self):
        self._validation_cache: Dict[str, bool] = {}
        self._failed_addresses: Dict[str, int] = {}
        
        # Pre-whitelist known good addresses
        for addr in self.WHITELISTED_ADDRESSES.keys():
            self._validation_cache[addr.lower()] = True
        
        logger.info("🔍 Token Validator initialized")
        logger.info(f"   ✅ Whitelisted {len(self.WHITELISTED_ADDRESSES)} verified addresses")
    
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
        
        # ✅ Check whitelist first (fast path)
        if address.lower() in self.WHITELISTED_ADDRESSES:
            return True, "Whitelisted"
        
        # Cache check
        cache_key = f"{address}_{chain}"
        if cache_key in self._validation_cache:
            cached = self._validation_cache[cache_key]
            return cached, "Cached" if cached else "Previously failed"
        
        # Check if previously failed
        if address.lower() in self._failed_addresses:
            fail_count = self._failed_addresses[address.lower()]
            if fail_count >= 2:
                return False, f"Blacklisted ({fail_count} failures)"
        
        # ✅ CRITICAL: Skip unsupported chains early
        chain_lower = chain.lower()
        if chain_lower not in self.SUPPORTED_CHAINS:
            # Don't cache or blacklist - just skip
            return False, f"Unsupported chain: {chain}"
        
        # Validation 1: Address format
        is_valid, reason = self._validate_address_format(address, chain)
        if not is_valid:
            self._mark_invalid(cache_key, address, f"format_{reason}")
            return False, reason
        
        # Validation 2: Known bad patterns (EVM only)
        if chain_lower in ["ethereum", "polygon", "arbitrum", "base", "optimism", "bsc", "eth"]:
            is_valid, reason = self._check_bad_patterns(address)
            if not is_valid:
                self._mark_invalid(cache_key, address, f"pattern_{reason}")
                return False, reason
            
            # Validation 3: Entropy check (EVM only)
            is_valid, reason = self._check_address_entropy(address, chain)
            if not is_valid:
                self._mark_invalid(cache_key, address, f"entropy_{reason}")
                return False, reason
        
        # Validation 4: Symbol sanity
        is_valid, reason = self._validate_symbol(symbol)
        if not is_valid:
            self._mark_invalid(cache_key, address, f"symbol_{reason}")
            return False, reason
        
        # Validation 5: Price sanity
        if price > 0:
            is_valid, reason = self._validate_price(price)
            if not is_valid:
                self._mark_invalid(cache_key, address, f"price_{reason}")
                return False, reason
        
        # Validation 6: Liquidity sanity
        if liquidity > 0:
            is_valid, reason = self._validate_liquidity(liquidity)
            if not is_valid:
                self._mark_invalid(cache_key, address, f"liquidity_{reason}")
                return False, reason
        
        # All checks passed
        self._validation_cache[cache_key] = True
        return True, "Valid"
    
    def _validate_address_format(self, address: str, chain: str) -> Tuple[bool, str]:
        """Validate address format for chain"""
        if not address or not chain:
            return False, "Empty address or chain"
        
        # Normalize chain name
        chain = chain.lower()
        
        # ✅ Handle chain aliases
        if chain == "eth":
            chain = "ethereum"
        
        if chain not in self.ADDRESS_PATTERNS:
            return False, f"Unsupported chain: {chain}"
        
        # Check pattern
        pattern = self.ADDRESS_PATTERNS[chain]
        if not re.match(pattern, address):
            return False, f"Invalid {chain} address format"
        
        return True, "Format OK"
    
    def _check_bad_patterns(self, address: str) -> Tuple[bool, str]:
        """Check for known bad address patterns (EVM only)"""
        for pattern in self.INVALID_PATTERNS:
            if re.match(pattern, address.lower()):
                return False, "Suspicious address pattern"
        
        return True, "Pattern OK"
    
    def _check_address_entropy(self, address: str, chain: str) -> Tuple[bool, str]:
        """
        Check if address has reasonable entropy (EVM only)
        """
        if chain.lower() in ["solana", "svm"]:
            return True, "Entropy OK (Solana)"
        
        # Remove 0x prefix
        hex_part = address[2:].lower() if address.startswith("0x") else address.lower()
        
        if len(hex_part) != 40:
            return False, "Invalid length"
        
        # Check for too many repeated characters
        from collections import Counter
        char_counts = Counter(hex_part)
        
        max_count = max(char_counts.values())
        if max_count > 20:
            return False, "Low entropy (repeated chars)"
        
        # Check for long runs of same character
        max_run = 1
        current_run = 1
        for i in range(1, len(hex_part)):
            if hex_part[i] == hex_part[i-1]:
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 1
        
        if max_run > 6:  # ✅ Increased from 5 to 6 (less strict)
            return False, "Low entropy (long run)"
        
        # Check for sequential patterns
        for i in range(len(hex_part) - 3):
            quad = hex_part[i:i+4]
            if self._is_sequential(quad):
                return False, "Sequential pattern"
        
        return True, "Entropy OK"
    
    def _is_sequential(self, s: str) -> bool:
        """Check if string is sequential"""
        if len(s) < 3:
            return False
        
        ascending = all(ord(s[i+1]) - ord(s[i]) == 1 for i in range(len(s)-1))
        if ascending:
            return True
        
        descending = all(ord(s[i]) - ord(s[i+1]) == 1 for i in range(len(s)-1))
        return descending
    
    def _validate_symbol(self, symbol: str) -> Tuple[bool, str]:
        """Validate token symbol"""
        if not symbol:
            return False, "Empty symbol"
        
        # Length check
        if len(symbol) < 1 or len(symbol) > 15:
            return False, f"Symbol length {len(symbol)} invalid"
        
        # Suspicious symbols
        suspicious = ["test", "xxx", "scam", "rug", "fake", "bot", "dead", "rip"]
        if any(p in symbol.lower() for p in suspicious):
            return False, "Suspicious symbol"
        
        # Must be alphanumeric
        if not re.match(r"^[A-Za-z0-9_\-\.]+$", symbol):
            return False, "Invalid symbol characters"
        
        return True, "Symbol OK"
    
    def _validate_price(self, price: float) -> Tuple[bool, str]:
        """Validate price sanity"""
        if price <= 0:
            return False, "Price <= 0"
        
        if price > 100_000_000:
            return False, "Price too high"
        
        # ✅ More lenient for very small prices (memecoins)
        if price < 0.00000000001:
            return False, "Price too low"
        
        return True, "Price OK"
    
    def _validate_liquidity(self, liquidity: float) -> Tuple[bool, str]:
        """Validate liquidity sanity"""
        # ✅ More lenient for scanner (will be filtered by strategy later)
        if liquidity < 5_000:  # $5k minimum
            return False, "Liquidity too low"
        
        if liquidity > 1_000_000_000_000:
            return False, "Liquidity suspiciously high"
        
        return True, "Liquidity OK"
    
    def _mark_invalid(self, cache_key: str, address: str, reason: str):
        """Mark token as invalid"""
        self._validation_cache[cache_key] = False
        addr_lower = address.lower()
        self._failed_addresses[addr_lower] = self._failed_addresses.get(addr_lower, 0) + 1
        
        fail_count = self._failed_addresses[addr_lower]
        
        # Only log if significant
        if fail_count >= 2 or "liquidity" not in reason.lower():
            logger.debug(f"❌ {address[:10]}... ({reason}, fails: {fail_count})")
    
    def record_trade_failure(self, address: str, chain: str):
        """Record that a trade with this address failed"""
        cache_key = f"{address}_{chain}"
        addr_lower = address.lower()
        
        self._failed_addresses[addr_lower] = self._failed_addresses.get(addr_lower, 0) + 1
        self._validation_cache[cache_key] = False
        
        fail_count = self._failed_addresses[addr_lower]
        logger.warning(f"❌ Trade failed for {address[:10]}... (fail count: {fail_count})")
        
        if fail_count >= 2:
            logger.error(f"🚫 Blacklisted: {address[:10]}... (2+ failures)")
    
    def is_blacklisted(self, address: str) -> bool:
        """Check if address is blacklisted"""
        return self._failed_addresses.get(address.lower(), 0) >= 2
    
    def clear_blacklist(self):
        """Clear blacklist"""
        before = len(self._failed_addresses)
        self._failed_addresses.clear()
        logger.info(f"🗑️ Cleared {before} blacklisted addresses")
    
    def get_stats(self) -> Dict:
        """Get validator statistics"""
        return {
            "cache_size": len(self._validation_cache),
            "whitelisted": len(self.WHITELISTED_ADDRESSES),
            "blacklisted_count": sum(1 for c in self._failed_addresses.values() if c >= 2),
            "failed_addresses": len(self._failed_addresses),
            "total_validated": len(self._validation_cache)
        }


# Global validator instance
token_validator = TokenValidator()