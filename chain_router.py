"""
Intelligent Cross-Chain Router
Handles chain-to-chain token routing when direct routes don't exist
"""
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from logging_manager import get_logger
from config import config

logger = get_logger("ChainRouter")


@dataclass
class ChainRoute:
    """Represents a trading route between chains"""
    from_chain: str
    to_chain: str
    intermediate_steps: List[Dict]
    total_steps: int
    estimated_cost: float  # Gas cost estimate
    
    def __repr__(self):
        return f"Route({self.from_chain} → {self.to_chain}, {self.total_steps} steps)"


class ChainRouter:
    """
    Intelligent router for cross-chain trades
    Handles scenarios where direct trading routes don't exist
    """
    
    # Chain normalization map
    CHAIN_ALIASES = {
        "eth": "ethereum",
        "ethereum": "ethereum",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "base": "base",
        "optimism": "optimism",
        "bsc": "bsc",
        "solana": "solana",
        "svm": "solana"
    }
    
    # USDC addresses per chain (for intermediate swaps)
    USDC_BY_CHAIN = {
        "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "polygon": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # Native USDC
        "arbitrum": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "base": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # Native USDC
        "optimism": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
        "bsc": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    }
    
    # Bridged USDC (legacy) - fallback options
    BRIDGED_USDC = {
        "polygon": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e
        "base": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"  # USDbC
    }
    
    # Gas cost estimates (relative, higher = more expensive)
    GAS_COSTS = {
        "ethereum": 10,
        "polygon": 1,
        "arbitrum": 2,
        "base": 2,
        "optimism": 3,
        "bsc": 1,
        "solana": 1
    }
    
    # Supported bridge routes (for future expansion)
    BRIDGE_ROUTES = {
        ("ethereum", "polygon"): {"bridge": "polygon_pos", "avg_time_mins": 7},
        ("ethereum", "arbitrum"): {"bridge": "arbitrum_bridge", "avg_time_mins": 10},
        ("ethereum", "base"): {"bridge": "base_bridge", "avg_time_mins": 10},
        ("ethereum", "optimism"): {"bridge": "optimism_bridge", "avg_time_mins": 10},
        ("polygon", "ethereum"): {"bridge": "polygon_pos", "avg_time_mins": 30},
        # Add more routes as needed
    }
    
    def __init__(self):
        logger.info("🔗 Chain Router initialized")
        logger.info(f"   ✅ Supporting {len(self.USDC_BY_CHAIN)} chains")
        logger.info(f"   ✅ {len(self.BRIDGE_ROUTES)} bridge routes available")
    
    def normalize_chain(self, chain: str) -> str:
        """Normalize chain name"""
        return self.CHAIN_ALIASES.get(chain.lower(), chain.lower())
    
    def find_best_route(
        self,
        from_token_address: str,
        from_chain: str,
        to_token_address: str,
        to_chain: str,
        available_holdings: Dict[str, Dict]
    ) -> Optional[ChainRoute]:
        """
        Find best trading route between chains
        
        Args:
            from_token_address: Source token address
            from_chain: Source chain
            to_token_address: Destination token address
            to_chain: Destination chain
            available_holdings: Dict of available holdings
        
        Returns:
            ChainRoute if valid route exists, None otherwise
        """
        from_chain_norm = self.normalize_chain(from_chain)
        to_chain_norm = self.normalize_chain(to_chain)
        
        # Case 1: Same chain - direct trade
        if from_chain_norm == to_chain_norm:
            return ChainRoute(
                from_chain=from_chain_norm,
                to_chain=to_chain_norm,
                intermediate_steps=[{
                    "step": 1,
                    "action": "direct_swap",
                    "from": from_token_address,
                    "to": to_token_address,
                    "chain": from_chain_norm
                }],
                total_steps=1,
                estimated_cost=self.GAS_COSTS.get(from_chain_norm, 5)
            )
        
        # Case 2: Different chains - need intermediate swap
        # Strategy: Swap source token to USDC on source chain
        #          Then use that USDC for target token on target chain
        
        logger.info(f"🔍 Finding route: {from_chain_norm} → {to_chain_norm}")
        
        # Check if we have USDC on target chain
        target_usdc_address = self.USDC_BY_CHAIN.get(to_chain_norm)
        if not target_usdc_address:
            logger.warning(f"⚠️ No USDC configured for {to_chain_norm}")
            return None
        
        # Build multi-step route
        steps = []
        
        # Step 1: Sell source token to USDC on source chain (if not already USDC)
        source_usdc = self.USDC_BY_CHAIN.get(from_chain_norm)
        if from_token_address.lower() != source_usdc.lower() if source_usdc else True:
            steps.append({
                "step": 1,
                "action": "swap_to_usdc",
                "from": from_token_address,
                "to": source_usdc or "USDC",
                "chain": from_chain_norm,
                "note": "Convert to USDC on source chain"
            })
        
        # Step 2: Check if we have USDC on target chain already
        has_target_usdc = self._check_usdc_on_chain(available_holdings, to_chain_norm)
        
        if has_target_usdc:
            # We have USDC on target chain - can buy directly
            steps.append({
                "step": len(steps) + 1,
                "action": "buy_with_usdc",
                "from": target_usdc_address,
                "to": to_token_address,
                "chain": to_chain_norm,
                "note": f"Buy token with existing USDC on {to_chain_norm}"
            })
            
            total_cost = (
                self.GAS_COSTS.get(from_chain_norm, 5) +
                self.GAS_COSTS.get(to_chain_norm, 5)
            )
        else:
            # Need to bridge USDC or find another solution
            bridge_info = self.BRIDGE_ROUTES.get((from_chain_norm, to_chain_norm))
            
            if bridge_info:
                steps.append({
                    "step": len(steps) + 1,
                    "action": "bridge_usdc",
                    "from": source_usdc,
                    "to": target_usdc_address,
                    "from_chain": from_chain_norm,
                    "to_chain": to_chain_norm,
                    "bridge": bridge_info["bridge"],
                    "estimated_time_mins": bridge_info["avg_time_mins"],
                    "note": f"Bridge USDC via {bridge_info['bridge']}"
                })
                
                steps.append({
                    "step": len(steps) + 1,
                    "action": "buy_with_usdc",
                    "from": target_usdc_address,
                    "to": to_token_address,
                    "chain": to_chain_norm,
                    "note": "Buy token after bridge completes"
                })
                
                total_cost = (
                    self.GAS_COSTS.get(from_chain_norm, 5) +
                    self.GAS_COSTS.get(to_chain_norm, 5) +
                    10  # Bridge cost premium
                )
            else:
                # No bridge route - can't complete trade
                logger.warning(f"❌ No bridge route from {from_chain_norm} to {to_chain_norm}")
                return None
        
        route = ChainRoute(
            from_chain=from_chain_norm,
            to_chain=to_chain_norm,
            intermediate_steps=steps,
            total_steps=len(steps),
            estimated_cost=total_cost
        )
        
        logger.info(f"✅ Route found: {route}")
        for step in steps:
            logger.info(f"   Step {step['step']}: {step['action']} - {step.get('note', '')}")
        
        return route
    
    def _check_usdc_on_chain(self, holdings: Dict[str, Dict], target_chain: str) -> bool:
        """Check if we have USDC on the target chain"""
        target_chain_norm = self.normalize_chain(target_chain)
        target_usdc = self.USDC_BY_CHAIN.get(target_chain_norm, "").lower()
        target_bridged = self.BRIDGED_USDC.get(target_chain_norm, "").lower()
        
        for symbol, holding in holdings.items():
            if not holding.get("is_stablecoin", False):
                continue
            
            holding_chain = self.normalize_chain(holding.get("chain", ""))
            if holding_chain != target_chain_norm:
                continue
            
            holding_address = holding.get("tokenAddress", "").lower()
            holding_value = holding.get("value", 0)
            
            # Check if it's USDC on target chain with sufficient balance
            if holding_value >= 10 and (
                holding_address == target_usdc or 
                holding_address == target_bridged
            ):
                return True
        
        return False
    
    def get_usdc_symbol_for_chain(
        self, 
        holdings: Dict[str, Dict], 
        chain: str
    ) -> Optional[Tuple[str, str]]:
        """
        Get the USDC symbol and address for a specific chain
        
        Returns:
            (symbol, address) or None
        """
        chain_norm = self.normalize_chain(chain)
        target_usdc = self.USDC_BY_CHAIN.get(chain_norm, "").lower()
        target_bridged = self.BRIDGED_USDC.get(chain_norm, "").lower()
        
        candidates = []
        
        for symbol, holding in holdings.items():
            if not holding.get("is_stablecoin", False):
                continue
            
            holding_chain = self.normalize_chain(holding.get("chain", ""))
            if holding_chain != chain_norm:
                continue
            
            holding_address = holding.get("tokenAddress", "").lower()
            holding_value = holding.get("value", 0)
            
            if holding_value < 10:
                continue
            
            # Prioritize native USDC over bridged
            is_native = holding_address == target_usdc
            is_bridged = holding_address == target_bridged
            
            if is_native or is_bridged:
                priority = 1 if is_native else 2
                candidates.append((symbol, holding_address, holding_value, priority))
        
        if not candidates:
            return None
        
        # Sort by priority (native first), then by value
        candidates.sort(key=lambda x: (x[3], -x[2]))
        
        return (candidates[0][0], candidates[0][1])
    
    def estimate_route_cost(self, route: ChainRoute) -> Dict:
        """
        Estimate the cost and time for a route
        
        Returns:
            Dict with cost estimates
        """
        total_gas_cost = route.estimated_cost
        
        # Estimate time
        total_time_mins = 0
        for step in route.intermediate_steps:
            if step["action"] == "bridge_usdc":
                total_time_mins += step.get("estimated_time_mins", 10)
            else:
                total_time_mins += 1  # 1 min per swap
        
        # Estimate slippage
        slippage_pct = 0.005 * route.total_steps  # 0.5% per step
        
        return {
            "total_steps": route.total_steps,
            "estimated_time_mins": total_time_mins,
            "relative_gas_cost": total_gas_cost,
            "estimated_slippage_pct": slippage_pct,
            "needs_bridge": any(s["action"] == "bridge_usdc" for s in route.intermediate_steps)
        }
    
    def can_trade_direct(self, from_chain: str, to_chain: str) -> bool:
        """Check if two chains can trade directly (same chain)"""
        return self.normalize_chain(from_chain) == self.normalize_chain(to_chain)
    
    def get_supported_chains(self) -> List[str]:
        """Get list of supported chains"""
        return list(self.USDC_BY_CHAIN.keys())


# Global router instance
chain_router = ChainRouter()