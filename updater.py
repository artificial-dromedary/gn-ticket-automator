# updater.py - GitHub Auto-Update System for GN Ticket Automator

import requests
import json
import os
import sys
import shutil
import zipfile
import tempfile
import subprocess
import threading
import time
from pathlib import Path
from packaging import version
import tkinter as tk
from tkinter import ttk

# CONFIGURATION - Update these values for your releases:
APP_VERSION = "1.2.0"  # Update this with each release
GITHUB_REPO = "artificial-dromedary/gn-ticket-automator"  # Your GitHub repo
UPDATE_CHECK_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


class AppUpdater:
    def __init__(self):
        self.current_version = APP_VERSION
        self.app_path = self.get_app_path()

    def get_app_path(self):
        """Get the path to the current app bundle"""
        if getattr(sys, 'frozen', False):
            # Running as bundled app
            return Path(sys.executable).parents[2]  # Go up to .app folder
        else:
            # Running in development - return None
            return None

    def check_for_updates(self):
        """Check if a newer version is available on GitHub"""
        try:
            response = requests.get(UPDATE_CHECK_URL, timeout=10)
            response.raise_for_status()
            release_data = response.json()

            latest_version = release_data['tag_name'].lstrip('v')

            if version.parse(latest_version) > version.parse(self.current_version):
                download_url = self.get_download_url(release_data)
                if download_url:
                    return {
                        'available': True,
                        'version': latest_version,
                        'download_url': download_url,
                        'release_notes': release_data.get('body', ''),
                        'release_date': release_data.get('published_at', ''),
                        'release_url': release_data.get('html_url', '')
                    }

            return {'available': False}

        except Exception as e:
            print(f"Update check failed: {e}")
            return {'available': False, 'error': str(e)}

    def get_download_url(self, release_data):
        """Get the download URL for the Mac app"""
        print(f"Looking for DMG in release assets...")
        for asset in release_data.get('assets', []):
            name = asset['name'].lower()
            print(f"  Checking asset: {asset['name']}")

            # Look for DMG files with our app name pattern
            if name.endswith('.dmg'):
                # Accept any DMG that contains 'gn' or 'ticket' or 'automator'
                if 'gn' in name or 'ticket' in name or 'automator' in name:
                    print(f"  ✅ Found matching DMG: {asset['name']}")
                    return asset['browser_download_url']

                # Fallback: accept any DMG if it's the only one
                print(f"  Found DMG (fallback): {asset['name']}")
                return asset['browser_download_url']

        print("  ❌ No DMG file found in release assets")
        return None

    def _download_update_with_progress(self, download_url, progress_callback=None):
        """Internal method to download the update and report progress."""
        try:
            temp_dir = tempfile.gettempdir()
            temp_path = Path(temp_dir)

            if progress_callback:
                progress_callback("Starting download...", 5)
            time.sleep(0.5)

            if progress_callback:
                progress_callback("Downloading update...", 10)

            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()

            filename = download_url.split('/')[-1]
            download_path = temp_path / filename

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            with open(download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            percent = 10 + (downloaded / total_size) * 80  # 10-90%
                            mb_downloaded = downloaded / (1024 * 1024)
                            mb_total = total_size / (1024 * 1024)
                            progress_callback(f"Downloaded {mb_downloaded:.1f} MB of {mb_total:.1f} MB", percent)

            if progress_callback:
                progress_callback("Download complete!", 95)

            return download_path

        except Exception as e:
            if progress_callback:
                progress_callback(f"Download failed: {str(e)}", 0)
            raise

    def show_native_progress_window(self, queue):
        """Displays a simple Tkinter window to show update progress."""
        root = tk.Tk()
        root.title("Updating GN Ticket Automator")
        root.geometry("400x120")

        # Center the window
        window_width = 400
        window_height = 120
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x_cordinate = int((screen_width/2) - (window_width/2))
        y_cordinate = int((screen_height/2) - (window_height/2))
        root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")


        label = ttk.Label(root, text="Starting update...", padding=10)
        label.pack(fill='x', padx=10, pady=5)

        progress_bar = ttk.Progressbar(root, orient="horizontal", length=380, mode="determinate")
        progress_bar.pack(padx=10, pady=10)

        def check_queue():
            try:
                message, progress = queue.get_nowait()
                label.config(text=message)
                progress_bar['value'] = progress
                if progress >= 100:
                    # Once complete, the main app will exit and this window will be orphaned
                    # The helper script takes over from here.
                    label.config(text="Update complete. Relaunching...")
                    time.sleep(2)
                    root.destroy()
            except:
                pass
            root.after(100, check_queue)

        root.after(100, check_queue)
        root.mainloop()

    def prepare_and_launch_installer(self, download_url):
        """
        Downloads the update, creates a helper script to perform the installation,
        launches the script, and exits the application.
        """
        if not self.app_path:
            raise Exception("Cannot update in development mode")
        
        # Use a multiprocessing queue to communicate between the download thread and the UI process
        from multiprocessing import Process, Queue
        progress_queue = Queue()

        # Launch the native UI in a separate process
        ui_process = Process(target=self.show_native_progress_window, args=(progress_queue,))
        ui_process.start()

        def progress_callback(message, progress):
            progress_queue.put((message, progress))

        # Step 1: Download the DMG file
        dmg_path = self._download_update_with_progress(download_url, progress_callback)
        progress_callback("Creating installer script...", 98)

        # Step 2: Create the installer helper script
        script_path = self.create_installer_script(dmg_path, self.app_path)
        if not script_path:
            raise Exception("Failed to create the installer script.")

        if progress_callback:
            progress_callback("Handing off to installer...", 100)

        time.sleep(1)  # Give UI time to update

        # Step 3: Launch the script as a separate process
        print(f"Launching installer script: {script_path}")
        subprocess.Popen(['/bin/bash', script_path], start_new_session=True)

        # Step 4: Exit the current application
        print("Main application exiting to allow update.")
        os._exit(0)

    def create_installer_script(self, dmg_path, app_path):
        """Creates a bash script to handle the update process."""
        try:
            # --- NEW: Define a dedicated log file for the updater ---
            log_dir = Path.home() / 'Library' / 'Logs'
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / 'GN_Ticket_Automator_Updater.log'

            script_dir = Path(tempfile.gettempdir())
            script_path = script_dir / 'install_update.sh'

            # Get the process ID of the current app to wait for it to close
            app_pid = os.getpid()

            # --- MODIFIED SCRIPT CONTENT ---
            # We will add logging to every command to debug the update process
            script_content = f"""#!/bin/bash
# Installer script for GN Ticket Automator - DO NOT RUN MANUALLY

# --- Log Setup ---
LOG_FILE="{log_file}"
echo "--- GN Ticket Automator Updater ---" > "$LOG_FILE"
echo "Installer started at $(date)" >> "$LOG_FILE"
exec >> "$LOG_FILE" 2>&1 # Redirect all subsequent output to the log file

# --- Configuration ---
DMG_PATH="{dmg_path}"
APP_PATH="{app_path}"
APP_PID={app_pid}

# --- Wait for the main application to close ---
echo "Waiting for main application (PID: $APP_PID) to close..."
while ps -p $APP_PID > /dev/null; do
    echo "  - App still running, waiting 1s..."
    sleep 1
done
echo "Application has closed."
sleep 2

# --- Mount the DMG ---
echo "Mounting update DMG: $DMG_PATH"
MOUNT_OUTPUT=$(hdiutil attach -nobrowse -noverify -noautoopen "$DMG_PATH")
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to mount DMG. Output: $MOUNT_OUTPUT"
    exit 1
fi
MOUNT_POINT=$(echo "$MOUNT_OUTPUT" | grep "/Volumes/" | awk '{{print $3}}' | head -n 1)

if [ -z "$MOUNT_POINT" ]; then
    echo "ERROR: Could not find mount point in output: $MOUNT_OUTPUT"
    exit 1
fi
echo "DMG mounted at: $MOUNT_POINT"

# --- Backup user profile data ---
PROFILE_DIR="$HOME/GN_Ticket_Automator"
PROFILE_DB="$PROFILE_DIR/user_profiles.db"
BACKUP_DB="/tmp/user_profiles.db.bak"

if [ -f "$PROFILE_DB" ]; then
    echo "Backing up user profiles..."
    cp "$PROFILE_DB" "$BACKUP_DB"
else
    echo "No user profile data found to back up."
fi

# --- Find the .app bundle in the DMG ---
SOURCE_APP=$(find "$MOUNT_POINT" -name "*.app" -maxdepth 1 | head -n 1)
if [ -z "$SOURCE_APP" ]; then
    echo "ERROR: Could not find .app file in the DMG at path $MOUNT_POINT."
    ls -la "$MOUNT_POINT" # List contents for debugging
    hdiutil detach "$MOUNT_POINT"
    exit 1
fi
echo "Found new application at: $SOURCE_APP"

# --- Install the update ---
echo "Attempting to remove old application at: $APP_PATH"
rm -rf "$APP_PATH"
if [ $? -ne 0 ]; then
    echo "WARNING: Could not remove old application. This may be a permissions issue."
fi
sleep 1

echo "Copying new application to $APP_PATH"
cp -R "$SOURCE_APP" "$APP_PATH"
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to copy new application."
    if [ -f "$BACKUP_DB" ]; then
        mv "$BACKUP_DB" "$PROFILE_DB"
        echo "Restored user profiles after failed update."
    fi
    hdiutil detach "$MOUNT_POINT"
    exit 1
fi
echo "Application copied successfully."

# --- Restore user profile data ---
if [ -f "$BACKUP_DB" ]; then
    echo "Restoring user profiles..."
    mkdir -p "$PROFILE_DIR"
    mv "$BACKUP_DB" "$PROFILE_DB"
fi

# --- CRITICAL: Remove quarantine attribute ---
echo "Removing quarantine attributes..."
xattr -cr "$APP_PATH"
echo "Quarantine attributes removed."

# --- Cleanup ---
echo "Cleaning up..."
hdiutil detach "$MOUNT_POINT"
rm -f "$DMG_PATH"
# We don't remove the script itself until the very end

# --- Relaunch the application ---
echo "Update complete. Relaunching application..."
open "$APP_PATH"

echo "Installer finished successfully. Cleaning up script."
rm -- "$0"

exit 0
"""
            with open(script_path, 'w') as f:
                f.write(script_content)

            os.chmod(script_path, 0o755)
            return str(script_path)

        except Exception as e:
            print(f"Error creating installer script: {e}")
            return None
