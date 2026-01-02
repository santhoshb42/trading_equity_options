#!/usr/bin/env python3
"""
Webhook Router for TradingView Alerts
Routes incoming alerts from TradingView (port 80) to both equity and options bots
"""

import os
import json
import logging
import time
import threading
from flask import Flask, request, jsonify
from typing import Dict, Any
import requests
from datetime import datetime
from pathlib import Path

# Setup logging - use shared logs directory
log_dir = Path("/root/santhosh/trading/equity/logs")  # Store router logs with equity
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / f"webhook_router_{datetime.now().strftime('%Y-%m-%d')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
ROUTER_PORT = int(os.getenv("ROUTER_PORT", "80"))
ROUTER_HOST = os.getenv("ROUTER_HOST", "0.0.0.0")

# Downstream bot endpoints
EQUITY_BOT_URL = os.getenv("EQUITY_BOT_URL", "http://127.0.0.1:8080/webhook")
OPTIONS_BOT_URL = os.getenv("OPTIONS_BOT_URL", "http://127.0.0.1:8081/webhook/options")

# Optional authentication
ROUTER_SECRET = os.getenv("ROUTER_SECRET", "")

# Track stats
STATS = {
    "total_alerts_received": 0,
    "equity_forwarded": 0,
    "options_forwarded": 0,
    "forward_failures": 0,
    "last_alert_time": None,
    "last_symbols": []
}


def validate_webhook_secret(received_secret: str = None) -> bool:
    """Validate webhook secret if configured"""
    if not ROUTER_SECRET:
        return True
    
    if not received_secret:
        logger.warning("Webhook secret required but not provided")
        return False
    
    if received_secret != ROUTER_SECRET:
        logger.warning(f"Invalid webhook secret provided")
        return False
    
    return True


def forward_alert(url: str, payload: Dict[str, Any], bot_name: str, retries: int = 1) -> bool:
    """Forward alert to bot endpoint with retry logic"""
    for attempt in range(retries):
        try:
            # Increased timeout to 30s for slow webhook processing
            response = requests.post(
                url,
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"✓ Alert forwarded to {bot_name}: {response.status_code}")
                return True
            else:
                logger.error(f"✗ {bot_name} returned status {response.status_code}: {response.text}")
                return False
                
        except requests.exceptions.ConnectTimeout:
            if attempt < retries - 1:
                logger.warning(f"⏳ {bot_name} timeout (attempt {attempt + 1}/{retries}), retrying...")
                time.sleep(2)
            else:
                logger.error(f"✗ {bot_name} connection timeout after {retries} attempts - bot may be down")
                return False
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                logger.warning(f"⏳ {bot_name} read timeout (attempt {attempt + 1}/{retries}), retrying...")
                time.sleep(2)
            else:
                logger.error(f"✗ {bot_name} read timeout after {retries} attempts")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"✗ Error forwarding to {bot_name}: {str(e)}")
            return False
    
    return False


@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Main webhook endpoint - receives alerts from TradingView"""
    try:
        # Get secret from query param or header
        secret = request.args.get('secret') or request.headers.get('X-Webhook-Secret')
        
        if not validate_webhook_secret(secret):
            logger.warning("Request rejected: invalid or missing secret")
            return jsonify({"error": "Unauthorized"}), 401
        
        # Parse payload - handle both JSON and form-data from TradingView
        raw_payload = {}
        
        # Try JSON first
        if request.is_json:
            raw_payload = request.get_json() or {}
        # TradingView sometimes sends as form-data
        elif request.form:
            # Get the alert message from form data
            alert_message = request.form.get('message', '{}')
            try:
                raw_payload = json.loads(alert_message) if alert_message else {}
            except json.JSONDecodeError:
                logger.warning(f"Could not parse alert message as JSON: {alert_message}")
                raw_payload = {}
        # Try raw data as JSON
        else:
            try:
                raw_data = request.get_data(as_text=True)
                if raw_data:
                    raw_payload = json.loads(raw_data) or {}
            except:
                logger.warning(f"Could not parse request data as JSON")
                raw_payload = {}
        
        # Extract from TradingView Alerts wrapper if present
        payload = raw_payload
        if "Alerts" in raw_payload and isinstance(raw_payload["Alerts"], list) and len(raw_payload["Alerts"]) > 0:
            payload = raw_payload["Alerts"][0]
            logger.info(f"ℹ️ Extracted alert from TradingView Alerts wrapper")
        elif "alerts" in raw_payload and isinstance(raw_payload["alerts"], list) and len(raw_payload["alerts"]) > 0:
            payload = raw_payload["alerts"][0]
            logger.info(f"ℹ️ Extracted alert from lowercase alerts wrapper")
        
        # Validate required fields
        if not payload.get('symbol'):
            logger.warning(f"Alert missing symbol field: {payload}")
            return jsonify({"error": "Missing symbol field"}), 400
        
        # Update stats
        STATS["total_alerts_received"] += 1
        STATS["last_alert_time"] = datetime.now().isoformat()
        
        symbol = payload.get('symbol')
        if symbol not in STATS["last_symbols"]:
            STATS["last_symbols"].append(symbol)
            if len(STATS["last_symbols"]) > 10:
                STATS["last_symbols"].pop(0)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"📨 WEBHOOK ALERT RECEIVED (#{STATS['total_alerts_received']})")
        logger.info(f"{'='*70}")
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Action: {payload.get('action', 'N/A')}")
        logger.info(f"Price: {payload.get('price', 'N/A')}")
        logger.info(f"Timestamp: {datetime.now().isoformat()}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        logger.info(f"{'='*70}\n")
        
        # Forward to both bots IN PARALLEL (not sequentially) for burst handling
        logger.info(f"🔄 Forwarding alert to both bots in PARALLEL...")
        
        # Launch forwarding to both bots in separate threads
        equity_result = {'success': False}
        options_result = {'success': False}
        
        def forward_equity():
            equity_result['success'] = forward_alert(EQUITY_BOT_URL, payload, "EQUITY BOT")
        
        def forward_options():
            options_result['success'] = forward_alert(OPTIONS_BOT_URL, payload, "OPTIONS BOT")
        
        equity_thread = threading.Thread(target=forward_equity, daemon=True, name="EquityForward")
        options_thread = threading.Thread(target=forward_options, daemon=True, name="OptionsForward")
        
        equity_thread.start()
        options_thread.start()
        
        # Wait for both with timeout (5 seconds max per bot, so 5 seconds total for both in parallel)
        equity_thread.join(timeout=5)
        options_thread.join(timeout=5)
        
        equity_success = equity_result['success']
        options_success = options_result['success']
        
        if equity_success:
            STATS["equity_forwarded"] += 1
        if options_success:
            STATS["options_forwarded"] += 1
        
        if not (equity_success or options_success):
            STATS["forward_failures"] += 1
            logger.error("⚠️  Alert failed to forward to both bots!")
            return jsonify({
                "status": "partial_failure",
                "message": "Alert could not be forwarded to any bot"
            }), 503
        
        if equity_success and options_success:
            logger.info("✓ Alert successfully forwarded to BOTH bots IN PARALLEL")
            return jsonify({
                "status": "success",
                "message": "Alert forwarded to both equity and options bots (parallel)",
                "equity_status": "success",
                "options_status": "success"
            }), 200
        else:
            logger.warning("⚠️  Alert forwarded to only 1 bot")
            return jsonify({
                "status": "partial_success",
                "message": "Alert forwarded to some bots",
                "equity_status": "success" if equity_success else "failed",
                "options_status": "success" if options_success else "failed"
            }), 206
        
    except Exception as e:
        logger.error(f"✗ Error processing webhook: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/stats', methods=['GET'])
def get_stats():
    """Return router statistics"""
    return jsonify({
        "router_status": "running",
        "timestamp": datetime.now().isoformat(),
        "statistics": STATS,
        "config": {
            "router_port": ROUTER_PORT,
            "equity_bot": EQUITY_BOT_URL,
            "options_bot": OPTIONS_BOT_URL
        }
    }), 200


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with info"""
    return jsonify({
        "service": "TradingView Webhook Router",
        "version": "1.0.0",
        "endpoints": {
            "webhook": "POST /webhook - Receive TradingView alerts",
            "stats": "GET /stats - View router statistics",
            "health": "GET /health - Health check"
        },
        "configuration": {
            "listen_port": ROUTER_PORT,
            "equity_bot_endpoint": EQUITY_BOT_URL,
            "options_bot_endpoint": OPTIONS_BOT_URL,
            "authentication": "enabled" if ROUTER_SECRET else "disabled"
        }
    }), 200


def start_router():
    """Start the webhook router"""
    logger.info(f"\n{'='*70}")
    logger.info("🚀 TRADINGVIEW WEBHOOK ROUTER STARTING")
    logger.info(f"{'='*70}")
    logger.info(f"Listening on: http://{ROUTER_HOST}:{ROUTER_PORT}")
    logger.info(f"Equity bot endpoint: {EQUITY_BOT_URL}")
    logger.info(f"Options bot endpoint: {OPTIONS_BOT_URL}")
    logger.info(f"Authentication: {'ENABLED' if ROUTER_SECRET else 'DISABLED'}")
    logger.info(f"{'='*70}\n")
    
    try:
        app.run(
            host=ROUTER_HOST,
            port=ROUTER_PORT,
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        logger.error(f"Failed to start router: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    start_router()
