"""
Health Check API for Railway Monitoring
Simple FastAPI endpoint to monitor agent status
"""
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from typing import Optional
import asyncio

app = FastAPI(title="Trading Agent Health Check")

# Global reference to agent (set by main.py)
_agent: Optional[object] = None


def set_agent(agent):
    """Set the agent instance for health checks"""
    global _agent
    _agent = agent


@app.get("/")
async def root():
    """Basic health check"""
    return {"status": "ok", "service": "trading-agent", "version": "4.1"}


@app.get("/health")
async def health_check():
    """
    Comprehensive health check for Railway
    Returns 200 if healthy, 503 if unhealthy
    """
    if _agent is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "reason": "Agent not initialized"}
        )
    
    try:
        health_status = _agent.get_health_status()
        
        # Check if agent is running
        if not health_status.get("running", False):
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "reason": "Agent not running",
                    "details": health_status
                }
            )
        
        # Check for excessive errors
        consecutive_errors = health_status.get("consecutive_errors", 0)
        if consecutive_errors >= 3:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "reason": f"{consecutive_errors} consecutive errors",
                    "details": health_status
                }
            )
        
        # Check last successful cycle
        last_cycle = health_status.get("last_successful_cycle")
        if last_cycle:
            last_cycle_time = datetime.fromisoformat(last_cycle)
            elapsed = (datetime.now(timezone.utc) - last_cycle_time).total_seconds()
            
            # If no successful cycle in 30 minutes, degraded
            if elapsed > 1800:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "degraded",
                        "reason": f"No successful cycle in {elapsed/60:.0f} minutes",
                        "details": health_status
                    }
                )
        
        # Healthy
        return {
            "status": "healthy",
            "details": health_status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "reason": str(e)
            }
        )


@app.get("/metrics")
async def metrics():
    """Get trading metrics"""
    if _agent is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Agent not initialized"}
        )
    
    try:
        metrics = _agent.metrics.to_dict()
        return {
            "metrics": metrics,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/portfolio")
async def portfolio_summary():
    """Get portfolio summary"""
    if _agent is None or not _agent.competition_id:
        return JSONResponse(
            status_code=503,
            content={"error": "Agent not initialized"}
        )
    
    try:
        portfolio = await _agent.portfolio_manager.get_portfolio_state(
            _agent.competition_id
        )
        
        # Simplify for API response
        summary = {
            "total_value": portfolio.get("total_value", 0),
            "positions_count": len(portfolio.get("positions", [])),
            "holdings": {
                symbol: {
                    "value": h["value"],
                    "pct": h["pct"]
                }
                for symbol, h in portfolio.get("holdings", {}).items()
            },
            "timestamp": portfolio.get("timestamp")
        }
        
        return summary
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/positions")
async def positions():
    """Get current positions"""
    if _agent is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Agent not initialized"}
        )
    
    try:
        tracked = _agent.portfolio_manager.tracked_positions
        
        positions_data = []
        for symbol, pos in tracked.items():
            positions_data.append({
                "symbol": symbol,
                "entry_price": pos.entry_price,
                "entry_amount": pos.entry_amount,
                "entry_value_usd": pos.entry_value_usd,
                "highest_price": pos.highest_price,
                "chain": pos.chain,
                "entry_timestamp": pos.entry_timestamp
            })
        
        return {
            "positions": positions_data,
            "count": len(positions_data),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/logs")
async def logs(last_n: int = 50):
    """Get recent logs"""
    from logging_manager import log_manager
    
    try:
        logs = log_manager.get_logs(last_n)
        return {
            "logs": [
                {
                    "timestamp": log["timestamp"],
                    "level": log["level"],
                    "message": log["message"]
                }
                for log in logs
            ],
            "count": len(logs)
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/shutdown")
async def shutdown():
    """
    Trigger graceful shutdown (protected endpoint)
    Use with caution
    """
    if _agent is None:
        return {"error": "Agent not initialized"}
    
    try:
        asyncio.create_task(_agent.shutdown())
        return {
            "status": "shutdown_initiated",
            "message": "Agent is shutting down gracefully"
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)