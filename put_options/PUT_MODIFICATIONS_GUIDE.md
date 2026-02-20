# PUT Options Bot - Modification Guide

## Key Changes Required:

### 1. **Contract Type**
- Change all `CE` → `PE`
- Change all `CALL` → `PUT`

### 2. **Greeks Logic**
- **Call**: Delta increases when underlying goes UP → SL -10% below entry
- **Put**: Delta increases when underlying goes DOWN → SL -10% ABOVE entry (opposite direction)

### 3. **Entry Signals**
- **Calls**: Long when price goes UP
- **Puts**: Long when price goes DOWN

### 4. **Position Direction**
- Calls: Profit when underlying rises
- Puts: Profit when underlying falls

### 5. **Strike Selection**
- Calls: Select OTM strikes above underlying (higher strike)
- Puts: Select OTM strikes below underlying (lower strike)

### 6. **Configuration Changes**
- Mode: `put_options` instead of `options`
- Service port: Different (8082 instead of 8081)
- Log folder: `logs/put_options` directory
- Data folder: `data/put_options` directory

## Files to Modify:
1. optcode/optconfig.py - Update configuration
2. optcode/ce_extractor.py → pe_extractor.py - Symbol extraction
3. optcode/strike_selector.py - Strike selection logic
4. optcode/optmonitor.py - Position monitoring (Greeks logic reversed)
5. optcode/entry_filter_engine.py - Entry signal logic
6. main.py - Bot initialization
7. requirements.txt - Ensure PE support

## Status:
[ ] optconfig.py - Configuration
[ ] strike_selector.py - Strike selection
[ ] optmonitor.py - Position Greeks & SL logic
[ ] entry_filter_engine.py - Entry signals
[ ] ce_extractor.py → pe_extractor.py
[ ] main.py - Bot mode
[ ] Log/Data directories - Create PE folders
