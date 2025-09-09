#!/usr/bin/env python3
"""
Robust Launcher script for GN Ticket Automator
Automatically sets up .env file on first run and checks for updates
"""

import os
import sys
import webbrowser
import time
import threading
import socket
from pathlib import Path
import traceback
import shutil


def ensure_env_file():
    """Ensure .env file exists in user directory, copy from bundle if needed"""
    user_dir = Path.home() / 'GN_Ticket_Automator'
    user_dir.mkdir(exist_ok=True)

    user_env = user_dir / '.env'

    # If .env already exists in user directory, we're good
    if user_env.exists():
        print(f"✅ Found existing .env at: {user_env}")
        return True

    print(f"📋 First run detected - setting up configuration...")

    # Create the default .env content with the new Desktop OAuth setup
    default_env_content = """# GN Ticket Automator Configuration
# This file contains safe configuration values that can be distributed
# No secrets are stored here - Desktop apps don't need client secrets

# Google OAuth Settings (Desktop App)
GOOGLE_CLIENT_ID=156593059325-2gq1fbm28pdrarvci2fpcse06p1ohh86.apps.googleusercontent.com
# Note: No client secret needed for desktop apps - Google handles this securely

# Allowed email domains
ALLOWED_DOMAINS=takingitglobal.org
"""

    # Try to find .env in various locations
    possible_sources = []

    if getattr(sys, 'frozen', False):
        # Running as bundled app
        if hasattr(sys, '_MEIPASS'):
            # Try _MEIPASS directory (where PyInstaller puts data files)
            possible_sources.append(Path(sys._MEIPASS) / '.env')

            # Also check if it's in the base directory of _MEIPASS
            # PyInstaller sometimes puts files here with --add-data=".env:."
            meipass_base = Path(sys._MEIPASS)
            if meipass_base.exists():
                # List all .env files in _MEIPASS for debugging
                env_files = list(meipass_base.glob('.env*'))
                for env_file in env_files:
                    print(f"  Found potential env file: {env_file}")
                    possible_sources.append(env_file)

        # Try the executable directory itself
        executable_dir = Path(sys.executable).parent
        possible_sources.append(executable_dir / '.env')

        # Try the Resources directory (macOS app bundle structure)
        possible_sources.append(executable_dir.parent / 'Resources' / '.env')

        # Try Frameworks directory (where PyInstaller often puts things)
        possible_sources.append(executable_dir.parent / 'Frameworks' / '.env')
    else:
        # Running in development
        possible_sources.append(Path(__file__).parent / '.env')

    # Try each possible source
    for source_env in possible_sources:
        if source_env and source_env.exists():
            print(f"✅ Found .env template at: {source_env}")
            print(f"📋 Copying to: {user_env}")
            try:
                # Read the source file
                source_content = source_env.read_text()

                # If it's an old .env with CLIENT_SECRET, use the new format instead
                if 'GOOGLE_CLIENT_SECRET' in source_content:
                    print(f"📝 Updating to new Desktop OAuth format (no client secret needed)")
                    user_env.write_text(default_env_content)
                else:
                    # Use the source as-is
                    user_env.write_text(source_content)

                print(f"✅ Configuration file created successfully!")
                return True
            except Exception as e:
                print(f"⚠️ Error copying .env: {e}")

    # If we couldn't find a .env file anywhere, create the new format
    print(f"📝 Creating new Desktop OAuth configuration...")

    try:
        user_env.write_text(default_env_content)
        print(f"✅ Created .env file with Desktop OAuth configuration")
        return True

    except Exception as e:
        print(f"❌ Failed to create .env file: {e}")
        return False


def setup_logging():
    """Setup logging for bundled app"""
    if getattr(sys, 'frozen', False):
        # Running as bundled app - create log files
        app_dir = Path.home() / 'GN_Ticket_Automator'
        app_dir.mkdir(exist_ok=True)

        log_file = app_dir / 'app.log'
        error_file = app_dir / 'error.log'

        # Create or clear log files
        try:
            with open(log_file, 'w') as f:
                f.write(f"Starting at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            with open(error_file, 'w') as f:
                f.write(f"Error log started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception as e:
            print(f"Could not create log files: {e}")

        # Redirect stdout and stderr to log files
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = open(log_file, 'a', buffering=1)
        sys.stderr = open(error_file, 'a', buffering=1)

        print(f"🚀 GN Ticket Automator starting at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"App directory: {app_dir}")
        print(f"Python executable: {sys.executable}")
        print(f"Working directory: {os.getcwd()}")


def get_app_data_dir():
    """Get the application data directory"""
    if getattr(sys, 'frozen', False):
        app_dir = Path.home() / 'GN_Ticket_Automator'
    else:
        app_dir = Path(__file__).parent

    app_dir.mkdir(exist_ok=True)
    return app_dir


def check_chrome_installed():
    """Check if Chrome is installed"""
    chrome_paths = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Google Chrome.app',
        '/Applications/Chromium.app',
    ]

    print("🔍 Checking for Chrome installation...")
    for path in chrome_paths:
        if os.path.exists(path):
            print(f"✅ Found Chrome at: {path}")
            return True

    print("⚠️ Chrome not found - some features may not work")
    return False


def find_available_port(start_port=5000):
    """Find an available port starting from start_port"""
    print(f"🔍 Looking for available port starting from {start_port}")
    for port in range(start_port, start_port + 50):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.close()
            print(f"✅ Found available port: {port}")
            return port
        except OSError:
            continue

    print("⚠️ Using fallback port 5000")
    return 5000


def copy_resources_if_needed():
    """Copy templates and static files to user directory if they're missing"""
    user_dir = Path.home() / 'GN_Ticket_Automator'

    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Check if templates exist
        user_templates = user_dir / 'templates'
        bundle_templates = Path(sys._MEIPASS) / 'templates'

        if not user_templates.exists() and bundle_templates.exists():
            print(f"📋 Copying templates to user directory...")
            shutil.copytree(bundle_templates, user_templates)
            print(f"✅ Templates copied")

        # Check if static files exist
        user_static = user_dir / 'static'
        bundle_static = Path(sys._MEIPASS) / 'static'

        if not user_static.exists() and bundle_static.exists():
            print(f"📋 Copying static files to user directory...")
            shutil.copytree(bundle_static, user_static)
            print(f"✅ Static files copied")


def run_simple_interface():
    """Fallback: run with simple browser-only interface"""
    print("🌐 Starting simple browser interface...")

    try:
        # Import and start Flask directly
        from main import app

        # Find available port
        port = find_available_port()

        print(f"🌐 Starting Flask server on port {port}...")

        # Auto-open browser after delay
        def open_browser():
            time.sleep(3)
            url = f'http://127.0.0.1:{port}'
            print(f"🌐 Opening browser to: {url}")
            webbrowser.open(url)

        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()

        # Start Flask app
        app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)

    except Exception as e:
        print(f"❌ Failed to start simple interface: {e}")
        traceback.print_exc()


def main():
    """Main launcher function"""
    try:
        # Setup logging first
        setup_logging()

        print("=" * 60)
        print("🚀 GN TICKET AUTOMATOR LAUNCHER")
        print("=" * 60)

        # CRITICAL: Ensure .env file exists before anything else
        print("\n📋 Checking configuration...")
        ensure_env_file()

        # Setup app directory
        app_data_dir = get_app_data_dir()
        print(f"\n📁 App data directory: {app_data_dir}")

        # Change to app directory
        try:
            os.chdir(app_data_dir)
            print(f"✅ Changed to app directory")
        except Exception as e:
            print(f"⚠️ Could not change directory: {e}")

        # Copy other resources if needed (templates, static files)
        copy_resources_if_needed()

        # Check Chrome installation (warn but don't fail)
        chrome_ok = check_chrome_installed()

        # Adjust path for bundled app
        if getattr(sys, 'frozen', False):
            bundle_dir = os.path.dirname(sys.executable)
            print(f"\n📦 Bundle directory: {bundle_dir}")
            if bundle_dir not in sys.path:
                sys.path.insert(0, bundle_dir)
                print("✅ Added bundle directory to Python path")

            # Also add the _MEIPASS directory if it exists
            if hasattr(sys, '_MEIPASS'):
                meipass_dir = sys._MEIPASS
                print(f"📦 MEIPASS directory: {meipass_dir}")
                if meipass_dir not in sys.path:
                    sys.path.insert(0, meipass_dir)

        # Quick update check (non-blocking)
        try:
            print("\n🔍 Checking for updates...")
            from updater import AppUpdater
            updater = AppUpdater()
            update_info = updater.check_for_updates()

            if update_info.get('available'):
                print(f"")
                print("=" * 60)
                print(f"🎉 UPDATE AVAILABLE: Version {update_info.get('version')}")
                print(f"   Current version: {updater.current_version}")
                print(f"   Go to the web interface and click 'Check Updates'")
                print("=" * 60)
                print("")
        except Exception as e:
            # Don't block startup if update check fails
            print(f"⚠️ Could not check for updates: {e}")
            pass

        # Try different interface options in order of preference
        interface_options = [
            ("🖥  Native Window Interface", "native_window_simple"),
            ("🌐 Simple Browser Interface", "simple")
        ]

        for interface_name, interface_type in interface_options:
            try:
                print(f"\n🚀 Attempting to start {interface_name}...")

                if interface_type == "native_window_simple":
                    # Try the robust native window
                    import native_window_simple
                    print("✅ Successfully imported native window module")
                    native_window_simple.run_app_with_native_window()
                    break  # If we get here, it worked

                elif interface_type == "simple":
                    # Fallback to simple browser interface
                    run_simple_interface()
                    break

            except ImportError as e:
                print(f"❌ Could not import {interface_type}: {e}")
                continue
            except Exception as e:
                print(f"❌ {interface_name} failed: {e}")
                traceback.print_exc()
                continue

        else:
            # If all interfaces failed
            print("\n❌ All interface options failed!")
            print("Check the error log at: ~/GN_Ticket_Automator/error.log")

            # On macOS, show an error dialog
            if sys.platform == 'darwin':
                try:
                    os.system(
                        """osascript -e 'display dialog "GN Ticket Automator failed to start. Please check the error log at ~/GN_Ticket_Automator/error.log" with title "Launch Error" buttons {"OK"} default button 1' """)
                except:
                    pass

            print("⏳ Waiting 15 seconds before exit...")
            time.sleep(15)

    except KeyboardInterrupt:
        print("\n🛑 Received keyboard interrupt")
        print("👋 Shutting down gracefully...")

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print("📋 Full error details:")
        traceback.print_exc()
        print("⏳ Waiting 15 seconds before exit...")
        time.sleep(15)

    finally:
        print("\n📚 App launcher finished")
        # Flush logs
        if hasattr(sys.stdout, 'flush'):
            sys.stdout.flush()
        if hasattr(sys.stderr, 'flush'):
            sys.stderr.flush()


if __name__ == '__main__':
    main()