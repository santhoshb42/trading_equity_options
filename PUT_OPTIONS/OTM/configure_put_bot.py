#!/usr/bin/env python3
"""
Configure PUT Options Bot from copied CE Options Bot
Modifies key files for PUT options trading
"""
import os
import re
from pathlib import Path

base_path = '/root/santhosh/trading/put_options'

print("🔧 Configuring PUT Options Bot...\n")

# 1. Update main.py - Change service name and endpoint
print("1️⃣ Updating main.py...")
main_file = f'{base_path}/main.py'
with open(main_file, 'r') as f:
    content = f.read()

# Replace service names
content = content.replace(
    'service_name = "Options Bot"',
    'service_name = "PUT Options Bot"'
)
content = content.replace(
    'service_name = "options"',
    'service_name = "put_options"'
)
content = content.replace(
    'OPTIONS_BOT',
    'PUT_OPTIONS_BOT'
)
content = content.replace(
    '/webhook/options',
    '/webhook/put_options'
)

with open(main_file, 'w') as f:
    f.write(content)
print("   ✅ main.py updated")

# 2. Update optconfig.py - Change mode and webhook endpoint
print("\n2️⃣ Updating optconfig.py...")
config_file = f'{base_path}/optcode/optconfig.py'
with open(config_file, 'r') as f:
    content = f.read()

content = content.replace(
    'ENDPOINT = "/webhook/options"',
    'ENDPOINT = "/webhook/put_options"'
)

with open(config_file, 'w') as f:
    f.write(content)
print("   ✅ optconfig.py updated")

# 3. Rename ce_extractor.py to pe_extractor.py
print("\n3️⃣ Renaming ce_extractor.py → pe_extractor.py...")
ce_file = f'{base_path}/optcode/ce_extractor.py'
pe_file = f'{base_path}/optcode/pe_extractor.py'
if os.path.exists(ce_file):
    os.rename(ce_file, pe_file)
    # Update references
    with open(pe_file, 'r') as f:
        content = f.read()
    content = content.replace('CE_EXTRACTOR', 'PE_EXTRACTOR')
    content = content.replace('ce_extractor', 'pe_extractor')
    content = content.replace('extract_ce_symbols', 'extract_pe_symbols')
    with open(pe_file, 'w') as f:
        f.write(content)
    print("   ✅ Renamed and updated pe_extractor.py")

# 4. Create PUT-specific directories
print("\n4️⃣ Creating PUT-specific directories...")
dirs = [
    f'{base_path}/data/put_options',
    f'{base_path}/logs/put_options',
    f'{base_path}/data/put_options/learning'
]
for dir_path in dirs:
    os.makedirs(dir_path, exist_ok=True)
    print(f"   ✅ Created {dir_path}")

# 5. Update all Python files to use PE instead of CE
print("\n5️⃣ Updating all Python files (CE → PE conversions)...")
py_files = list(Path(f'{base_path}/optcode').glob('*.py'))

replacements = [
    ('contract_type.*=.*["\']CE["\']', 'contract_type = "PE"'),
    ('contract_type.*==.*["\']CE["\']', 'contract_type == "PE"'),
    ('OPTIONS_BOT', 'PUT_OPTIONS_BOT'),
    ('options_bot', 'put_options_bot'),
    ('.options_bot.lock', '.put_options_bot.lock'),
    ('options_bot.pid', 'put_options_bot.pid'),
]

for py_file in py_files:
    try:
        with open(py_file, 'r') as f:
            content = f.read()
        
        # Update CE to PE references
        if 'ce_extractor' in content:
            content = content.replace('ce_extractor', 'pe_extractor')
        if 'from .ce_extractor' in content:
            content = content.replace('from .ce_extractor', 'from .pe_extractor')
            
        with open(py_file, 'w') as f:
            f.write(content)
    except Exception as e:
        print(f"   ⚠️ Error updating {py_file.name}: {e}")

print(f"   ✅ Updated {len(py_files)} Python files")

# 6. Update main.py imports
print("\n6️⃣ Updating main.py imports...")
with open(main_file, 'r') as f:
    content = f.read()

# Replace specific imports
if 'from optcode.ce_extractor' in content:
    content = content.replace('from optcode.ce_extractor', 'from optcode.pe_extractor')

with open(main_file, 'w') as f:
    f.write(content)
print("   ✅ main.py imports updated")

print("\n" + "="*60)
print("✅ PUT Options Bot Configuration Complete!")
print("="*60)
print("\n📋 Next Steps:")
print("   1. Review optcode/strike_selector.py - Change strike selection for PE")
print("   2. Review optcode/optmonitor.py - Reverse Greeks logic for PUT options")
print("   3. Review optcode/entry_filter_engine.py - Change entry signals (DOWN instead of UP)")
print("   4. Test with small capital first")
print("   5. Configure TradingView alerts for PUT signals")
print()
