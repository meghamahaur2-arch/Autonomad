"""
LLM Trading Brain - The "Vibes Check" Layer
Provides high-level reasoning, market regime detection, and trade confirmation
"""
import asyncio
import json
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
    
    Responsibilities:
    - Token ranking (which tokens are worth trading?)
    - Signal confirmation (is this a good setup?)
    - Regime detection (trend vs chop vs crash)
    - Trade rejection (vibes check)
    - Reason generation (why did we do this?)
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
        """Extract JSON from LLM response (handles markdown code blocks)"""
        if not text:
            return ""
        
        # Remove markdown code blocks
        text = text.strip()
        
        # Try to find JSON between ```json and ```
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end != -1:
                text = text[start:end].strip()
        elif "```" in text:
            # Generic code block
            start = text.find("```") + 3
            end = text.find("```", start)
            if end != -1:
                text = text[start:end].strip()
        
        # Find JSON object/array boundaries
        if not text.startswith('{') and not text.startswith('['):
            # Try to find first { or [
            json_start = min(
                (text.find('{') if '{' in text else len(text)),
                (text.find('[') if '[' in text else len(text))
            )
            if json_start < len(text):
                text = text[json_start:]
        
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
        
        Returns: List of (token, llm_score, reasoning)
        """
        if not self.enabled or not tokens:
            # Fallback: use algorithmic scores
            return [(t, t.opportunity_score, "Algorithmic scoring") for t in tokens]
        
        logger.info(f"🧠 LLM ranking {len(tokens)} tokens...")
        
        # Build context
        top_tokens = tokens[:10]  # Only rank top 10 to save tokens
        
        token_summaries = []
        for i, token in enumerate(top_tokens, 1):
            # ✅ FIXED: Format market_cap outside f-string
            market_cap_str = f"${token.market_cap:,.0f}" if token.market_cap else "Unknown"
            
            token_summaries.append(
                f"{i}. {token.symbol} on {token.chain}\n"
                f"   Price: ${token.price:.6f} | Change: {token.change_24h_pct:+.2f}%\n"
                f"   Volume: ${token.volume_24h:,.0f} | Liquidity: ${token.liquidity_usd:,.0f}\n"
                f"   Market Cap: {market_cap_str}\n"
                f"   Algo Score: {token.opportunity_score:.1f}"
            )
        
        prompt = f"""You are an elite crypto trader. Analyze these tokens and rank them by SHORT-TERM trading potential (1-12 hours).

MARKET CONTEXT:
- Regime: {market_snapshot.regime.value}
- Avg 24h Change: {market_snapshot.avg_change_24h:+.2f}%
- Volatility: {market_snapshot.volatility:.2f}%
- Bullish/Bearish: {market_snapshot.bullish_count}/{market_snapshot.bearish_count}

TOKENS TO RANK:
{chr(10).join(token_summaries)}

For each token, provide:
1. Score (0-10): Trading potential
2. Reasoning (one line): Why this score?

Consider:
- Is the price action sustainable or just a pump?
- Is the volume real or wash trading?
- Does the market cap make sense?
- Is this regime good for this token?
- Are there red flags?

Respond in JSON format:
{{
  "rankings": [
    {{"symbol": "SYMBOL", "score": 8.5, "reason": "Strong momentum with healthy volume"}},
    ...
  ]
}}"""

        system = "You are a professional crypto trader with 10 years experience. Be skeptical, concise, and focus on SHORT-TERM trades."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            logger.warning("⚠️ LLM ranking failed, using algorithmic scores")
            return [(t, t.opportunity_score, "Algorithmic (LLM failed)") for t in top_tokens]
        
        try:
            # Parse JSON response
            json_text = self._extract_json(response)
            
            if not json_text:
                logger.warning("⚠️ No JSON found in LLM response")
                logger.debug(f"Response was: {response[:500]}")
                return [(t, t.opportunity_score, "Algorithmic (no JSON)") for t in top_tokens]
            
            data = json.loads(json_text)
            rankings = data.get("rankings", [])
            
            # Match back to tokens
            results = []
            for ranking in rankings:
                symbol = ranking.get("symbol", "")
                llm_score = float(ranking.get("score", 0))
                reason = ranking.get("reason", "No reason")
                
                # Find matching token
                for token in top_tokens:
                    if token.symbol == symbol:
                        results.append((token, llm_score, reason))
                        break
            
            logger.info(f"🧠 LLM ranked {len(results)} tokens")
            
            # Sort by LLM score
            results.sort(key=lambda x: x[1], reverse=True)
            
            return results
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse LLM rankings: {e}")
            logger.debug(f"JSON text was: {json_text[:500] if 'json_text' in locals() else 'N/A'}")
            logger.debug(f"Full response: {response[:500]}")
            return [(t, t.opportunity_score, "Algorithmic (parse failed)") for t in top_tokens]
        except Exception as e:
            logger.error(f"❌ Failed to parse LLM rankings: {e}")
            logger.debug(f"Response was: {response[:500]}")
            return [(t, t.opportunity_score, "Algorithmic (parse failed)") for t in top_tokens]
    
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
        
        Returns: (approved, reasoning, adjusted_conviction)
        """
        if not self.enabled:
            return (True, "Algorithmic decision", conviction)
        
        logger.info(f"🧠 LLM confirming trade: {token.symbol} ({signal_type})")
        
        # ✅ FIXED: Format market_cap outside f-string
        market_cap_str = f"${token.market_cap:,.0f}" if token.market_cap else "Unknown"
        
        prompt = f"""You are reviewing a trade before execution. Should we take this trade?

TOKEN:
- Symbol: {token.symbol} on {token.chain}
- Price: ${token.price:.6f}
- 24h Change: {token.change_24h_pct:+.2f}%
- Volume: ${token.volume_24h:,.0f}
- Liquidity: ${token.liquidity_usd:,.0f}
- Market Cap: {market_cap_str}

SIGNAL:
- Type: {signal_type}
- Conviction: {conviction.value}

MARKET:
- Regime: {market_snapshot.regime.value}
- Volatility: {market_snapshot.volatility:.2f}%

PORTFOLIO:
- Open Positions: {portfolio_stats.get('positions', 0)}/5
- Deployed Capital: {portfolio_stats.get('deployed_pct', 0)*100:.0f}%
- Win Rate: {portfolio_stats.get('win_rate', 0)*100:.0f}%
- Consecutive Losses: {portfolio_stats.get('consecutive_losses', 0)}

DECISION:
Should we take this trade? Consider:
- Is this signal reliable in current market regime?
- Are we overexposed?
- Is the token legitimate?
- Any red flags?

Respond in JSON:
{{
  "approved": true/false,
  "reasoning": "One line explanation",
  "conviction": "HIGH/MEDIUM/LOW"
}}"""

        system = "You are a risk manager for a trading desk. Be conservative. Reject suspicious trades."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            logger.warning("⚠️ LLM confirmation failed, approving with original conviction")
            return (True, "LLM unavailable, algo approved", conviction)
        
        try:
            json_text = self._extract_json(response)
            
            if not json_text:
                logger.warning("⚠️ No JSON found in LLM confirmation")
                return (True, "Parse failed, algo approved", conviction)
            
            data = json.loads(json_text)
            approved = data.get("approved", True)
            reasoning = data.get("reasoning", "LLM confirmed")
            new_conviction_str = data.get("conviction", conviction.value)
            
            # Parse conviction
            try:
                new_conviction = Conviction[new_conviction_str.upper()]
            except (KeyError, AttributeError):
                new_conviction = conviction
            
            if approved:
                logger.info(f"✅ LLM approved: {reasoning}")
            else:
                logger.warning(f"❌ LLM rejected: {reasoning}")
            
            return (approved, reasoning, new_conviction)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse LLM confirmation: {e}")
            logger.debug(f"JSON text was: {json_text[:500] if 'json_text' in locals() else 'N/A'}")
            logger.debug(f"Full response: {response[:500]}")
            return (True, "Parse failed, algo approved", conviction)
        except Exception as e:
            logger.error(f"❌ Failed to parse LLM confirmation: {e}")
            logger.debug(f"Response was: {response[:500]}")
            return (True, "Parse failed, algo approved", conviction)
    
    async def detect_market_regime(
        self,
        market_snapshot: MarketSnapshot,
        recent_trades: List[Dict]
    ) -> Tuple[str, str]:
        """
        🧠 BRAIN FUNCTION: Understand market conditions
        
        Returns: (regime_description, trading_advice)
        """
        if not self.enabled:
            return (market_snapshot.regime.value, "Trade normally")
        
        # Build context from recent trades
        trade_summary = "No recent trades"
        if recent_trades:
            wins = sum(1 for t in recent_trades if t.get("pnl_pct", 0) > 0)
            losses = len(recent_trades) - wins
            avg_pnl = sum(t.get("pnl_pct", 0) for t in recent_trades) / len(recent_trades)
            trade_summary = f"{wins}W/{losses}L, Avg PnL: {avg_pnl:+.2f}%"
        
        prompt = f"""Analyze current market conditions and give trading advice.

MARKET DATA:
- Regime (algo): {market_snapshot.regime.value}
- Avg 24h Change: {market_snapshot.avg_change_24h:+.2f}%
- Volatility: {market_snapshot.volatility:.2f}%
- Bullish Tokens: {market_snapshot.bullish_count}
- Bearish Tokens: {market_snapshot.bearish_count}

RECENT PERFORMANCE:
- {trade_summary}

Questions:
1. What type of market are we in? (trending up/down, choppy, volatile, crash)
2. What's the best strategy right now?
3. Should we be more aggressive or defensive?

Respond in JSON:
{{
  "regime": "One word description",
  "advice": "One sentence trading advice"
}}"""

        system = "You are a veteran trader who's seen every market cycle. Be concise and actionable."
        
        response = await self._call_llm(prompt, system)
        
        if not response:
            return (market_snapshot.regime.value, "LLM unavailable")
        
        try:
            json_text = self._extract_json(response)
            
            if not json_text:
                return (market_snapshot.regime.value, "LLM unavailable")
            
            data = json.loads(json_text)
            regime = data.get("regime", market_snapshot.regime.value)
            advice = data.get("advice", "Trade normally")
            
            logger.info(f"🧠 Market Regime: {regime}")
            logger.info(f"📊 Trading Advice: {advice}")
            
            return (regime, advice)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse regime detection: {e}")
            logger.debug(f"JSON text was: {json_text[:500] if 'json_text' in locals() else 'N/A'}")
            return (market_snapshot.regime.value, "Parse failed")
        except Exception as e:
            logger.error(f"❌ Failed to parse regime detection: {e}")
            return (market_snapshot.regime.value, "Parse failed")
    
    async def explain_trade_result(
        self,
        token_symbol: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        hold_hours: float,
        exit_reason: str
    ) -> str:
        """
        🧠 BRAIN FUNCTION: Explain what happened (for learning)
        
        Returns: Human-readable explanation
        """
        if not self.enabled:
            return f"Exited {exit_reason}: {pnl_pct:+.2f}%"
        
        prompt = f"""Explain this trade result in one sentence.

TRADE:
- Token: {token_symbol}
- Entry: ${entry_price:.6f}
- Exit: ${exit_price:.6f}
- P&L: {pnl_pct:+.2f}%
- Hold Time: {hold_hours:.1f} hours
- Exit Reason: {exit_reason}

Explain what happened and what we learned. Be concise (20 words max)."""

        system = "You are a trading journal. Write clear, educational summaries."
        
        response = await self._call_llm(prompt, system)
        
        return response if response else f"{exit_reason}: {pnl_pct:+.2f}%"
    
    def get_stats(self) -> Dict:
        """Get LLM usage statistics"""
        return {
            "enabled": self.enabled,
            "model": self.model,
            "cache_size": len(self._cache)
        }