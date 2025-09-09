#!/bin/bash
# Final robust build script - MODIFIED to include Fixer App in a custom DMG

# --- PRE-BUILD CHECKS ---

# Check if the 'create-dmg' tool is installed
if ! command -v create-dmg &> /dev/null
then
    echo "❌ 'create-dmg' command not found."
    echo "   This script requires it to build a custom installer."
    echo "   Please install it by running: pip install create-dmg"
    exit 1
fi
echo "✅ create-dmg tool is available."

# Extract version from updater.py
echo "🔍 Reading version from updater.py..."
VERSION=$(python3 -c "
import sys
sys.path.insert(0, '.')
from updater import APP_VERSION
print(APP_VERSION)
")

if [ -z "$VERSION" ]; then
    echo "❌ Could not read version from updater.py"
    exit 1
fi

echo "📦 Building GN Ticket Automator version: $VERSION"

# Make sure .env file exists
if [ ! -f ".env" ]; then
    echo "❌ ERROR: .env file not found!"
    echo "   Please create .env file"
    exit 1
fi
echo "✅ Found .env file"

echo "✅ Ready to build"

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf build dist *.spec dmg_staging

# Check for icon file
ICON_ARG=""
if [ -f "icon.icns" ]; then
    echo "✅ Found icon.icns"
    ICON_ARG="--icon=icon.icns"
else
    echo "ℹ️  No icon file found - building without custom icon"
fi

# Build the app with PyInstaller
echo "🔨 Building app bundle (version $VERSION)..."
pyinstaller --onedir \
    --windowed \
    --name="GN_Ticket_Automator" \
    $ICON_ARG \
    --osx-bundle-identifier="org.takingitglobal.gn-ticket-automator" \
    --add-data="templates:templates" \
    --add-data="static:static" \
    --add-data=".env:." \
    --add-data="native_window_simple.py:." \
    --hidden-import="google.auth.transport.requests" \
    --hidden-import="googleapiclient" \
    --hidden-import="google_auth_oauthlib" \
    --hidden-import="cryptography" \
    --hidden-import="keyring" \
    --hidden-import="keyring.backends" \
    --hidden-import="tkinter" \
    --hidden-import="tkinter.ttk" \
    --hidden-import="tkinter.messagebox" \
    --hidden-import="werkzeug.serving" \
    --hidden-import="flask_session" \
    --collect-submodules=google \
    --collect-submodules=googleapiclient \
    --collect-submodules=keyring \
    app_launcher.py

# --- POST-BUILD PROCESSING ---

if [ $? -eq 0 ]; then
    echo "✅ Build completed successfully!"
    echo "📁 App bundle created at: dist/GN_Ticket_Automator.app"

    # Set version in Info.plist
    PLIST_PATH="dist/GN_Ticket_Automator.app/Contents/Info.plist"
    if [ -f "$PLIST_PATH" ]; then
        echo "📝 Setting version information in Info.plist..."
        defaults write "$(pwd)/$PLIST_PATH" CFBundleShortVersionString "$VERSION"
        defaults write "$(pwd)/$PLIST_PATH" CFBundleVersion "$VERSION"
        defaults write "$(pwd)/$PLIST_PATH" CFBundleDisplayName "GN Ticket Automator"
        echo "✅ Version set correctly: $VERSION"
    fi

    # --- STAGING FOR DMG CREATION ---
    # 1. Create a clean directory to stage assets for the DMG.
    echo "📦 Staging files for DMG..."
    DMG_STAGING_DIR="dmg_staging"
    rm -rf "$DMG_STAGING_DIR"
    mkdir "$DMG_STAGING_DIR"

    # 2. Copy the main application bundle to the staging directory.
    cp -R "dist/GN_Ticket_Automator.app" "$DMG_STAGING_DIR/"

    # --- ADD HELPER ASSETS TO STAGE ---
    # Define paths for our assets
    ASSETS_DIR="build_assets"
    BACKUP_SCRIPT_NAME="backup method.scpt"
    BACKUP_SCRIPT_SOURCE_PATH="$ASSETS_DIR/$BACKUP_SCRIPT_NAME"

    # Check for the backup script and copy it to the stage
    if [ ! -f "$BACKUP_SCRIPT_SOURCE_PATH" ]; then
        echo "❌ ERROR: Backup script not found at '$BACKUP_SCRIPT_SOURCE_PATH'."
        exit 1
    fi
    echo "✅ Found backup script, adding to stage."
    cp "$BACKUP_SCRIPT_SOURCE_PATH" "$DMG_STAGING_DIR/"


    # --- CREATE THE CUSTOM DMG ---
    DMG_NAME="GN_Ticket_Automator_v${VERSION}.dmg"
    echo "💿 Creating custom DMG: $DMG_NAME"

    # Remove old DMG if it exists
    [ -f "$DMG_NAME" ] && rm "$DMG_NAME"

    # 3. Create the DMG from the completed staging directory.
    create-dmg \
      --volname "GN Ticket Automator v$VERSION" \
      --window-pos 200 120 \
      --window-size 800 500 \
      --icon-size 130 \
      --text-size 14 \
      --icon "GN_Ticket_Automator.app" 250 220 \
      --hide-extension "GN_Ticket_Automator.app" \
      --icon "$BACKUP_SCRIPT_NAME" 550 350 \
      --app-drop-link 400 400 \
      "$DMG_NAME" \
      "$DMG_STAGING_DIR/"


    if [ -f "$DMG_NAME" ]; then
        echo "✅ DMG created successfully: $DMG_NAME"
        DMG_SIZE=$(du -h "$DMG_NAME" | cut -f1)
        echo "📏 DMG size: $DMG_SIZE"
    else
        echo "❌ DMG creation failed."
        exit 1
    fi

    # 4. Clean up the staging directory
    rm -rf "$DMG_STAGING_DIR"

    echo ""
    echo "🎉 BUILD COMPLETE!"
    echo "============================================================"
    echo "📱 Version: $VERSION"
    echo "📁 App Bundle: dist/GN_Ticket_Automator.app"
    echo "💿 Installer: $DMG_NAME (includes the backup script)"
    echo ""
    echo "🚀 Ready for distribution!"

    # Optional: Test the app
    read -p "🧪 Test the app now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🧪 Launching test..."
        open "dist/GN_Ticket_Automator.app"
    fi

else
    echo "❌ Build failed!"
    echo "📋 Check the error messages above for details"
    exit 1
fi