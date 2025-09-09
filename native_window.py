# native_window.py - Native macOS status window

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import webbrowser
import subprocess
import sys
import os
from pathlib import Path

class GNTicketStatusWindow:
    def __init__(self, flask_port=5000):
        self.flask_port = flask_port
        self.flask_url = f"http://127.0.0.1:{flask_port}"
        self.flask_server = None
        self.window = None
        self.status_var = None
        self.setup_window()
        
    def setup_window(self):
        """Create the native status window"""
        self.window = tk.Tk()
        self.window.title("GN Ticket Automator")
        self.window.geometry("400x300")
        self.window.resizable(False, False)
        
        # Set app icon if available
        try:
            # Try to set a custom icon
            icon_path = self.get_icon_path()
            if icon_path and os.path.exists(icon_path):
                self.window.iconbitmap(icon_path)
        except:
            pass
        
        # Configure window close behavior
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Bind Cmd+Q (or Ctrl+Q on other platforms)
        self.window.bind('<Command-q>', lambda e: self.quit_app())
        self.window.bind('<Control-q>', lambda e: self.quit_app())
        
        self.create_widgets()
        
        # Center window on screen
        self.center_window()
        
    def get_icon_path(self):
        """Get path to app icon"""
        if getattr(sys, 'frozen', False):
            # In bundled app
            return os.path.join(sys._MEIPASS, 'icon.ico')
        else:
            # In development
            return 'icon.ico'
    
    def create_widgets(self):
        """Create the UI widgets"""
        # Main frame
        main_frame = ttk.Frame(self.window, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # App title
        title_label = ttk.Label(
            main_frame, 
            text="🎫 GN Ticket Automator", 
            font=("SF Pro Display", 24, "bold")
        )
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Status indicator
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="10")
        status_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 20))
        
        self.status_var = tk.StringVar(value="🟢 Running")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, font=("SF Pro Display", 14))
        status_label.grid(row=0, column=0)
        
        # Buttons frame
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.grid(row=2, column=0, columnspan=2, pady=(0, 20))
        
        # Open Web Interface button
        open_btn = ttk.Button(
            buttons_frame,
            text="📱 Open Web Interface",
            command=self.open_web_interface,
            width=20
        )
        open_btn.grid(row=0, column=0, padx=(0, 10))
        
        # Check Updates button
        update_btn = ttk.Button(
            buttons_frame,
            text="📦 Check for Updates",
            command=self.check_updates,
            width=20
        )
        update_btn.grid(row=0, column=1, padx=(10, 0))
        
        # Info section
        info_frame = ttk.LabelFrame(main_frame, text="Information", padding="10")
        info_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 20))
        
        # Version info
        try:
            from updater import APP_VERSION
            version_text = f"Version: {APP_VERSION}"
        except:
            version_text = "Version: Unknown"
            
        version_label = ttk.Label(info_frame, text=version_text)
        version_label.grid(row=0, column=0, sticky=tk.W)
        
        # URL info
        url_label = ttk.Label(info_frame, text=f"Web Interface: {self.flask_url}")
        url_label.grid(row=1, column=0, sticky=tk.W)
        
        # Quit button
        quit_frame = ttk.Frame(main_frame)
        quit_frame.grid(row=4, column=0, columnspan=2, pady=(20, 0))
        
        quit_btn = ttk.Button(
            quit_frame,
            text="⏹ Quit App",
            command=self.quit_app,
            width=15
        )
        quit_btn.grid(row=0, column=0)
        
        # Configure grid weights for responsive layout
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
    
    def center_window(self):
        """Center the window on screen"""
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - (self.window.winfo_width() // 2)
        y = (self.window.winfo_screenheight() // 2) - (self.window.winfo_height() // 2)
        self.window.geometry(f"+{x}+{y}")
    
    def open_web_interface(self):
        """Open the web interface in default browser"""
        try:
            webbrowser.open(self.flask_url)
            self.status_var.set("🌐 Web interface opened")
            # Reset status after 3 seconds
            self.window.after(3000, lambda: self.status_var.set("🟢 Running"))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open web interface:\n{e}")
    
    def check_updates(self):
        """Check for app updates"""
        try:
            self.status_var.set("🔍 Checking for updates...")
            
            def check_in_background():
                try:
                    from updater import AppUpdater
                    updater = AppUpdater()
                    update_info = updater.check_for_updates()
                    
                    if update_info.get('available'):
                        # Update available
                        self.window.after(0, lambda: self.show_update_available(update_info))
                    else:
                        # No update available
                        self.window.after(0, lambda: self.status_var.set("✅ Up to date"))
                        self.window.after(3000, lambda: self.status_var.set("🟢 Running"))
                        
                except Exception as e:
                    self.window.after(0, lambda: self.status_var.set("❌ Update check failed"))
                    self.window.after(3000, lambda: self.status_var.set("🟢 Running"))
            
            # Run update check in background thread
            threading.Thread(target=check_in_background, daemon=True).start()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to check for updates:\n{e}")
            self.status_var.set("🟢 Running")
    
    def show_update_available(self, update_info):
        """Show update available dialog"""
        self.status_var.set("📦 Update available!")
        
        message = f"Version {update_info['version']} is available!\n\n"
        if update_info.get('release_notes'):
            message += f"What's new:\n{update_info['release_notes'][:200]}..."
        
        result = messagebox.askyesno(
            "Update Available",
            message + "\n\nWould you like to open the web interface to install the update?",
            icon='question'
        )
        
        if result:
            # Open web interface and navigate to update page
            webbrowser.open(f"{self.flask_url}/update/check")
        
        self.status_var.set("🟢 Running")
    
    def set_flask_server(self, server):
        """Set reference to Flask server for proper shutdown"""
        self.flask_server = server
    
    def on_closing(self):
        """Handle window close event"""
        self.quit_app()
    
    def quit_app(self):
        """Quit the entire application"""
        if messagebox.askokcancel("Quit", "Quit GN Ticket Automator?"):
            self.status_var.set("🛑 Shutting down...")
            self.window.update()
            
            # Shutdown Flask server if available
            if self.flask_server:
                try:
                    self.flask_server.shutdown()
                except:
                    pass
            
            # Close window and exit
            self.window.quit()
            self.window.destroy()
            
            # Force exit
            import os
            os._exit(0)
    
    def run(self):
        """Start the status window main loop"""
        self.window.mainloop()

# Integration function to start both Flask and native window
def run_app_with_native_window():
    """Run the app with both Flask server and native window"""
    import threading
    from werkzeug.serving import make_server
    
    # Import your Flask app
    from main import app
    
    # Create Flask server
    server = make_server('127.0.0.1', 5000, app, threaded=True)
    
    # Create native window
    status_window = GNTicketStatusWindow(flask_port=5000)
    status_window.set_flask_server(server)
    
    # Start Flask server in background thread
    def start_flask():
        print("🌐 Starting Flask server...")
        server.serve_forever()
    
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # Give Flask a moment to start
    import time
    time.sleep(2)
    
    # Auto-open web interface
    try:
        import webbrowser
        webbrowser.open("http://127.0.0.1:5000")
    except:
        pass
    
    print("🖥 Starting native window...")
    # Start native window (this blocks until window is closed)
    status_window.run()

if __name__ == "__main__":
    run_app_with_native_window()