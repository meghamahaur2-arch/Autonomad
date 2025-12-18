#!/usr/bin/env python3
"""
Token Address Checker
Quick diagnostic tool to check if addresses are valid
"""
import asyncio
import sys
from token_validator import token_validator
from market_scanner import MarketScanner
from logging_manager import get_logger

logger = get_logger("AddressChecker")


async def check_discovered_tokens():
    """
    Run a market scan and validate all discovered tokens
    """
    scanner = MarketScanner()
    
    try:
        logger.info("🔍 Running market scan...")
        tokens = await scanner.scan_market()
        
        logger.info(f"\n{'='*70}")
        logger.info(f"DISCOVERED {len(tokens)} TOKENS - VALIDATING ADDRESSES")
        logger.info(f"{'='*70}\n")
        
        valid_count = 0
        invalid_count = 0
        
        for i, token in enumerate(tokens, 1):
            is_valid, reason = token_validator.validate_token(
                address=token.address,
                chain=token.chain,
                symbol=token.symbol,
                price=token.price,
                liquidity=token.liquidity_usd
            )
            
            status = "✅" if is_valid else "❌"
            
            print(f"{status} {i}. {token.symbol} on {token.chain}")
            print(f"   Address: {token.address[:20]}...")
            print(f"   Price: ${token.price:.6f} | Liquidity: ${token.liquidity_usd:,.0f}")
            
            if is_valid:
                print(f"   Status: VALID")
                valid_count += 1
            else:
                print(f"   Status: INVALID - {reason}")
                invalid_count += 1
            
            print()
        
        logger.info(f"\n{'='*70}")
        logger.info(f"VALIDATION SUMMARY")
        logger.info(f"{'='*70}")
        logger.info(f"   ✅ Valid:   {valid_count}")
        logger.info(f"   ❌ Invalid: {invalid_count}")
        logger.info(f"   Total:     {len(tokens)}")
        logger.info(f"   Success Rate: {valid_count/len(tokens)*100:.1f}%")
        logger.info(f"{'='*70}\n")
        
        # Show validator stats
        stats = token_validator.get_stats()
        logger.info("Validator Statistics:")
        logger.info(f"   Cache size: {stats['cache_size']}")
        logger.info(f"   Blacklisted: {stats['blacklisted_count']}")
        
    finally:
        await scanner.close()


async def check_single_address(address: str, chain: str, symbol: str = "TEST"):
    """
    Check a single address
    """
    logger.info(f"\n{'='*70}")
    logger.info(f"CHECKING SINGLE ADDRESS")
    logger.info(f"{'='*70}")
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Chain: {chain}")
    logger.info(f"Address: {address}")
    logger.info(f"{'='*70}\n")
    
    is_valid, reason = token_validator.validate_token(
        address=address,
        chain=chain,
        symbol=symbol
    )
    
    if is_valid:
        logger.info("✅ ADDRESS IS VALID")
    else:
        logger.error(f"❌ ADDRESS IS INVALID: {reason}")
    
    logger.info(f"\n{'='*70}\n")


async def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        # Check specific address
        if len(sys.argv) < 3:
            print("Usage: python check_addresses.py <address> <chain> [symbol]")
            print("Example: python check_addresses.py 0x1234... ethereum TOKEN")
            return
        
        address = sys.argv[1]
        chain = sys.argv[2]
        symbol = sys.argv[3] if len(sys.argv) > 3 else "TEST"
        
        await check_single_address(address, chain, symbol)
    else:
        # Run full scan
        await check_discovered_tokens()


if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║              TOKEN ADDRESS VALIDATOR                         ║
    ║                                                              ║
    ║  Checks if discovered token addresses are valid             ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⌨️ Interrupted by user")
    except Exception as e:
        logger.error(f"💀 Error: {e}", exc_info=True)