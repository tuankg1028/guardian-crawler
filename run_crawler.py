#!/usr/bin/env python3

import subprocess
import sys
import os

def install_requirements():
    """Install required packages"""
    print("Installing requirements...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    
def install_playwright():
    """Install Playwright browsers"""
    print("Installing Playwright browsers...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install"])

def main():
    try:
        # Install requirements
        install_requirements()
        install_playwright()
        
        # Run the crawler
        print("\nStarting Guardian crawler...")
        print("This will collect posts from https://www.guardian.co.tt/ from 15 years ago to present")
        print("Press Ctrl+C to stop the crawler at any time\n")
        
        from guardian_crawler import main as crawler_main
        import asyncio
        
        asyncio.run(crawler_main())
        
    except KeyboardInterrupt:
        print("\nCrawler stopped by user")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()