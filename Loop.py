import time
import subprocess
import sys
from datetime import datetime

# Configuration
num_loops = 1
interval = 30  # seconds

def run_collector():
    """Run the PSX collector script and handle errors."""
    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Zephyrris.py...")
        
        # Use sys.executable to ensure correct Python interpreter
        result = subprocess.run(
            [sys.executable, "Zephyrris.py"],
            capture_output=True,
            text=True,
            timeout=300  # 5-minute timeout to prevent hanging
        )
        
        if result.returncode == 0:
            print(f"✓ Completed successfully")
            if result.stdout:
                print(result.stdout)
        else:
            print(f"✗ Exit code: {result.returncode}")
            if result.stderr:
                print(f"Error output: {result.stderr}")
            if result.stdout:
                print(f"Standard output: {result.stdout}")
        
        return result.returncode
        
    except subprocess.TimeoutExpired:
        print(f"✗ Script timed out after 5 minutes")
        return -1
    except Exception as e:
        print(f"✗ Error running script: {e}")
        import traceback
        traceback.print_exc()
        return -1

# Main loop
if __name__ == "__main__":
    print(f"Starting PSX Auto Loop - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Configuration: {num_loops} loops with {interval}s interval\n")
    
    for i in range(num_loops):
        print(f"\n{'='*50}")
        print(f"Run {i+1}/{num_loops}")
        print(f"{'='*50}")
        
        run_collector()
        
        # Wait before next run
        if i < num_loops - 1:
            print(f"\nWaiting {interval} seconds until next run...")
            time.sleep(interval)

    print(f"\n{'='*50}")
    print(f"All {num_loops} runs completed!")
    print(f"{'='*50}")
