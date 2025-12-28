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
        """Generate CSV format for all trades - Essential columns only with alignment"""
        live_data = self.load_live_data()
        if not live_data:
            return ""
        
        trades = live_data['trades']
        
        # Column widths for alignment
        col_widths = {
            'Trade ID': 12,
            'Symbol': 25,
            'Action': 6,
            'Quantity': 8,
            'Status': 8,
            'Entry Premium': 15,
            'Entry Value': 15,
            'Alert Price': 12,
            'Current Premium': 15,
            'Current Value': 15,
            'Highest Premium': 15,
            'Unrealized PNL': 15,
            'Unrealized PNL %': 15
        }
        
        # Header row
        headers = ['Trade ID', 'Symbol', 'Action', 'Quantity', 'Status', 
                  'Entry Premium', 'Entry Value', 'Alert Price', 
                  'Current Premium', 'Current Value', 'Highest Premium', 
                  'Unrealized PNL', 'Unrealized PNL %']
        
        # Create header with proper alignment
        csv = ""
        for header in headers:
            csv += f"{header:<{col_widths[header]}} | "
        csv = csv.rstrip(" | ") + "\n"
        
        # Separator line
        csv += "-" * (sum(col_widths.values()) + len(headers) * 3) + "\n"
        
        # Data rows
        for trade in trades:
            alert_price = trade.get('underlying_alert_price', '')
            
            row_data = [
                trade['trade_id'],
                trade['symbol'][:24],  # Truncate long symbols
                trade['action'],
                str(trade['quantity']),
                trade['status'],
                f"{trade['entry_premium']:.2f}",
                f"{trade['entry_value']:.2f}",
                str(alert_price),
                f"{trade['current_premium']:.2f}",
                f"{trade['current_value']:.2f}",
                f"{trade['highest_premium']:.2f}",
                f"{trade.get('unrealized_pnl', 0):.2f}",
                f"{trade.get('unrealized_pnl_percent', 0):.2f}%"
            ]
            
            for i, value in enumerate(row_data):
                header = headers[i]
                csv += f"{str(value):<{col_widths[header]}} | "
            csv = csv.rstrip(" | ") + "\n"
        
        return csv
    
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
