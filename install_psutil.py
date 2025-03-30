#!/usr/bin/env python
"""
Simple script to install the psutil package for memory monitoring.
"""
import subprocess
import sys

def install_psutil():
    """Install psutil package."""
    print("Installing psutil package for memory monitoring...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil>=5.9.0"])
        print("Successfully installed psutil!")
    except subprocess.CalledProcessError as e:
        print(f"Error installing psutil: {e}")
        return False
    
    # Verify installation
    try:
        import psutil
        print(f"Verified psutil installation. Version: {psutil.__version__}")
        return True
    except ImportError:
        print("Failed to import psutil after installation.")
        return False

if __name__ == "__main__":
    success = install_psutil()
    if success:
        print("You can now run the application with memory monitoring enabled.")
    else:
        print("Memory monitoring will be limited without psutil.") 