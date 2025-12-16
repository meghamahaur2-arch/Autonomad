"""
LLM Trading Brain - FIXED JSON parsing
"""
import asyncio
import json
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import aiohttp
from aiohttp import ClientTimeout

from config import config
from models import DiscoveredToken, MarketSnapshot, Conviction, Position
from logging_manager import get_logger

logger = get_logger("LLMBrain")


class LLMBrain:
    """
    Elite LLM Brain for trading decisions
    """
    
    def __init__(self):
        self.enabled = config.ENABLE_LLM_BRAIN
        self.api_key = config.LLM_API_KEY
        self.base_url = config.LLM_BASE_URL.rstrip('/')
        self.model = config.LLM_MODEL
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, any] = {}
        
        if self.enabled:
            if not self.api_key or not self.base_url:
                logger.warning("⚠️ LLM enabled but credentials missing - disabling")
                self.enabled = False
            else:
                logger.info(f"🧠 LLM Brain initialized (model: {self.model})")
        else:
            logger.info("🤖 Running in pure algorithmic mode (LLM disabled)")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=config.LLM_TIMEOUT)
            )
        return self._session
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _extract_json(self, text: str) -> str:
        """Extract JSON from LLM response - IMPROVED"""
        if not text:
            return ""
        
        text = text.strip()
        
        # Method 1: Try markdown code blocks
        if "```json" in text:
            match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
            if match:
                return match.group(1).strip()
        elif "```" in text:
            match = re.search(r'```\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
            if match:
                return match.group(1).strip()
        
        # Method 2: Find JSON object/array
        # Try to find { ... } first
        brace_start = text.find('{')
        if brace_start != -1:
            brace_count = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return text[brace_start:i+1].strip()
        
        # Try to find [ ... ]
        bracket_start = text.find('[')
        if bracket_start != -1:
            bracket_count = 0
            for i in range(bracket_start, len(text)):
                if text[i] == '[':
                    bracket_count += 1
                elif text[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        return text[bracket_start:i+1].strip()
        
        return text.strip()
    
    async def _call_llm(self, prompt: str, system: str = None) -> str:
        """Call LLM API"""
        if not self.enabled:
            return ""
        
        try:
            session = await self._get_session()
            
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": config.LLM_TEMPERATURE,
                "max_tokens": config.LLM_MAX_TOKENS
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"❌ LLM API error ({resp.status}): {error[:200]}")
                    return ""
                
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
                
        except Exception as e:
            logger.error(f"❌ LLM call failed: {e}")
            return ""
    
    async def rank_tokens(
        self, 
        tokens: List[DiscoveredToken],
        market_snapshot: MarketSnapshot
    ) -> List[Tuple[DiscoveredToken, float, str]]:
        """
        🧠 BRAIN FUNCTION: Rank discovered tokens by trading potential
        """
        if not self.enabled or not tokens:
            return [(t, t.opportunity_score, "Algorithmic scoring") for t in tokens]
        
        logger.info(f"🧠 LLM ranking {len(tokens)} tokens...")
        
        # Limit to top 15 to save tokens
        top_tokens = tokens[:15]
        
        token_summaries = []
        for i, token in enumerate(top_tokens, 1):
            market_cap_str = f"${token.market_cap:,.0f}" if token.market_cap else "Unknown"
            
            token_summaries.append(
                f"{i}. {token.symbol} on {token.chain}\n"
                f"   Price: ${token.price:.6f} | Change: {token.change_24h_pct:+.2f}%\n"
                f"   Volume: ${token.volume_24h:,.0f} | Liquidity: ${token.liquidity_usd:,.0f}\n"
                f"   Market Cap: {market_cap_str} | Algo Score: {token.opportunity_score:.1f}"
            )
        
        prompt = f"""Analyze these crypto tokens and rank them for SHORT-TERM trading (1-12 hours).

MARKET CONTEXT:
- Regime: {market_snapshot.regime.value}
- Avg 24h Change: {market_snapshot.avg_change_24h:+.2f}%
- Volatility: {market_snapshot.volatility:.2f}%

TOKENS:
{chr(10).join(token_summaries)}

For EACH token, provide a score (0-10) and brief reason.

CRITICAL: Respond ONLY with valid JSON. No other text. Format:
{{
  "rankings": [
    {{"symbol": "ARB", "score": 8.5, "reason": "Strong momentum"}},
    {{"symbol": "OP", "score": 7.0, "reason": "Moderate volume"}}
  ]
}}"""

        system = "You are a crypto trader. Respond ONLY with valid JSON. No preamble, no markdown, just JSON."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            logger.warning("⚠️ LLM ranking failed, using algo scores")
            return [(t, t.opportunity_score, "LLM failed") for t in top_tokens]
        
        try:
            # Extract JSON
            json_text = self._extract_json(response)
            
            if not json_text:
                logger.warning("⚠️ No JSON found in LLM response")
                logger.debug(f"Response: {response[:300]}")
                return [(t, t.opportunity_score, "No JSON") for t in top_tokens]
            
            # Parse JSON
            data = json.loads(json_text)
            rankings = data.get("rankings", [])
            
            if not rankings:
                logger.warning("⚠️ Empty rankings from LLM")
                return [(t, t.opportunity_score, "Empty rankings") for t in top_tokens]
            
            # Match back to tokens
            results = []
            for ranking in rankings:
                symbol = ranking.get("symbol", "").upper()
                llm_score = float(ranking.get("score", 0))
                reason = ranking.get("reason", "No reason")
                
                # Find matching token
                for token in top_tokens:
                    if token.symbol.upper() == symbol:
                        results.append((token, llm_score, reason))
                        break
            
            if not results:
                logger.warning("⚠️ No tokens matched from LLM rankings")
                return [(t, t.opportunity_score, "No matches") for t in top_tokens]
            
            logger.info(f"🧠 LLM ranked {len(results)} tokens successfully")
            
            # Sort by LLM score
            results.sort(key=lambda x: x[1], reverse=True)
            
            return results
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse error: {e}")
            logger.debug(f"JSON text: {json_text[:300] if 'json_text' in locals() else 'N/A'}")
            return [(t, t.opportunity_score, "Parse error") for t in top_tokens]
        except Exception as e:
            logger.error(f"❌ LLM ranking error: {e}")
            return [(t, t.opportunity_score, "Error") for t in top_tokens]
    
    async def confirm_trade(
        self,
        token: DiscoveredToken,
        signal_type: str,
        conviction: Conviction,
        market_snapshot: MarketSnapshot,
        portfolio_stats: Dict
    ) -> Tuple[bool, str, Conviction]:
        """
        🧠 BRAIN FUNCTION: Confirm if this trade makes sense
        """
        if not self.enabled:
            return (True, "Algorithmic decision", conviction)
        
        logger.info(f"🧠 LLM confirming trade: {token.symbol} ({signal_type})")
        
        market_cap_str = f"${token.market_cap:,.0f}" if token.market_cap else "Unknown"
        
        prompt = f"""Should we take this crypto trade?

TOKEN: {token.symbol} on {token.chain}
Price: ${token.price:.6f} | 24h: {token.change_24h_pct:+.2f}%
Volume: ${token.volume_24h:,.0f} | Liquidity: ${token.liquidity_usd:,.0f}
Market Cap: {market_cap_str}

SIGNAL: {signal_type} (Conviction: {conviction.value})
MARKET: {market_snapshot.regime.value} | Volatility: {market_snapshot.volatility:.2f}%

PORTFOLIO:
Positions: {portfolio_stats.get('positions', 0)}/5
Win Rate: {portfolio_stats.get('win_rate', 0)*100:.0f}%
Consecutive Losses: {portfolio_stats.get('consecutive_losses', 0)}

CRITICAL: Respond ONLY with valid JSON:
{{
  "approved": true,
  "reasoning": "One sentence",
  "conviction": "HIGH"
}}"""

        system = "You are a risk manager. Respond ONLY with valid JSON. No markdown."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            return (True, "LLM unavailable", conviction)
        
        try:
            json_text = self._extract_json(response)
            
            if not json_text:
                return (True, "No JSON", conviction)
            
            data = json.loads(json_text)
            approved = data.get("approved", True)
            reasoning = data.get("reasoning", "LLM confirmed")
            new_conviction_str = data.get("conviction", conviction.value).upper()
            
            try:
                new_conviction = Conviction[new_conviction_str]
            except (KeyError, AttributeError):
                new_conviction = conviction
            
            if approved:
                logger.info(f"✅ LLM approved: {reasoning}")
            else:
                logger.warning(f"❌ LLM rejected: {reasoning}")
            
            return (approved, reasoning, new_conviction)
            
        except Exception as e:
            logger.error(f"❌ LLM confirm error: {e}")
            return (True, "Error, approved anyway", conviction)
    
    def get_stats(self) -> Dict:
        """Get LLM usage statistics"""
        return {
            "enabled": self.enabled,
            "model": self.model,
            "cache_size": len(self._cache)
        }