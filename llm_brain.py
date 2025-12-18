"""
LLM Trading Brain - FIXED: Better ranking with fallback
✅ Returns all tokens even if LLM fails to rank some
✅ Uses algorithmic scores as fallback
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
    Enhanced with better fallback logic
    """
    
    def __init__(self):
        self.enabled = config.ENABLE_LLM_BRAIN
        self.api_key = config.LLM_API_KEY
        self.base_url = config.LLM_BASE_URL.rstrip('/')
        self.model = config.LLM_MODEL
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, any] = {}
        
        # Circuit breaker for LLM failures
        self._failure_count = 0
        self._max_failures = 3
        self._circuit_open = False
        self._last_failure_time: Optional[datetime] = None
        
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
    
    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker should allow request"""
        if not self._circuit_open:
            return True
        
        # Check if we should try to reset
        if self._last_failure_time:
            elapsed = (datetime.now() - self._last_failure_time).total_seconds()
            if elapsed > 300:  # 5 minutes
                logger.info("🔄 LLM circuit breaker attempting reset")
                self._circuit_open = False
                self._failure_count = 0
                return True
        
        return False
    
    def _on_llm_failure(self):
        """Record LLM failure"""
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        
        if self._failure_count >= self._max_failures:
            self._circuit_open = True
            logger.warning(
                f"⚠️ LLM circuit breaker OPEN after {self._failure_count} failures. "
                "Falling back to algorithmic mode."
            )
    
    def _on_llm_success(self):
        """Record LLM success"""
        self._failure_count = 0
        if self._circuit_open:
            logger.info("✅ LLM circuit breaker CLOSED - service recovered")
            self._circuit_open = False
    
    def _extract_json(self, text: str) -> str:
        """Extract JSON from LLM response"""
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
    
    async def _call_llm(self, prompt: str, system: str = None, retry_count: int = 2) -> str:
        """Call LLM API with retry logic"""
        if not self.enabled or not self._check_circuit_breaker():
            return ""
        
        for attempt in range(retry_count):
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
                        
                        if attempt < retry_count - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        
                        self._on_llm_failure()
                        return ""
                    
                    data = await resp.json()
                    result = data["choices"][0]["message"]["content"].strip()
                    
                    self._on_llm_success()
                    return result
                    
            except asyncio.TimeoutError:
                logger.warning(f"⏰ LLM timeout (attempt {attempt + 1}/{retry_count})")
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self._on_llm_failure()
                    return ""
                    
            except Exception as e:
                logger.error(f"❌ LLM call failed (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self._on_llm_failure()
                    return ""
        
        return ""
    
    async def rank_tokens(
        self, 
        tokens: List[DiscoveredToken],
        market_snapshot: MarketSnapshot
    ) -> List[Tuple[DiscoveredToken, float, str]]:
        """
        🧠 BRAIN FUNCTION: Rank discovered tokens by trading potential
        ✅ FIXED: Returns ALL tokens with fallback to algo scores
        """
        if not self.enabled or not tokens or self._circuit_open:
            logger.info("🤖 Using algorithmic scores (LLM disabled/unavailable)")
            return [(t, t.opportunity_score, "Algorithmic scoring") for t in tokens]
        
        logger.info(f"🧠 LLM ranking {len(tokens)} tokens...")
        
        # Limit to top 15 to save tokens
        top_tokens = tokens[:15]
        
        # Create symbol lookup for matching
        token_lookup = {t.symbol.upper(): t for t in top_tokens}
        
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

For EACH token above, provide a score (0-10) and brief reason.

CRITICAL REQUIREMENTS:
1. Respond ONLY with valid JSON. No other text.
2. Include ALL {len(top_tokens)} tokens in your response
3. Use exact symbol names as shown above

Format:
{{
  "rankings": [
    {{"symbol": "EXACT_SYMBOL_FROM_ABOVE", "score": 8.5, "reason": "Brief reason"}},
    ...
  ]
}}"""

        system = "You are a crypto trader. Respond ONLY with valid JSON containing ALL tokens. No preamble, no markdown, just JSON."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            logger.warning("⚠️ LLM ranking failed, using algo scores")
            return [(t, t.opportunity_score, "LLM failed - algo fallback") for t in top_tokens]
        
        try:
            # Extract JSON
            json_text = self._extract_json(response)
            
            if not json_text:
                logger.warning("⚠️ No JSON found in LLM response")
                logger.debug(f"Response: {response[:300]}")
                return [(t, t.opportunity_score, "No JSON - algo fallback") for t in top_tokens]
            
            # Parse JSON
            data = json.loads(json_text)
            rankings = data.get("rankings", [])
            
            if not rankings:
                logger.warning("⚠️ Empty rankings from LLM")
                return [(t, t.opportunity_score, "Empty rankings - algo fallback") for t in top_tokens]
            
            # ✅ NEW: Create results dict for all tokens with LLM scores
            llm_scored = {}
            matched_count = 0
            
            for ranking in rankings:
                symbol = ranking.get("symbol", "").upper().strip()
                llm_score = float(ranking.get("score", 0))
                reason = ranking.get("reason", "No reason")
                
                # Try exact match first
                if symbol in token_lookup:
                    token = token_lookup[symbol]
                    llm_scored[symbol] = (token, llm_score, reason)
                    matched_count += 1
                else:
                    # Try partial match (e.g., "BEMU" matches "BEMU")
                    for token_sym, token in token_lookup.items():
                        if symbol in token_sym or token_sym in symbol:
                            llm_scored[token_sym] = (token, llm_score, reason)
                            matched_count += 1
                            break
            
            logger.info(f"🧠 LLM matched {matched_count}/{len(top_tokens)} tokens")
            
            # ✅ NEW: Build final results - LLM scored tokens first, then unranked with algo scores
            results = []
            
            # Add LLM-scored tokens (sorted by LLM score)
            llm_results = list(llm_scored.values())
            llm_results.sort(key=lambda x: x[1], reverse=True)
            results.extend(llm_results)
            
            # Add unranked tokens with their algo scores
            unranked = [t for t in top_tokens if t.symbol.upper() not in llm_scored]
            if unranked:
                logger.info(f"📊 Adding {len(unranked)} unranked tokens with algo scores")
                for token in unranked:
                    results.append((token, token.opportunity_score, "Algo score (LLM didn't rank)"))
            
            # Final sort by score (mix of LLM and algo)
            results.sort(key=lambda x: x[1], reverse=True)
            
            logger.info(f"✅ Returning {len(results)} total tokens ({matched_count} LLM-ranked, {len(unranked)} algo-fallback)")
            
            return results
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse error: {e}")
            logger.debug(f"JSON text: {json_text[:300] if 'json_text' in locals() else 'N/A'}")
            return [(t, t.opportunity_score, "Parse error - algo fallback") for t in top_tokens]
        except Exception as e:
            logger.error(f"❌ LLM ranking error: {e}")
            return [(t, t.opportunity_score, "Error - algo fallback") for t in top_tokens]
    
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
        if not self.enabled or self._circuit_open:
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
            "circuit_open": self._circuit_open,
            "failure_count": self._failure_count,
            "cache_size": len(self._cache)
        }