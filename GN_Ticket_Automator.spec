# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['google.auth.transport.requests', 'googleapiclient', 'google_auth_oauthlib', 'cryptography', 'keyring', 'keyring.backends', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'werkzeug.serving', 'flask_session']
hiddenimports += collect_submodules('google')
hiddenimports += collect_submodules('googleapiclient')
hiddenimports += collect_submodules('keyring')


a = Analysis(
    ['app_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static'), ('.env', '.'), ('native_window_simple.py', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GN_Ticket_Automator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GN_Ticket_Automator',
)
app = BUNDLE(
    coll,
    name='GN_Ticket_Automator.app',
    icon=None,
    bundle_identifier='org.takingitglobal.gn-ticket-automator',
)
