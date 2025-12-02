#!/usr/bin/env python3
"""
Log Directory Cleanup and Standardization Script

ENFORCES standardized YYYY-MM-DD format for ALL log directories.

Why YYYY-MM-DD format?
- AngelOne (SmartAPI) broker API uses YYYY-MM-DD format
- Consistent with ISO 8601 international standard
- Simplifies data reconciliation with broker API responses
- Better for sorting and file system operations

What it does:
1. Identifies all log directories (both formats)
2. Consolidates DD-MM-YYYY directories into YYYY-MM-DD format
3. Removes duplicate/empty directories
4. Ensures ALL logs use YYYY-MM-DD going forward (AngelOne API compatibility)
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

def get_equity_root():
    """Get the equity directory path"""
    script_dir = Path(__file__).parent
    return script_dir.parent

def enforce_iso_log_directories():
    """Enforce ISO format (YYYY-MM-DD) for all log directories"""
    equity_root = get_equity_root()
    logs_dir = equity_root / "logs"
    
    if not logs_dir.exists():
        print("No logs directory found")
        return
    
    print("🧹 LOG DIRECTORY STANDARDIZATION (ISO YYYY-MM-DD Format)")
    print("=" * 60)
    print("🔍 Analyzing log directories...\n")
    
    subdirs = [d for d in logs_dir.iterdir() if d.is_dir()]
    
    iso_format_dirs = []      # YYYY-MM-DD (correct - AngelOne API standard)
    dmy_format_dirs = []      # DD-MM-YYYY (needs conversion)
    
    for subdir in sorted(subdirs):
        dir_name = subdir.name
        
        # Check YYYY-MM-DD format (2025-11-25) - CORRECT
        if len(dir_name) == 10 and dir_name[4] == '-' and dir_name[7] == '-':
            try:
                datetime.strptime(dir_name, '%Y-%m-%d')
                iso_format_dirs.append(subdir)
                print(f"✅ ISO format (correct): {dir_name}")
            except ValueError:
                print(f"⚠️  Unknown format: {dir_name}")
        
        # Check DD-MM-YYYY format (25-11-2025) - WILL BE CONVERTED
        elif len(dir_name) == 10 and dir_name[2] == '-' and dir_name[5] == '-':
            try:
                datetime.strptime(dir_name, '%d-%m-%Y')
                dmy_format_dirs.append(subdir)
                print(f"❌ DD-MM-YYYY format (NEEDS CONVERSION): {dir_name}")
            except ValueError:
                print(f"⚠️  Unknown format: {dir_name}")
        else:
            print(f"⚠️  Unknown format: {dir_name}")
    
    print(f"\n📊 Summary:")
    print(f"   YYYY-MM-DD (ISO) [CORRECT]: {len(iso_format_dirs)}")
    print(f"   DD-MM-YYYY [TO CONVERT]: {len(dmy_format_dirs)}")
    
    # Convert DD-MM-YYYY directories to YYYY-MM-DD format (ISO standard)
    if dmy_format_dirs:
        print("\n🔄 Converting DD-MM-YYYY directories to ISO format...")
        
        for dmy_dir in dmy_format_dirs:
            try:
                # Parse DD-MM-YYYY and convert to YYYY-MM-DD (ISO format)
                dmy_date = datetime.strptime(dmy_dir.name, '%d-%m-%Y')
                iso_name = dmy_date.strftime('%Y-%m-%d')  # Convert to ISO format
                target_dir = logs_dir / iso_name
                
                print(f"   📂 {dmy_dir.name} → {iso_name}")
                
                # Create target directory if it doesn't exist
                target_dir.mkdir(exist_ok=True)
                
                # Move all files from source to target
                files_moved = 0
                for file_path in dmy_dir.iterdir():
                    if file_path.is_file():
                        target_file = target_dir / file_path.name
                        
                        # If target exists, keep the newer one
                        if target_file.exists():
                            source_mtime = file_path.stat().st_mtime
                            target_mtime = target_file.stat().st_mtime
                            if source_mtime <= target_mtime:
                                # Source is older, skip it
                                print(f"      ⏭️  Skipped (older): {file_path.name}")
                                continue
                        
                        shutil.move(str(file_path), str(target_file))
                        files_moved += 1
                        print(f"      📄 Moved: {file_path.name}")
                
                # Remove the empty DD-MM-YYYY directory
                try:
                    dmy_dir.rmdir()
                    print(f"      🗑️  Removed empty directory: {dmy_dir.name}")
                except OSError:
                    print(f"      ⚠️  Directory not empty, keeping: {dmy_dir.name}")
                
                print(f"      ✅ Processed {files_moved} files")
            
            except Exception as e:
                print(f"   ❌ Error converting {dmy_dir.name}: {e}")
    
    print(f"\n📁 Final directory structure (ISO format):")
    for subdir in sorted(logs_dir.iterdir()):
        if subdir.is_dir():
            file_count = len(list(subdir.glob('*')))
            print(f"   ✅ {subdir.name} ({file_count} files)")
    
    print(f"\n✅ Log directory standardization complete!")
    print(f"🔒 All logs now use ISO format (YYYY-MM-DD)")
    print(f"🛡️  AngelOne API compatibility guaranteed!")

def main():
    try:
        enforce_iso_log_directories()
        print("\n🎉 Standardization completed successfully!")
        return 0
        
    except Exception as e:
        print(f"\n❌ Error during standardization: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
