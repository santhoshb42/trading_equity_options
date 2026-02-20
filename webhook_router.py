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
CE_BOT_URL = os.getenv("CE_BOT_URL", "http://127.0.0.1:8081/webhook/options")
PE_BOT_URL = os.getenv("PE_BOT_URL", "http://127.0.0.1:8082/webhook/put_options")

# Optional authentication
ROUTER_SECRET = os.getenv("ROUTER_SECRET", "")

# Track stats
STATS = {
    "total_alerts_received": 0,
    "equity_forwarded": 0,
    "ce_forwarded": 0,
    "pe_forwarded": 0,
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
            
            # Accept 2xx status codes (200, 201, 202 Accepted, 204 No Content)
            if 200 <= response.status_code < 300:
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


def detect_alert_type(payload: Dict[str, Any], raw_payload: Dict[str, Any] = None) -> str:
    """
    Detect if alert is for CE (Call) or PE (Put) options.
    
    Detection priority:
    1. Check wrapper: "PE_Alerts" vs "Alerts" in raw_payload
    2. Check signal name: "BUY_CE", "SELL_PE", etc.
    3. Check symbol suffix: ends with "CE" or "PE"
    4. Check message/notes field
    5. Default to "CE" (Call options)
    
    Returns: "ce", "pe", or "equity"
    """
    if raw_payload is None:
        raw_payload = {}
    
    # Check for PE_Alerts wrapper (PE specific) - HIGHEST PRIORITY
    if "PE_Alerts" in raw_payload:
        logger.info(f"✓ Detected PE from 'PE_Alerts' wrapper")
        return 'pe'
    
    # Check for standard Alerts wrapper (CE specific)
    if "Alerts" in raw_payload:
        logger.info(f"✓ Detected CE from 'Alerts' wrapper")
        return 'ce'
    
    # Check signal/action field for CE/PE
    signal = payload.get('signal', '') or payload.get('action', '')
    if 'PE' in signal.upper():
        logger.info(f"✓ Detected PE from signal/action field")
        return 'pe'
    elif 'CE' in signal.upper():
        logger.info(f"✓ Detected CE from signal/action field")
        return 'ce'
    
    # Check symbol for CE/PE suffix
    symbol = payload.get('symbol', '').upper()
    if symbol.endswith('PE'):
        logger.info(f"✓ Detected PE from symbol suffix")
        return 'pe'
    elif symbol.endswith('CE'):
        logger.info(f"✓ Detected CE from symbol suffix")
        return 'ce'
    
    # Check message field
    message = payload.get('message', '') or payload.get('notes', '')
    if 'PE' in message.upper():
        logger.info(f"✓ Detected PE from message field")
        return 'pe'
    elif 'CE' in message.upper():
        logger.info(f"✓ Detected CE from message field")
        return 'ce'
    
    # Default to CE (Call options are more common)
    logger.info(f"⚠️  Could not detect CE/PE from alert, defaulting to CE")
    return 'ce'


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
        alert_source = "direct"
        
        # Check for PE_Alerts (Put options)
        if "PE_Alerts" in raw_payload and isinstance(raw_payload["PE_Alerts"], list) and len(raw_payload["PE_Alerts"]) > 0:
            payload = raw_payload["PE_Alerts"][0]
            alert_source = "PE_Alerts"
            logger.info(f"ℹ️ Extracted alert from TradingView PE_Alerts wrapper (PUT OPTIONS)")
        # Check for standard Alerts (Call options)
        elif "Alerts" in raw_payload and isinstance(raw_payload["Alerts"], list) and len(raw_payload["Alerts"]) > 0:
            payload = raw_payload["Alerts"][0]
            alert_source = "Alerts"
            logger.info(f"ℹ️ Extracted alert from TradingView Alerts wrapper (CALL OPTIONS)")
        elif "alerts" in raw_payload and isinstance(raw_payload["alerts"], list) and len(raw_payload["alerts"]) > 0:
            payload = raw_payload["alerts"][0]
            alert_source = "alerts"
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
        
        # ✅ INTELLIGENT ROUTING: Detect CE vs PE from wrapper or other fields
        alert_type = detect_alert_type(payload, raw_payload)
        logger.info(f"🔍 DETECTED: {alert_type.upper()} OPTION ALERT")
        
        # Determine target bot(s)
        targets = []
        if alert_type == 'ce':
            targets.append(('CE OPTIONS BOT', CE_BOT_URL, 'ce'))
            logger.info(f"🎯 ROUTING: To CE Bot (port 8081)")
        elif alert_type == 'pe':
            targets.append(('PE OPTIONS BOT', PE_BOT_URL, 'pe'))
            logger.info(f"🎯 ROUTING: To PE Bot (port 8082)")
        else:
            targets.append(('EQUITY BOT', EQUITY_BOT_URL, 'equity'))
            logger.info(f"🎯 ROUTING: To EQUITY Bot (port 8080)")
        
        # Forward to target bot(s) IN PARALLEL
        logger.info(f"🔄 Forwarding alert to {len(targets)} bot(s) in PARALLEL...")
        
        results = {}
        threads = []
        
        for bot_name, bot_url, bot_type in targets:
            result_dict = {'success': False}
            results[bot_type] = result_dict
            
            def forward_to_bot(name=bot_name, url=bot_url, res_dict=result_dict):
                res_dict['success'] = forward_alert(url, payload, name)
            
            thread = threading.Thread(target=forward_to_bot, daemon=True, name=f"{bot_type.upper()}Forward")
            thread.start()
            threads.append(thread)
        
        # Wait for all threads with timeout (5 seconds max per bot)
        for thread in threads:
            thread.join(timeout=5)
        
        # Check results
        ce_success = results.get('ce', {}).get('success', False)
        pe_success = results.get('pe', {}).get('success', False)
        equity_success = results.get('equity', {}).get('success', False)
        
        # Update stats
        if ce_success:
            STATS["ce_forwarded"] += 1
        if pe_success:
            STATS["pe_forwarded"] += 1
        if equity_success:
            STATS["equity_forwarded"] += 1
        
        total_success = ce_success or pe_success or equity_success
        if not total_success:
            STATS["forward_failures"] += 1
            logger.error("⚠️  Alert failed to forward to target bot!")
            return jsonify({
                "status": "failure",
                "message": "Alert could not be forwarded to target bot"
            }), 503
        
        logger.info(f"✓ Alert successfully routed to {alert_type.upper()} bot")
        return jsonify({
            "status": "success",
            "message": f"Alert routed to {alert_type.upper()} bot",
            "alert_type": alert_type,
            "ce_status": "success" if ce_success else "skipped",
            "pe_status": "success" if pe_success else "skipped",
            "equity_status": "success" if equity_success else "skipped"
        }), 200
        
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
            "ce_bot": CE_BOT_URL,
            "pe_bot": PE_BOT_URL
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
            "ce_bot_endpoint": CE_BOT_URL,
            "pe_bot_endpoint": PE_BOT_URL,
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
    logger.info(f"CE Options bot endpoint: {CE_BOT_URL}")
    logger.info(f"PE Options bot endpoint: {PE_BOT_URL}")
    logger.info(f"Authentication: {'ENABLED' if ROUTER_SECRET else 'DISABLED'}")
    logger.info(f"Alert routing: INTELLIGENT (CE/PE detection enabled)")
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
