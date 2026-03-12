"""
Privy Token Auto-Refresh via Playwright Persistent Browser

Keeps a browser session alive with Definitive open.
Privy's JS SDK auto-refreshes tokens. We intercept and save them.

Usage:
    # First run (manual login required):
    python auth/privy_session.py --setup
    
    # Subsequent runs (automated, runs in background):
    python auth/privy_session.py

Tokens are written to auth/tokens.json and optionally synced to .env
"""

import asyncio
import json
import os
import sys
import time
import base64
from pathlib import Path
from datetime import datetime, timedelta

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

DEFINITIVE_URL = "https://app.definitive.fi"
USER_DATA_DIR = Path(__file__).parent / "browser_data"
TOKENS_FILE = Path(__file__).parent / "tokens.json"
ENV_FILE = Path(__file__).parent.parent / ".env"

# Refresh 10 minutes before expiry
REFRESH_BUFFER_SECONDS = 600


def decode_jwt_exp(token: str) -> int:
    """Extract expiry timestamp from JWT without verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return 0
        payload = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        return data.get("exp", 0)
    except Exception:
        return 0


def tokens_need_refresh(tokens: dict) -> bool:
    """Check if tokens are expired or about to expire."""
    access_token = tokens.get("access_token", "")
    if not access_token:
        return True
    
    exp = decode_jwt_exp(access_token)
    if exp == 0:
        return True
    
    # Refresh if less than buffer time remaining
    time_remaining = exp - time.time()
    return time_remaining < REFRESH_BUFFER_SECONDS


def save_tokens(access_token: str, id_token: str):
    """Save tokens to JSON file and sync to .env"""
    tokens = {
        "access_token": access_token,
        "id_token": id_token,
        "updated_at": datetime.now().isoformat(),
        "access_exp": decode_jwt_exp(access_token),
        "id_exp": decode_jwt_exp(id_token),
    }
    
    # Save to JSON
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    
    # Sync to .env
    sync_to_env(access_token, id_token)
    
    exp_time = datetime.fromtimestamp(tokens["access_exp"])
    print(f"[PRIVY] Tokens saved. Access expires: {exp_time.strftime('%H:%M:%S')}")


def sync_to_env(access_token: str, id_token: str):
    """Update .env file with new tokens."""
    if not ENV_FILE.exists():
        return
    
    content = ENV_FILE.read_text()
    lines = content.split("\n")
    new_lines = []
    
    for line in lines:
        if line.startswith("PRIVY_ACCESS_TOKEN="):
            new_lines.append(f"PRIVY_ACCESS_TOKEN={access_token}")
        elif line.startswith("PRIVY_ID_TOKEN="):
            new_lines.append(f"PRIVY_ID_TOKEN={id_token}")
        else:
            new_lines.append(line)
    
    ENV_FILE.write_text("\n".join(new_lines))


def load_tokens() -> dict:
    """Load tokens from JSON file."""
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE) as f:
            return json.load(f)
    return {}


async def run_session(headless: bool = False):
    """Run the persistent browser session."""
    from playwright.async_api import async_playwright
    
    print(f"[PRIVY] Starting browser session...")
    print(f"[PRIVY] User data: {USER_DATA_DIR}")
    print(f"[PRIVY] Headless: {headless}")
    
    async with async_playwright() as p:
        # Launch with persistent context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 800},
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        # Track tokens
        current_tokens = {"access": None, "id": None}
        
        async def extract_tokens_from_cookies():
            """Extract tokens from browser cookies - works even without network calls."""
            try:
                cookies = await context.cookies()
                access = None
                id_tok = None
                for cookie in cookies:
                    if cookie["name"] == "privy-token":
                        access = cookie["value"]
                    elif cookie["name"] == "privy-id-token":
                        id_tok = cookie["value"]
                
                if access and id_tok:
                    # Check if different from what we have
                    if access != current_tokens["access"]:
                        current_tokens["access"] = access
                        current_tokens["id"] = id_tok
                        save_tokens(access, id_tok)
                        print(f"[PRIVY] Tokens extracted from cookies!")
                        return True
            except Exception as e:
                print(f"[PRIVY] Cookie extraction error: {e}")
            return False
        
        # Intercept Privy API responses
        async def handle_response(response):
            try:
                url = response.url
                if "privy.io" in url and response.status == 200:
                    try:
                        data = await response.json()
                        
                        # Check for token in response
                        access_token = data.get("token")
                        id_token = data.get("identity_token")
                        
                        if access_token and id_token:
                            if access_token != current_tokens["access"]:
                                current_tokens["access"] = access_token
                                current_tokens["id"] = id_token
                                save_tokens(access_token, id_token)
                                print(f"[PRIVY] New tokens from network!")
                    except:
                        pass  # Not JSON or parse error
            except Exception as e:
                pass  # Ignore errors in response handling
        
        page.on("response", handle_response)
        
        # Navigate to Definitive
        print(f"[PRIVY] Navigating to {DEFINITIVE_URL}...")
        await page.goto(DEFINITIVE_URL, wait_until="networkidle")
        
        # Check if logged in by looking for wallet connect button vs portfolio
        await asyncio.sleep(3)
        
        # Check current state
        content = await page.content()
        is_logged_in = "Portfolio" in content or "portfolio" in content.lower()
        
        if not is_logged_in and not headless:
            print("\n" + "="*60)
            print("[PRIVY] Please log in via MetaMask in the browser window.")
            print("[PRIVY] Once logged in, tokens will be captured automatically.")
            print("="*60 + "\n")
        elif is_logged_in:
            print("[PRIVY] Already logged in!")
            # Get initial tokens from cookies
            await extract_tokens_from_cookies()
        
        # Main loop - keep session alive and refresh proactively
        print("[PRIVY] Monitoring for token refreshes... (Ctrl+C to stop)")
        
        last_check = time.time()
        last_log = 0
        
        while True:
            try:
                now = time.time()
                
                # Load current token expiry
                tokens = load_tokens()
                exp = tokens.get("access_exp", 0)
                time_remaining = exp - now if exp > 0 else 0
                
                # Determine check interval based on urgency
                if time_remaining < 300:  # < 5 min: check every 30s, urgent
                    check_interval = 30
                    await asyncio.sleep(30)
                elif time_remaining < 900:  # < 15 min: check every 60s
                    check_interval = 60
                    await asyncio.sleep(60)
                else:  # Comfortable buffer: check every 5 min
                    check_interval = 300
                    await asyncio.sleep(120)
                
                now = time.time()
                
                # Periodic token extraction from cookies
                if now - last_check > check_interval:
                    last_check = now
                    await extract_tokens_from_cookies()
                    
                    # Reload tokens and check if refresh needed
                    tokens = load_tokens()
                    if tokens_need_refresh(tokens):
                        exp = tokens.get("access_exp", 0)
                        remaining = exp - now
                        print(f"[PRIVY] Token expiring in {remaining:.0f}s, forcing page refresh...")
                        
                        # Multiple refresh attempts if needed
                        for attempt in range(3):
                            await page.reload(wait_until="networkidle")
                            await asyncio.sleep(3)
                            if await extract_tokens_from_cookies():
                                # Check if we got fresh tokens
                                new_tokens = load_tokens()
                                new_exp = new_tokens.get("access_exp", 0)
                                if new_exp > exp:
                                    print(f"[PRIVY] Token refresh SUCCESS! New expiry: {datetime.fromtimestamp(new_exp).strftime('%H:%M:%S')}")
                                    break
                            if attempt < 2:
                                print(f"[PRIVY] Refresh attempt {attempt+1} - retrying...")
                                await asyncio.sleep(5)
                
                # Status log every 10 min
                if now - last_log > 600:
                    last_log = now
                    tokens = load_tokens()
                    exp = tokens.get("access_exp", 0)
                    remaining = exp - now
                    if remaining > 0:
                        print(f"[PRIVY] Token valid for {remaining/60:.0f} min (expires {datetime.fromtimestamp(exp).strftime('%H:%M:%S')})")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[PRIVY] Error: {e}")
                await asyncio.sleep(30)
        
        await context.close()


async def setup_session():
    """First-time setup - opens visible browser for manual login."""
    print("\n" + "="*60)
    print("PRIVY SESSION SETUP")
    print("="*60)
    print("\n1. A browser window will open")
    print("2. Navigate to Definitive and log in with MetaMask")
    print("3. Once logged in, tokens will be captured")
    print("4. Press Ctrl+C when done to save and exit")
    print("\n" + "="*60 + "\n")
    
    await run_session(headless=False)


async def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Privy Token Auto-Refresh")
    parser.add_argument("--setup", action="store_true", help="First-time setup with visible browser")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    args = parser.parse_args()
    
    if args.setup:
        await setup_session()
    else:
        # Check if we have existing session
        if not USER_DATA_DIR.exists():
            print("[PRIVY] No existing session. Run with --setup first.")
            return
        
        # Run in background mode
        headless = args.headless
        await run_session(headless=headless)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[PRIVY] Session ended.")
