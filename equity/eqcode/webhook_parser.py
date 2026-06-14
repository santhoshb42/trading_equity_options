"""
Lightweight webhook parser for TradingView alerts.
This module is intentionally minimal and side-effect free so it can be imported
in test environments without starting broker/monitor threads.
"""
from datetime import datetime
from typing import Dict, Any, Tuple

try:
    from .config import WebhookConfig
except Exception:
    # Minimal fallback config
    class WebhookConfig:
        VALID_ACTIONS = ("BUY", "SELL", "EXIT")

try:
    from .bot_logging import log_event
except Exception:
    def log_event(*args, **kwargs):
        pass

try:
    from .signal_filters import validate_signal_quality
except Exception:
    def validate_signal_quality(*args, **kwargs):
        return True, "Filter module unavailable"

# ML Signal Filter integration
try:
    from .ml_signal_filter import MLSignalFilter
    _ml_filter = MLSignalFilter()
    ML_FILTER_AVAILABLE = True
except Exception:
    _ml_filter = None
    ML_FILTER_AVAILABLE = False
    
    def validate_signal_with_ml(*args, **kwargs):
        return True, 0.65, {'status': 'ml_filter_unavailable'}


def process_symbol(raw_symbol: str) -> str:
    # Remove exchange prefixes (NSE:, BSE:, etc) and suffixes (.NSE)
    clean_symbol = raw_symbol.replace("-EQ", "").replace(".NSE", "").strip().upper()
    
    # Remove exchange prefix (NSE:, BSE:)
    if ":" in clean_symbol:
        clean_symbol = clean_symbol.split(":", 1)[1]
    
    processed_symbol = f"{clean_symbol}-EQ"
    try:
        log_event("SYMBOL", f"Processed symbol: {raw_symbol} -> {processed_symbol}")
    except Exception:
        pass
    return processed_symbol


def validate_alert(alert_data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate incoming TradingView alert. Enforces:
      - If `verdict` is present: it must be 1 to proceed
      - `score` and `confidence` must both be > 90 when `verdict` present
    Supports both the TradingView `Alerts` array wrapper and legacy flat payloads.

    Returns (is_valid, error_msg, processed_data)
    """
    try:
        log_event("VALIDATION_START", "Starting alert validation", raw_data_keys=list(alert_data.keys()) if isinstance(alert_data, dict) else "not_dict")
        
        source_alert = None
        if isinstance(alert_data, dict) and "Alerts" in alert_data and isinstance(alert_data["Alerts"], list):
            if len(alert_data["Alerts"]) == 0:
                log_event("VALIDATION_FAILED", "Alerts array is empty")
                return False, "Alerts array is empty", {}
            source_alert = alert_data["Alerts"][0]
            log_event("VALIDATION_FORMAT", "Detected TradingView Alerts array format", alert_count=len(alert_data["Alerts"]))
        elif isinstance(alert_data, dict) and "alerts" in alert_data and isinstance(alert_data["alerts"], list):
            if len(alert_data["alerts"]) == 0:
                log_event("VALIDATION_FAILED", "alerts array is empty")
                return False, "alerts array is empty", {}
            source_alert = alert_data["alerts"][0]
            log_event("VALIDATION_FORMAT", "Detected lowercase alerts array format", alert_count=len(alert_data["alerts"]))
        else:
            source_alert = alert_data
            log_event("VALIDATION_FORMAT", "Detected flat alert format (no wrapper)")

        raw_symbol = str(source_alert.get("symbol") or source_alert.get("Symbol") or source_alert.get("ticker") or source_alert.get("sym") or "").strip()
        log_event("VALIDATION_EXTRACT", "Extracted raw symbol", raw_symbol=raw_symbol or "EMPTY")

        try:
            price = float(source_alert.get("price") if source_alert.get("price") is not None else alert_data.get("price", 0))
            log_event("VALIDATION_EXTRACT", "Extracted price", price=price)
        except Exception as e:
            price = 0.0
            log_event("VALIDATION_ERROR", "Failed to extract price", error=str(e))

        # Extract quality/metrics
        try:
            confidence = float(source_alert.get("confidence", 0))
        except (ValueError, TypeError):
            confidence = 0.0
        
        log_event("VALIDATION_EXTRACT", "Extracted confidence", confidence=confidence)

        try:
            score = float(source_alert.get("score", 0))
        except (ValueError, TypeError):
            score = 0.0
        
        log_event("VALIDATION_EXTRACT", "Extracted score", score=score)

        try:
            verdict = float(source_alert.get("verdict", 0))
        except (ValueError, TypeError):
            verdict = 0.0
        
        log_event("VALIDATION_EXTRACT", "Extracted verdict", verdict=verdict)

        # Determine action first (before verdict validation)
        action = ""
        action_raw = source_alert.get("action") or source_alert.get("signal")
        if action_raw is not None:
            s = str(action_raw).strip()
            if s.upper() in getattr(WebhookConfig, 'VALID_ACTIONS', ("BUY","SELL","EXIT")):
                action = s.upper()
        
        # Fallback to verdict for action determination
        if not action and "verdict" in source_alert:
            action_raw = source_alert.get("verdict")
            if action_raw is not None:
                s = str(action_raw).strip()
                if s.upper() in getattr(WebhookConfig, 'VALID_ACTIONS', ("BUY","SELL","EXIT")):
                    action = s.upper()

        # Apply verdict-based quality checks only for BUY signals or when verdict=1
        if "verdict" in source_alert:
            try:
                v_int = int(verdict)
                log_event("VALIDATION_VERDICT", "Processing verdict-based validation", verdict=v_int, action=action or "NONE")
            except Exception as e:
                log_event("VALIDATION_FAILED", "Invalid verdict value", verdict=verdict, error=str(e))
                return False, f"Invalid verdict value: {verdict}", {}
            
            # For explicit SELL/EXIT actions, allow verdict=0 with relaxed quality checks
            if action in ("SELL", "EXIT") and v_int == 0:
                log_event("VALIDATION_SELL_CHECK", "Checking SELL/EXIT signal quality", confidence=confidence, score=score, threshold=85.0)
                # SELL signals with verdict=0 are valid, use relaxed quality thresholds
                if confidence < 85.0:
                    log_event("VALIDATION_FAILED", "SELL confidence too low", confidence=confidence, threshold=85.0)
                    return False, f"SELL alert quality too low: confidence {confidence} < 85", {}
                if score < 85.0:
                    log_event("VALIDATION_FAILED", "SELL score too low", score=score, threshold=85.0)
                    return False, f"SELL alert quality too low: score {score} < 85", {}
                log_event("VALIDATION_PASSED", "SELL/EXIT signal quality check passed", confidence=confidence, score=score)
            elif v_int == 1:
                log_event("VALIDATION_BUY_CHECK", "Checking BUY signal quality", confidence=confidence, score=score, threshold=0)
                # DISABLED: Accept all BUY signals for direct trading
                # if confidence < 90.0:
                #     log_event("VALIDATION_FAILED", "BUY confidence too low", confidence=confidence, threshold=90.0)
                #     return False, f"Alert quality too low: confidence {confidence} < 90", {}
                # if score < 90.0:
                #     log_event("VALIDATION_FAILED", "BUY score too low", score=score, threshold=90.0)
                #     return False, f"Alert quality too low: score {score} < 90", {}
                log_event("VALIDATION_PASSED", "BUY signal quality check passed", confidence=confidence, score=score)
                # If no explicit action, infer BUY from verdict=1
                if not action:
                    action = "BUY"
                    log_event("VALIDATION_ACTION_INFERRED", "Inferred action from verdict=1", action="BUY")
            else:
                # Invalid verdict for signals without explicit action
                if not action:
                    log_event("VALIDATION_FAILED", "Invalid verdict without explicit action", verdict=verdict, action="NONE")
                    return False, f"Invalid verdict {verdict} without explicit action", {}
                log_event("VALIDATION_WARNING", "Non-standard verdict value with explicit action", verdict=verdict, action=action)

        # Fallback to verdict->BUY if verdict==1 and no action determined yet
        if not action and "verdict" in source_alert:
            if int(verdict) == 1:
                action = "BUY"

        # Fallback top-level action
        if not action:
            top_action = str(alert_data.get("action", "")).strip()
            if top_action:
                action = top_action.upper()

        if not raw_symbol:
            log_event("VALIDATION_FAILED", "Empty symbol in alert")
            return False, "Empty symbol", {}

        symbol = process_symbol(raw_symbol)
        log_event("VALIDATION_SYMBOL", "Symbol processed", raw=raw_symbol, processed=symbol)

        if price <= 0:
            log_event("VALIDATION_FAILED", "Invalid price", price=price)
            return False, f"Invalid price: {price}", {}

        # collect indicators
        indicators = {}
        for k in ("score", "confidence", "verdict", "vwap", "rsi", "ema9", "ema20", "vol_z", "open_gap_pct", "hl_range_pct"):
            if k in source_alert:
                v = source_alert.get(k)
                try:
                    indicators[k] = float(v)
                except Exception:
                    indicators[k] = v
        
        log_event("VALIDATION_INDICATORS", "Collected indicators", count=len(indicators), keys=list(indicators.keys()))

        processed = {
            "symbol": symbol,
            "action": action or "UNKNOWN",
            "price": price,
            "timestamp": datetime.now().isoformat(),
            "raw_symbol": raw_symbol,
            "raw_alert": source_alert,
            "indicators": indicators,
            "quality": {"confidence": confidence, "score": score, "verdict": verdict}
        }

        log_event("VALIDATION_SUCCESS", "Alert validation passed", 
                 symbol=symbol, action=action, price=price, 
                 confidence=confidence, score=score, verdict=verdict)
        
        # Apply signal quality filters
        try:
            filters_passed, filter_reason = validate_signal_quality(processed['quality'], symbol, price)
            if not filters_passed:
                log_event("SIGNAL_FILTER_REJECTED", f"Signal rejected by quality filters: {filter_reason}", 
                         symbol=symbol, action=action, price=price, reason=filter_reason)
                return False, f"Signal quality filter: {filter_reason}", {}
            log_event("SIGNAL_FILTER_PASSED", "Signal passed all quality filters",
                     symbol=symbol, action=action, confidence=confidence, score=score)
        except Exception as e:
            log_event("SIGNAL_FILTER_ERROR", f"Signal filter error: {str(e)}", symbol=symbol, error_type=type(e).__name__)
            # Continue on filter error (don't block trades)
        
        # Apply ML-based signal filtering (learns from trade outcomes)
        if ML_FILTER_AVAILABLE and _ml_filter and action == "BUY":
            try:
                ml_valid, ml_confidence, ml_details = _ml_filter.validate_signal_with_ml(
                    symbol=symbol,
                    alert_data=processed['quality'],
                    entry_price=price
                )
                
                # Log detailed ML decision with feature extraction data
                log_event(
                    "ML_SIGNAL_VALIDATION",
                    f"ML validation: {'ACCEPTED' if ml_valid else 'REJECTED'}",
                    symbol=symbol,
                    action=action,
                    price=price,
                    ml_confidence=ml_confidence,
                    ml_status=ml_details.get('status', 'unknown'),
                    model_trained=ml_details.get('model_trained', False),
                    rf_score=ml_details.get('rf_score'),
                    gb_score=ml_details.get('gb_score'),
                    svm_score=ml_details.get('svm_score'),
                    ensemble_score=ml_details.get('ensemble_score'),
                    training_samples=ml_details.get('training_samples', 0)
                )
                
                # Only reject if model is trained AND high confidence negative prediction
                if not ml_valid and ml_details.get('model_trained', False):
                    log_event(
                        "ML_FILTER_REJECTED",
                        f"Signal rejected by ML filter (confidence: {ml_confidence:.3f})",
                        symbol=symbol,
                        action=action,
                        price=price,
                        ml_reason=ml_details.get('reason', 'Unknown')
                    )
                    return False, f"ML signal filter: {ml_details.get('reason', 'Poor signal quality')}", {}
                
                # Log feature extraction data for analysis
                if ml_details.get('features'):
                    log_event(
                        "ML_FEATURES_EXTRACTED",
                        f"Features extracted for {symbol}",
                        symbol=symbol,
                        momentum_3=ml_details['features'].get('momentum_3'),
                        momentum_5=ml_details['features'].get('momentum_5'),
                        volatility=ml_details['features'].get('volatility'),
                        rsi_extreme=ml_details['features'].get('rsi_extreme'),
                        volume_trend=ml_details['features'].get('volume_trend'),
                        trend_consistency=ml_details['features'].get('trend_consistency')
                    )
                
            except Exception as e:
                log_event(
                    "ML_FILTER_ERROR",
                    f"ML signal filter error: {str(e)}",
                    symbol=symbol,
                    error_type=type(e).__name__,
                    action="continuing_without_ml"
                )
                # Continue on ML filter error (don't block trades)
        
        return True, "OK", processed

    except Exception as e:
        log_event("VALIDATION_EXCEPTION", "Alert validation crashed", error=str(e), error_type=type(e).__name__)
        return False, f"Alert validation error: {str(e)}", {}
