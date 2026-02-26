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
from pathlib import Path
from packaging import version

# CONFIGURATION - Update these values for your releases:
APP_VERSION = "1.0.3"  # Update this with each release
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
        for asset in release_data.get('assets', []):
            name = asset['name'].lower()
            if name.endswith('.dmg') and 'mac' in name:
                return asset['browser_download_url']
            elif name.endswith('.dmg'):
                return asset['browser_download_url']
        return None
    
    def download_and_install_update(self, download_url, progress_callback=None):
        """Download and install the update"""
        if not self.app_path:
            raise Exception("Cannot update in development mode")
            
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                if progress_callback:
                    progress_callback("Downloading update...", 10)
                
                # Download the update
                response = requests.get(download_url, stream=True)
                response.raise_for_status()
                
                filename = download_url.split('/')[-1]
                download_path = temp_path / filename
                
                # Download with progress
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                with open(download_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total_size > 0:
                                percent = 10 + (downloaded / total_size) * 60  # 10-70%
                                progress_callback(f"Downloading... {percent:.0f}%", percent)
                
                if progress_callback:
                    progress_callback("Installing update...", 80)
                
                # Install the DMG
                return self.install_from_dmg(download_path, progress_callback)
                    
        except Exception as e:
            if progress_callback:
                progress_callback(f"Update failed: {str(e)}", 0)
            raise
    
    def install_from_dmg(self, dmg_path, progress_callback=None):
        """Install update from DMG file"""
        try:
            if progress_callback:
                progress_callback("Mounting disk image...", 85)
            
            # Mount the DMG
            mount_result = subprocess.run(['hdiutil', 'attach', str(dmg_path), '-nobrowse'], 
                                        capture_output=True, text=True, check=True)
            
            # Find mount point from hdiutil output
            mount_point = None
            for line in mount_result.stdout.split('\n'):
                if '/Volumes/' in line:
                    mount_point = Path(line.split('\t')[-1].strip())
                    break
            
            if not mount_point:
                raise Exception("Could not find mount point")
            
            # Find the app in the mounted volume
            app_source = mount_point / "GN_Ticket_Automator.app"
            if not app_source.exists():
                # Try to find any .app file
                app_files = list(mount_point.glob("*.app"))
                if app_files:
                    app_source = app_files[0]
                else:
                    raise Exception("App not found in disk image")
            
            if progress_callback:
                progress_callback("Backing up current version...", 90)
            
            # Backup current app
            backup_path = self.app_path.parent / f"{self.app_path.name}.backup"
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.move(str(self.app_path), str(backup_path))
            
            if progress_callback:
                progress_callback("Installing new version...", 95)
            
            # Copy new app
            shutil.copytree(str(app_source), str(self.app_path))
            
            # Unmount DMG
            subprocess.run(['hdiutil', 'detach', str(mount_point)], check=True)
            
            if progress_callback:
                progress_callback("Update complete! Restart to use new version.", 100)
            
            return True
            
        except Exception as e:
            # Try to restore backup if something went wrong
            try:
                if 'backup_path' in locals() and backup_path.exists() and not self.app_path.exists():
                    shutil.move(str(backup_path), str(self.app_path))
            except:
                pass
            raise e