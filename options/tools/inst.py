"""
Download latest instrument.json from Angel Broking API
Run this periodically to keep instrument data fresh
"""
import requests
import json
from pathlib import Path

def download_instruments(output_path=None):
    """
    Download latest instruments from Angel Broking
    
    Args:
        output_path: Path to save instrument.json (default: current directory)
    
    Returns:
        True if successful, False otherwise
    """
    if output_path is None:
        output_path = Path(__file__).parent / "instrument.json"
    else:
        output_path = Path(output_path)
    
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    
    print(f"📥 Downloading instruments from Angel Broking...")
    
    try:
        r = requests.get(url, timeout=30)
        
        if r.status_code == 200:
            # Validate JSON
            data = r.json()
            print(f"✅ Downloaded {len(data)} instruments")
            
            # Save to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w') as f:
                json.dump(data, f)
            
            print(f"✅ Saved to {output_path}")
            
            # Print summary
            if isinstance(data, list):
                fo_stocks = [item for item in data if item.get('instrumenttype') == 'OPTSTK']
                print(f"   Total instruments: {len(data)}")
                print(f"   F&O option stocks: {len(fo_stocks)}")
            
            return True
        else:
            print(f"❌ Failed to download! Status: {r.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

if __name__ == "__main__":
    download_instruments()
