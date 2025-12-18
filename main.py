"""
Main Entry Point - Runs Trading Agent + Health Check API
"""
import asyncio
import uvicorn
from multiprocessing import Process
import signal
import sys

from agent import TradingAgent, main as agent_main
from health_check import app, set_agent
from config import config
from logging_manager import get_logger

logger = get_logger("Main")


def run_health_api():
    """Run health check API in separate process"""
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="warning"  # Reduce noise
    )


async def run_agent_with_health():
    """Run agent and register with health check"""
    agent = TradingAgent()
    
    # Register agent with health check API
    set_agent(agent)
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("📡 Received shutdown signal")
        asyncio.create_task(agent.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass  # Windows doesn't support this
    
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt")
    finally:
        if agent._running:
            await agent.shutdown()


def main():
    """
    Main entry point
    Runs health API in background, agent in foreground
    """
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║       🔥 ELITE HYBRID TRADING AGENT v4.1 🔥                  ║
    ║                                                              ║
    ║   Code (muscle) + LLM Brain (intelligence) = Elite Performance  ║
    ║                                                              ║
    ║   Health API: http://localhost:8000/health                  ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    # Validate configuration
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"❌ Configuration error: {error}")
        sys.exit(1)
    
    # Start health check API in background
    health_process = Process(target=run_health_api, daemon=True)
    health_process.start()
    logger.info("🏥 Health check API started on port 8000")
    
    try:
        # Run agent in main process
        asyncio.run(run_agent_with_health())
    except Exception as e:
        logger.critical(f"💀 Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if health_process.is_alive():
            health_process.terminate()
            health_process.join(timeout=5)


if __name__ == "__main__":
    main()