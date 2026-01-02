"""
Live Data Table Formatter

Generates human-readable table formats for live trading data:
- Markdown tables (viewable in VS Code preview)
- CSV format (importable to Excel)
- ASCII tables (console display)
- JSON with table structure

Files generated:
- live_data_tables.md (Markdown - best for VS Code)
- live_data_trades.csv (CSV - for Excel)
- live_data.json (existing - data source)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# =============================================================================
# Table Formatters
# =============================================================================

class LiveDataTableFormatter:
    """Formats live trading data into readable table formats"""
    
    def __init__(self):
        self.data_dir = Path('/root/santhosh/trading/options/data')
        self.live_data_file = self.data_dir / 'live_data.json'
        self.markdown_file = self.data_dir / 'live_data_tables.md'
        self.csv_file = self.data_dir / 'live_data_trades.csv'
    
    def load_live_data(self) -> Dict[str, Any]:
        """Load live_data.json"""
        try:
            with open(self.live_data_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading live_data.json: {e}")
            return None
    
    # =========================================================================
    # MARKDOWN TABLE FORMATTER (Best for VS Code)
    # =========================================================================
    
    def generate_markdown_summary(self, live_data: Dict[str, Any]) -> str:
        """Generate markdown summary table"""
        summary = live_data['summary']
        
        markdown = """# 📊 LIVE TRADING SUMMARY

**Last Updated**: {timestamp}  
**Trading Mode**: {mode} | **Market**: {market}

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total Budget | ₹{total_budget:,.0f} |
| Budget Used | ₹{budget_used:,.0f} ({budget_pct:.1f}%) |
| Budget Remaining | ₹{budget_remaining:,.0f} |
| Max Positions | {max_pos} |
| **Ongoing Trades** | **{ongoing}** |
| **Closed Trades** | **{closed}** |
| Total Trades | {total} |
| Winning Trades | {wins} ✅ |
| Losing Trades | {losses} ❌ |
| Win Rate | {win_rate:.1f}% |
| **Total PNL** | **₹{pnl:,.2f}** ({pnl_pct:.2f}%) |
| Avg Win | ₹{avg_win:,.2f} |
| Avg Loss | ₹{avg_loss:,.2f} |
| Largest Win | ₹{best:,.2f} |
| Largest Loss | ₹{worst:,.2f} |

""".format(
            timestamp=datetime.fromisoformat(live_data['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
            mode=live_data['trading_mode'],
            market=live_data['market_status'],
            total_budget=summary['total_budget'],
            budget_used=summary['budget_used'],
            budget_pct=summary['budget_used_percent'],
            budget_remaining=summary['budget_remaining'],
            max_pos=summary['max_positions_allowed'],
            ongoing=summary['ongoing_trades'],
            closed=summary['closed_trades'],
            total=summary['total_trades_today'],
            wins=summary['winning_trades'],
            losses=summary['losing_trades'],
            win_rate=summary['win_rate_percent'],
            pnl=summary['total_pnl'],
            pnl_pct=summary['total_pnl_percent'],
            avg_win=summary['avg_win'],
            avg_loss=summary['avg_loss'],
            best=summary['largest_win'],
            worst=summary['largest_loss']
        )
        
        return markdown
    
    def generate_markdown_open_trades(self, trades: List[Dict[str, Any]]) -> str:
        """Generate markdown table for open trades"""
        open_trades = [t for t in trades if t['status'] == 'OPEN']
        
        if not open_trades:
            return """## 📍 Open Trades

No open trades.

"""
        
        markdown = """## 📍 Open Trades ({count} active)

| ID | Symbol | Strike | Type | Action | Qty | Entry Time | Entry₹ | Current₹ | Current IV | Δ | Unrealized PNL | High₹ |
|:---|:-------|:------:|:----:|:------:|:---:|:----------:|:------:|:--------:|:----------:|:---:|:---------------:|:-----:|
""".format(count=len(open_trades))
        
        for trade in open_trades:
            entry_time = datetime.fromisoformat(trade['entry_time']).strftime('%H:%M:%S')
            delta = trade['current_greeks'].get('delta', 0) if trade['current_greeks'] else 0
            pnl = trade['unrealized_pnl']
            pnl_color = '🟢' if pnl >= 0 else '🔴'
            
            markdown += f"""| {trade['trade_id']} | {trade['symbol']} | {trade['strike']:.0f} | {trade['contract_type']} | {trade['action']} | {trade['quantity']} | {entry_time} | ₹{trade['entry_premium']:.2f} | ₹{trade['current_premium']:.2f} | {trade['current_iv']:.1f}% | {delta:.2f} | {pnl_color} ₹{pnl:.2f} | ₹{trade['highest_premium']:.2f} |
"""
        
        return markdown
    
    def generate_markdown_closed_trades(self, trades: List[Dict[str, Any]]) -> str:
        """Generate markdown table for closed trades"""
        closed_trades = [t for t in trades if t['status'] == 'CLOSED']
        
        if not closed_trades:
            return """## ✅ Closed Trades

No closed trades.

"""
        
        markdown = """## ✅ Closed Trades ({count} closed)

| ID | Symbol | Strike | Type | Action | Entry₹ | Exit₹ | Duration | Exit Reason | Realized PNL | Return % |
|:---|:-------|:------:|:----:|:------:|:------:|:-----:|:--------:|:-----------:|:------------:|:--------:|
""".format(count=len(closed_trades))
        
        for trade in closed_trades:
            pnl = trade['realized_pnl']
            pnl_color = '🟢' if pnl >= 0 else '🔴'
            
            markdown += f"""| {trade['trade_id']} | {trade['symbol']} | {trade['strike']:.0f} | {trade['contract_type']} | {trade['action']} | ₹{trade['entry_premium']:.2f} | ₹{trade['exit_premium']:.2f} | {trade['duration_formatted']} | {trade['exit_reason']} | {pnl_color} ₹{pnl:.2f} | {trade['realized_pnl_percent']:.2f}% |
"""
        
        return markdown
    
    def generate_markdown_detailed_trades(self, trades: List[Dict[str, Any]]) -> str:
        """Generate detailed markdown section with Greeks"""
        if not trades:
            return ""
        
        markdown = "\n## 🔬 Detailed Trade Analysis\n\n"
        
        for trade in trades:
            status_emoji = "🔵" if trade['status'] == 'OPEN' else "✅"
            markdown += f"""### {status_emoji} {trade['symbol']} (ID: {trade['trade_id']})

**Entry Details**
| Field | Value |
|-------|-------|
| Entry Time | {trade['entry_time']} |
| Entry Premium | ₹{trade['entry_premium']:.2f} |
| Entry Value | ₹{trade['entry_value']:.2f} |
| Entry IV | {trade['entry_iv']:.2f}% |
| Strike Price | {trade['strike']:.0f} |
| Action | {trade['action']} |
| Quantity | {trade['quantity']} |

"""
            
            # Entry Greeks
            if trade['entry_greeks']:
                markdown += """**Entry Greeks**
| Greek | Value |
|-------|-------|
"""
                greeks = trade['entry_greeks']
                markdown += f"| Delta (Δ) | {greeks.get('delta', 0):.4f} |\n"
                markdown += f"| Gamma (Γ) | {greeks.get('gamma', 0):.6f} |\n"
                markdown += f"| Theta (Θ) | {greeks.get('theta', 0):.6f} |\n"
                markdown += f"| Vega (ν) | {greeks.get('vega', 0):.4f} |\n\n"
            
            if trade['status'] == 'OPEN':
                markdown += f"""**Current Status**
| Field | Value |
|-------|-------|
| Current Premium | ₹{trade['current_premium']:.2f} |
| Current Value | ₹{trade['current_value']:.2f} |
| Current IV | {trade['current_iv']:.2f}% |
| Highest Premium | ₹{trade['highest_premium']:.2f} |
| Unrealized PNL | ₹{trade['unrealized_pnl']:.2f} |
| Unrealized PNL % | {trade['unrealized_pnl_percent']:.2f}% |

"""
                
                # Current Greeks
                if trade['current_greeks']:
                    markdown += """**Current Greeks**
| Greek | Value |
|-------|-------|
"""
                    greeks = trade['current_greeks']
                    markdown += f"| Delta (Δ) | {greeks.get('delta', 0):.4f} |\n"
                    markdown += f"| Gamma (Γ) | {greeks.get('gamma', 0):.6f} |\n"
                    markdown += f"| Theta (Θ) | {greeks.get('theta', 0):.6f} |\n"
                    markdown += f"| Vega (ν) | {greeks.get('vega', 0):.4f} |\n\n"
            else:
                # Closed trade details
                markdown += f"""**Exit Details**
| Field | Value |
|-------|-------|
| Exit Time | {trade['exit_time']} |
| Exit Premium | ₹{trade['exit_premium']:.2f} |
| Exit Reason | {trade['exit_reason']} |
| Exit IV | {trade['exit_iv']:.2f}% |
| Duration | {trade['duration_formatted']} |
| Realized PNL | ₹{trade['realized_pnl']:.2f} |
| Realized PNL % | {trade['realized_pnl_percent']:.2f}% |

"""
                
                # Exit Greeks
                if trade['exit_greeks']:
                    markdown += """**Exit Greeks**
| Greek | Value |
|-------|-------|
"""
                    greeks = trade['exit_greeks']
                    markdown += f"| Delta (Δ) | {greeks.get('delta', 0):.4f} |\n"
                    markdown += f"| Gamma (Γ) | {greeks.get('gamma', 0):.6f} |\n"
                    markdown += f"| Theta (Θ) | {greeks.get('theta', 0):.6f} |\n"
                    markdown += f"| Vega (ν) | {greeks.get('vega', 0):.4f} |\n\n"
            
            markdown += "---\n\n"
        
        return markdown
    
    def generate_markdown_report(self) -> str:
        """Generate complete markdown report"""
        live_data = self.load_live_data()
        if not live_data:
            return ""
        
        report = self.generate_markdown_summary(live_data)
        report += self.generate_markdown_open_trades(live_data['trades'])
        report += self.generate_markdown_closed_trades(live_data['trades'])
        report += self.generate_markdown_detailed_trades(live_data['trades'])
        
        return report
    
    # =========================================================================
    # CSV FORMAT (for Excel - Simplified with proper alignment)
    # =========================================================================
    
    def generate_csv(self) -> str:
        """Generate CSV format with closed trades first, then ongoing trades"""
        # Read closed trades from option_pnl_history.json
        pnl_file = self.data_dir / 'option_pnl_history.json'
        closed_trades = []
        if pnl_file.exists():
            with open(pnl_file, 'r') as f:
                pnl_data = json.load(f)
                if isinstance(pnl_data, list):
                    closed_trades = pnl_data
                else:
                    closed_trades = pnl_data.get('trades', [])
        
        # Filter today's closed trades
        today = datetime.now().date().isoformat()
        today_closed = [t for t in closed_trades if (t.get('closed_at', '') or t.get('exit_time', '')).startswith(today)]
        
        # Read ongoing trades from option_positions.json
        pos_file = self.data_dir / 'option_positions.json'
        ongoing_trades = []
        if pos_file.exists():
            with open(pos_file, 'r') as f:
                pos_data = json.load(f)
                if isinstance(pos_data, dict):
                    ongoing_trades = pos_data.get('positions', [])
                else:
                    ongoing_trades = pos_data
        
        def extract_underlying(symbol):
            """Extract underlying from option symbol (e.g., INFY27JAN261640CE -> INFY)"""
            import re
            match = re.match(r'^([A-Z]+)', symbol)
            return match.group(1) if match else 'N/A'
        
        # Generate CSV with fixed-width columns
        csv_lines = []
        
        # === TIMESTAMP ===
        csv_lines.append(f'Last Updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        csv_lines.append("")
        
        # === SECTION 1: CLOSED TRADES ===
        csv_lines.append("=== CLOSED TRADES (Today) ===")
        csv_lines.append("Sts | Underlying | Time  | Entry  | Exit   | High   | Qty    | PnL      | PnL%  | Dur   | Reason   | EntD   | EntG   | EntT   | ExD    | ExG    | ExT")
        csv_lines.append("----+------------+-------+--------+--------+--------+--------+----------+-------+-------+----------+--------+--------+--------+--------+--------+--------")
        
        # Sort closed trades by close time (most recent first)
        today_closed_sorted = sorted(today_closed, key=lambda x: x.get('closed_at', x.get('exit_time', '')), reverse=True)
        
        for trade in today_closed_sorted:
            symbol = trade.get('symbol', 'N/A')
            underlying = extract_underlying(symbol)[:12]  # Limit to 12 chars
            
            entry_time = trade.get('entry_time', 'N/A')
            if len(entry_time) > 16:
                entry_time = entry_time[11:16]  # Extract HH:MM only
            
            entry_prem = trade.get('entry_premium', 0)
            exit_prem = trade.get('exit_premium', 0)
            highest_prem = trade.get('highest_premium', 0)
            qty = trade.get('quantity', 0)
            pnl = trade.get('pnl', 0)
            pnl_pct = trade.get('pnl_percent', 0)
            
            # Duration
            duration_sec = trade.get('duration', 0)
            if duration_sec < 60:
                duration = f"{duration_sec:.0f}s"
            elif duration_sec < 3600:
                duration = f"{duration_sec/60:.0f}m"
            else:
                duration = f"{duration_sec/3600:.1f}h"
            
            exit_reason = trade.get('exit_reason', 'N/A')
            # Shorten exit reason
            if 'TRIAL_SL_HIT' in exit_reason:
                exit_reason = 'TRIAL_SL'
            elif 'MOMENTUM_REVERSAL' in exit_reason:
                exit_reason = 'MOMENTUM'
            elif 'EXPIRY' in exit_reason:
                exit_reason = 'EXPIRY'
            elif 'TARGET' in exit_reason:
                exit_reason = 'TARGET'
            elif 'STOPLOSS' in exit_reason:
                exit_reason = 'STOPLOSS'
            elif 'EOD_SQUAREOFF' in exit_reason:
                exit_reason = 'EOD_SQ'
            
            # Get entry/exit greeks
            entry_greeks = trade.get('entry_greeks', {})
            exit_greeks = trade.get('exit_greeks', {})
            entry_delta = entry_greeks.get('delta', 0)
            entry_gamma = entry_greeks.get('gamma', 0)
            entry_theta = entry_greeks.get('theta', 0)
            exit_delta = exit_greeks.get('delta', 0)
            exit_gamma = exit_greeks.get('gamma', 0)
            exit_theta = exit_greeks.get('theta', 0)
            
            # Format with fixed widths matching header
            line = f"CLS | {underlying:<10} | {entry_time:>5} | {entry_prem:>6.2f} | {exit_prem:>6.2f} | {highest_prem:>6.2f} | {qty:>6d} | {pnl:>8.1f} | {pnl_pct:>5.1f} | {duration:>5} | {exit_reason:<8} | {entry_delta:>6.3f} | {entry_gamma:>6.3f} | {entry_theta:>6.2f} | {exit_delta:>6.3f} | {exit_gamma:>6.3f} | {exit_theta:>6.2f}"
            csv_lines.append(line)
        
        # === SECTION 2: ONGOING TRADES ===
        csv_lines.append("")
        csv_lines.append("=== ONGOING TRADES (Live) ===")
        csv_lines.append("Sts | Underlying | Time  | Entry  | Curr   | High   | Qty    | UnPnL    | PnL%  | Dur   | EntD   | EntG   | EntT   | CurD   | CurG   | CurT")
        csv_lines.append("----+------------+-------+--------+--------+--------+--------+----------+-------+-------+--------+--------+--------+--------+--------+--------")
        
        # Sort ongoing by entry time (oldest first)
        ongoing_sorted = sorted(ongoing_trades, key=lambda x: x.get('entry_time', ''))
        
        for trade in ongoing_sorted:
            symbol = trade.get('symbol', 'N/A')
            underlying = extract_underlying(symbol)[:12]
            
            entry_time = trade.get('entry_time', 'N/A')
            if len(entry_time) > 16:
                entry_time = entry_time[11:16]  # Extract HH:MM only
            
            entry_prem = trade.get('entry_premium', 0)
            current_prem = trade.get('current_premium', 0)
            highest_prem = trade.get('highest_premium', 0)
            qty = trade.get('quantity', 0)
            unrealized_pnl = trade.get('unrealized_pnl', 0)
            pnl_pct = (unrealized_pnl / (entry_prem * qty) * 100) if (entry_prem * qty) > 0 else 0
            
            # Duration from entry to now
            try:
                entry_dt = datetime.fromisoformat(trade.get('entry_time', ''))
                duration_sec = (datetime.now() - entry_dt).total_seconds()
                if duration_sec < 60:
                    duration = f"{duration_sec:.0f}s"
                elif duration_sec < 3600:
                    duration = f"{duration_sec/60:.0f}m"
                else:
                    duration = f"{duration_sec/3600:.1f}h"
            except:
                duration = "N/A"
            
            # Entry and current greeks
            entry_greeks = trade.get('entry_greeks', {})
            current_greeks = trade.get('current_greeks', {})
            entry_delta = entry_greeks.get('delta', 0)
            entry_gamma = entry_greeks.get('gamma', 0)
            entry_theta = entry_greeks.get('theta', 0)
            cur_delta = current_greeks.get('delta', 0)
            cur_gamma = current_greeks.get('gamma', 0)
            cur_theta = current_greeks.get('theta', 0)
            
            # Format with fixed widths matching header
            line = f"OPN | {underlying:<10} | {entry_time:>5} | {entry_prem:>6.2f} | {current_prem:>6.2f} | {highest_prem:>6.2f} | {qty:>6d} | {unrealized_pnl:>8.1f} | {pnl_pct:>5.1f} | {duration:>5} | {entry_delta:>6.3f} | {entry_gamma:>6.3f} | {entry_theta:>6.2f} | {cur_delta:>6.3f} | {cur_gamma:>6.3f} | {cur_theta:>6.2f}"
            csv_lines.append(line)
        
        return "\n".join(csv_lines)
    
    # =========================================================================
    # ASCII TABLE (for console)
    # =========================================================================
    
    def generate_ascii_table(self) -> str:
        """Generate ASCII table format"""
        live_data = self.load_live_data()
        if not live_data:
            return ""
        
        summary = live_data['summary']
        trades = live_data['trades']
        
        output = f"""
╔════════════════════════════════════════════════════════════════════════╗
║                    LIVE TRADING DASHBOARD - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}              ║
╚════════════════════════════════════════════════════════════════════════╝

📊 SUMMARY STATISTICS
{'─' * 76}
Total Budget        : ₹{summary['total_budget']:>12,.0f}
Budget Used         : ₹{summary['budget_used']:>12,.0f} ({summary['budget_used_percent']:>6.1f}%)
Budget Remaining    : ₹{summary['budget_remaining']:>12,.0f}
─────────────────────────────────────────────────────────────────────────
Ongoing Trades      : {summary['ongoing_trades']:>13} / {summary['max_positions_allowed']}
Closed Trades       : {summary['closed_trades']:>20}
Total Trades        : {summary['total_trades_today']:>20}
─────────────────────────────────────────────────────────────────────────
Winning Trades      : {summary['winning_trades']:>20} ✅
Losing Trades       : {summary['losing_trades']:>20} ❌
Win Rate            : {summary['win_rate_percent']:>19.1f}%
─────────────────────────────────────────────────────────────────────────
Total PNL           : ₹{summary['total_pnl']:>12,.2f} ({summary['total_pnl_percent']:>6.2f}%)
Avg Win             : ₹{summary['avg_win']:>12,.2f}
Avg Loss            : ₹{summary['avg_loss']:>12,.2f}
Best Trade          : ₹{summary['largest_win']:>12,.2f}
Worst Trade         : ₹{summary['largest_loss']:>12,.2f}

"""
        
        # Open trades table
        open_trades = [t for t in trades if t['status'] == 'OPEN']
        if open_trades:
            output += f"""
📍 OPEN TRADES ({len(open_trades)} active)
{'─' * 76}
{self._format_trades_table(open_trades, detailed=False)}
"""
        
        # Closed trades table
        closed_trades = [t for t in trades if t['status'] == 'CLOSED']
        if closed_trades:
            output += f"""
✅ CLOSED TRADES ({len(closed_trades)} closed)
{'─' * 76}
{self._format_closed_trades_table(closed_trades)}
"""
        
        return output
    
    def _format_trades_table(self, trades: List[Dict], detailed: bool = False) -> str:
        """Format trades as ASCII table"""
        table = "Symbol              | Strike | Type | Action | Entry₹   | Current₹ | Unrealized₹ | Return%\n"
        table += "-" * 100 + "\n"
        
        for trade in trades:
            pnl = trade['unrealized_pnl']
            pnl_pct = trade['unrealized_pnl_percent']
            pnl_sign = "+" if pnl >= 0 else ""
            
            table += f"{trade['symbol']:<20} | {trade['strike']:>6.0f} | {trade['contract_type']:>4} | {trade['action']:>6} | {trade['entry_premium']:>8.2f} | {trade['current_premium']:>8.2f} | {pnl_sign}{pnl:>10.2f} | {pnl_sign}{pnl_pct:>6.2f}%\n"
        
        return table
    
    def _format_closed_trades_table(self, trades: List[Dict]) -> str:
        """Format closed trades as ASCII table"""
        table = "Symbol              | Entry₹   | Exit₹    | Duration     | Reason  | Realized₹   | Return%\n"
        table += "-" * 100 + "\n"
        
        for trade in trades:
            pnl = trade['realized_pnl']
            pnl_pct = trade['realized_pnl_percent']
            pnl_sign = "+" if pnl >= 0 else ""
            
            table += f"{trade['symbol']:<20} | {trade['entry_premium']:>8.2f} | {trade['exit_premium']:>8.2f} | {trade['duration_formatted']:>12} | {trade['exit_reason']:<7} | {pnl_sign}{pnl:>10.2f} | {pnl_sign}{pnl_pct:>6.2f}%\n"
        
        return table
    
    # =========================================================================
    # SAVE ALL FORMATS
    # =========================================================================
    
    def save_all_formats(self) -> bool:
        """Generate and save all table formats"""
        try:
            # Ensure data directory exists
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate Markdown
            markdown_report = self.generate_markdown_report()
            with open(self.markdown_file, 'w') as f:
                f.write(markdown_report)
            print(f"✅ Markdown tables saved: {self.markdown_file}")
            
            # Generate CSV
            csv_data = self.generate_csv()
            with open(self.csv_file, 'w') as f:
                f.write(csv_data)
            print(f"✅ CSV data saved: {self.csv_file}")
            
            # Generate and display ASCII
            ascii_table = self.generate_ascii_table()
            print(f"\n{ascii_table}")
            
            return True
        except Exception as e:
            print(f"❌ Error saving table formats: {e}")
            return False


# Global instance
_formatter = None

def get_table_formatter() -> LiveDataTableFormatter:
    """Get or create global formatter instance"""
    global _formatter
    if _formatter is None:
        _formatter = LiveDataTableFormatter()
    return _formatter
