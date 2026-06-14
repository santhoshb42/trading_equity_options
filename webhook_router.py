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
import socket
from flask import Flask, request, jsonify
from typing import Dict, Any
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
EQUITY_BOT_URL = os.getenv("EQUITY_BOT_URL", "http://127.0.0.1:8090/webhook")
CE_BOT_URL = os.getenv("CE_BOT_URL", "http://127.0.0.1:8081/webhook/options")
CE_ITM_BOT_URL = os.getenv("CE_ITM_BOT_URL", "http://127.0.0.1:8080/webhook/options")  # ITM CE bot on 8080
PE_BOT_URL = os.getenv("PE_BOT_URL", "http://127.0.0.1:8082/webhook/put_options")
PE_ITM_BOT_URL = os.getenv("PE_ITM_BOT_URL", "http://127.0.0.1:8083/webhook/put_options")  # ITM PE bot on 8083
IST = ZoneInfo("Asia/Kolkata")
ALERT_START_HOUR_IST = 9
ALERT_START_MINUTE_IST = 30

# Track stats
STATS = {
    "total_alerts_received": 0,
    "alerts_ignored_pre_930": 0,
    "equity_forwarded": 0,
    "ce_forwarded": 0,
    "pe_forwarded": 0,
    "pe_itm_forwarded": 0,
    "forward_failures": 0,
    "last_alert_time": None,
    "last_symbols": []
}


def extract_alert_batch(raw_payload: Any) -> tuple[list[Dict[str, Any]], str]:
    """Return alert payloads plus the source wrapper name, preserving batched alerts."""
    if isinstance(raw_payload, list):
        payloads = [item for item in raw_payload if isinstance(item, dict)]
        if payloads:
            return payloads, 'direct_list'
        return [], 'direct_list'

    if not isinstance(raw_payload, dict):
        return [], 'direct'

    wrapper_configs = (
        ('PE_Alerts', 'PE_Alerts', 'TradingView PE_Alerts wrapper (PUT OPTIONS)'),
        ('Alerts', 'Alerts', 'TradingView Alerts wrapper (CALL OPTIONS)'),
        ('alerts', 'alerts', 'lowercase alerts wrapper'),
    )

    for wrapper_key, alert_source, wrapper_label in wrapper_configs:
        wrapped_payloads = raw_payload.get(wrapper_key)
        if isinstance(wrapped_payloads, list) and wrapped_payloads:
            payloads = [item for item in wrapped_payloads if isinstance(item, dict)]
            if payloads:
                logger.info(f"ℹ️ Extracted {len(payloads)} alert(s) from {wrapper_label}")
                return payloads, alert_source

    return [raw_payload], 'direct'


def forward_alert(url: str, payload: Dict[str, Any], bot_name: str, retries: int = 1) -> bool:
    """Forward alert to bot endpoint with retry logic"""
    for attempt in range(retries):
        try:
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
    2. Check explicit TradingView action/entry_type conventions
    3. Check signal name
    4. Check symbol suffix: ends with "CE" or "PE"
    5. Check message/notes field
    6. Default to "CE" (Call options)
    
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

    action = str(payload.get('action', '')).upper()
    original_action = str(payload.get('original_action', '')).upper()
    entry_type = str(payload.get('entry_type', '')).upper()
    original_entry_type = str(payload.get('original_entry_type', '')).upper()
    option_side = str(payload.get('option_side', '')).upper()

    # Check normalized PE payloads forwarded by upstream systems.
    if option_side == 'PE' or original_action == 'BUY_PUT' or original_entry_type.startswith('PUT_'):
        logger.info(f"✓ Detected PE from normalized payload metadata")
        return 'pe'

    # Check explicit TradingView payload conventions used in this setup
    if action == 'BUY_PUT' or entry_type.startswith('PUT_'):
        logger.info(f"✓ Detected PE from TradingView PUT payload")
        return 'pe'
    if action == 'BUY':
        logger.info(f"✓ Detected CE from BUY action")
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


def normalize_payload_for_target(payload: Dict[str, Any], alert_type: str) -> Dict[str, Any]:
    """Normalize TradingView payloads into the format expected by the target bot."""
    normalized_payload = dict(payload)

    if alert_type == 'pe':
        original_action = str(payload.get('action', ''))
        original_entry_type = str(payload.get('entry_type', ''))

        if original_action.upper() == 'BUY_PUT':
            normalized_payload['action'] = 'SELL'
            normalized_payload['original_action'] = original_action
            normalized_payload['option_side'] = 'PE'

            if original_entry_type.upper().startswith('PUT_'):
                normalized_payload['entry_type'] = original_entry_type[4:]
                normalized_payload['original_entry_type'] = original_entry_type

            logger.info(
                "🔧 Normalized PE payload | "
                f"symbol={payload.get('symbol')} | action={original_action}->SELL | "
                f"entry_type={original_entry_type}->{normalized_payload.get('entry_type', original_entry_type)}"
            )

    return normalized_payload


def is_before_alert_window(now_ist: datetime | None = None) -> bool:
    """Return True when current IST time is before the allowed 09:30 alert window."""
    if now_ist is None:
        now_ist = datetime.now(IST)

    return (now_ist.hour, now_ist.minute) < (ALERT_START_HOUR_IST, ALERT_START_MINUTE_IST)


@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Main webhook endpoint - receives alerts from TradingView"""
    try:
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
        
        payloads, alert_source = extract_alert_batch(raw_payload)
        if not payloads:
            logger.warning(f"Alert payload did not contain any valid alert objects: {raw_payload}")
            return jsonify({"error": "No valid alerts found in payload"}), 400

        valid_payloads = [payload for payload in payloads if payload.get('symbol')]
        if not valid_payloads:
            logger.warning(f"Alert batch missing symbol field: {payloads}")
            return jsonify({"error": "Missing symbol field"}), 400

        dropped_payloads = len(payloads) - len(valid_payloads)
        if dropped_payloads:
            logger.warning(
                "Skipping alerts missing symbol field | "
                f"dropped={dropped_payloads} | alert_source={alert_source}"
            )

        payload = valid_payloads[0]
        batch_size = len(valid_payloads)
        
        # Update stats
        STATS["total_alerts_received"] += batch_size
        STATS["last_alert_time"] = datetime.now().isoformat()
        
        symbols = [alert.get('symbol') for alert in valid_payloads]
        symbol = payload.get('symbol')
        for recent_symbol in symbols:
            if recent_symbol not in STATS["last_symbols"]:
                STATS["last_symbols"].append(recent_symbol)
                if len(STATS["last_symbols"]) > 10:
                    STATS["last_symbols"].pop(0)
        
        logger.info(f"\n{'='*70}")
        logger.info(f"📨 WEBHOOK ALERT RECEIVED (#{STATS['total_alerts_received']})")
        logger.info(f"{'='*70}")
        logger.info(f"Alert source: {alert_source}")
        logger.info(f"Batch size: {batch_size}")
        logger.info(f"Symbols: {symbols}")
        logger.info(f"Primary symbol: {symbol}")
        logger.info(f"Action: {payload.get('action', 'N/A')}")
        logger.info(f"Price: {payload.get('price', 'N/A')}")
        logger.info(f"Timestamp: {datetime.now().isoformat()}")
        if batch_size == 1:
            logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        else:
            logger.info(f"Payload batch: {json.dumps(valid_payloads, indent=2)}")
        logger.info(f"{'='*70}\n")

        current_ist = datetime.now(IST)
        if is_before_alert_window(current_ist):
            STATS["alerts_ignored_pre_930"] += batch_size
            logger.warning(
                "Ignoring alert before 09:30 IST | "
                f"symbol={symbol} | batch_size={batch_size} | alert_source={alert_source} | current_ist={current_ist.isoformat()}"
            )
            return jsonify({
                "status": "ignored",
                "message": "Alerts are ignored before 09:30 AM IST",
                "batch_size": batch_size,
                "current_ist": current_ist.isoformat()
            }), 202
        
        # ✅ INTELLIGENT ROUTING: Detect CE vs PE from wrapper or other fields
        alert_type = detect_alert_type(payload, raw_payload)
        normalized_payloads = [normalize_payload_for_target(alert, alert_type) for alert in valid_payloads]
        forward_payload = normalized_payloads[0] if batch_size == 1 else normalized_payloads
        logger.info(f"🔍 DETECTED: {alert_type.upper()} OPTION ALERT")
        
        # Determine target bot(s)
        targets = []
        if alert_type == 'ce':
            targets.append(('CE OPTIONS BOT (OTM)', CE_BOT_URL, 'ce'))
            targets.append(('CE OPTIONS BOT (ITM)', CE_ITM_BOT_URL, 'ce_itm'))
            logger.info(f"🎯 ROUTING: To CE Bot (OTM on port 8081) and CE Bot (ITM on port 8080)")
        elif alert_type == 'pe':
            targets.append(('PE OPTIONS BOT (OTM)', PE_BOT_URL, 'pe'))
            targets.append(('PE OPTIONS BOT (ITM)', PE_ITM_BOT_URL, 'pe_itm'))
            logger.info(f"🎯 ROUTING: To PE Bot (OTM on port 8082) and PE Bot (ITM on port 8083) | batch_size={batch_size}")
        else:
            targets.append(('EQUITY BOT', EQUITY_BOT_URL, 'equity'))
            logger.info(f"🎯 ROUTING: To EQUITY Bot ({EQUITY_BOT_URL})")
        
        # Forward to target bot(s) IN PARALLEL
        logger.info(f"🔄 Forwarding alert to {len(targets)} bot(s) in PARALLEL...")
        
        results = {}
        threads = []
        
        for bot_name, bot_url, bot_type in targets:
            result_dict = {'success': False}
            results[bot_type] = result_dict
            
            def forward_to_bot(name=bot_name, url=bot_url, res_dict=result_dict):
                res_dict['success'] = forward_alert(url, forward_payload, name)
            
            thread = threading.Thread(target=forward_to_bot, daemon=True, name=f"{bot_type.upper()}Forward")
            thread.start()
            threads.append(thread)
        
        # Wait for all threads with timeout (5 seconds max per bot)
        for thread in threads:
            thread.join(timeout=5)
        
        # Check results
        ce_success = results.get('ce', {}).get('success', False)
        ce_itm_success = results.get('ce_itm', {}).get('success', False)
        pe_success = results.get('pe', {}).get('success', False)
        pe_itm_success = results.get('pe_itm', {}).get('success', False)
        equity_success = results.get('equity', {}).get('success', False)

        # Update stats
        if ce_success or ce_itm_success:
            STATS["ce_forwarded"] += 1
        if pe_success or pe_itm_success:
            STATS["pe_forwarded"] += 1
        if pe_itm_success:
            STATS["pe_itm_forwarded"] += 1
        if equity_success:
            STATS["equity_forwarded"] += 1

        total_success = ce_success or ce_itm_success or pe_success or pe_itm_success or equity_success
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
            "ce_itm_status": "success" if ce_itm_success else "skipped",
            "pe_status": "success" if pe_success else "skipped",
            "pe_itm_status": "success" if pe_itm_success else "skipped",
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
            "ce_itm_bot": CE_ITM_BOT_URL,
            "pe_bot": PE_BOT_URL,
            "pe_itm_bot": PE_ITM_BOT_URL
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
            "ce_itm_bot_endpoint": CE_ITM_BOT_URL,
            "pe_bot_endpoint": PE_BOT_URL,
            "pe_itm_bot_endpoint": PE_ITM_BOT_URL
        }
    }), 200


def wait_for_port_available(host: str, port: int, timeout_seconds: int = 30, poll_interval: float = 1.0) -> bool:
    """Wait for the listen port to become free before starting Flask.

    This avoids transient restart races where another process still holds the
    port for a few seconds and systemd marks the router as failed.
    """
    deadline = time.time() + timeout_seconds
    bind_host = host if host and host != "0.0.0.0" else ""

    while time.time() < deadline:
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_socket.bind((bind_host, port))
            return True
        except OSError as exc:
            remaining = max(0, int(deadline - time.time()))
            logger.warning(
                f"Router port {port} still busy ({exc}) | retrying for up to {remaining}s"
            )
            time.sleep(poll_interval)
        finally:
            test_socket.close()

    return False


def start_router():
    """Start the webhook router"""
    logger.info(f"\n{'='*70}")
    logger.info("🚀 TRADINGVIEW WEBHOOK ROUTER STARTING")
    logger.info(f"{'='*70}")
    logger.info(f"Listening on: http://{ROUTER_HOST}:{ROUTER_PORT}")
    logger.info(f"Equity bot endpoint: {EQUITY_BOT_URL}")
    logger.info(f"CE Options bot endpoint (OTM): {CE_BOT_URL}")
    logger.info(f"CE Options bot endpoint (ITM): {CE_ITM_BOT_URL}")
    logger.info(f"PE Options bot endpoint (OTM): {PE_BOT_URL}")
    logger.info(f"PE Options bot endpoint (ITM): {PE_ITM_BOT_URL}")
    logger.info(f"Alert routing: INTELLIGENT (CE/PE detection enabled)")
    logger.info(f"{'='*70}\n")

    if not wait_for_port_available(ROUTER_HOST, ROUTER_PORT, timeout_seconds=30):
        raise RuntimeError(
            f"Router port {ROUTER_PORT} stayed busy for 30s; aborting startup so systemd can retry"
        )
    
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
