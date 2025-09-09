# native_window_simple.py - Simplified version with minimal dependencies

import sys
import os
import threading
import time
import webbrowser
import signal

def simple_status_interface():
    """Simple command-line status interface if tkinter fails"""
    print("\n" + "="*50)
    print("🎫 GN TICKET AUTOMATOR")
    print("="*50)
    print("✅ Server running at: http://127.0.0.1:5000")
    print("🌐 Web interface should open automatically")
    print("")
    print("Commands:")
    print("  'o' + Enter  - Open web interface")
    print("  'u' + Enter  - Check for updates") 
    print("  'q' + Enter  - Quit app")
    print("  Ctrl+C       - Force quit")
    print("="*50)
    
    try:
        while True:
            try:
                command = input("\nGN Ticket Automator > ").lower().strip()
                
                if command == 'q' or command == 'quit':
                    print("🛑 Shutting down...")
                    break
                elif command == 'o' or command == 'open':
                    print("🌐 Opening web interface...")
                    webbrowser.open("http://127.0.0.1:5000")
                elif command == 'u' or command == 'update':
                    print("📦 Opening update page...")
                    webbrowser.open("http://127.0.0.1:5000/update/check")
                elif command == 'help' or command == 'h':
                    print("Commands: (o)pen, (u)pdate, (q)uit")
                elif command == '':
                    continue
                else:
                    print(f"Unknown command: '{command}'. Type 'help' for commands.")
                    
            except EOFError:
                break
            except Exception as e:
                print(f"Error: {e}")
                
    except KeyboardInterrupt:
        print("\n🛑 Received Ctrl+C")
    
    print("👋 Goodbye!")
    return True


def run_app_with_simple_interface():
    """Run the app with simple command-line interface"""
    import threading
    from werkzeug.serving import make_server
    
    # Import your Flask app
    try:
        # Ensure environment is loaded before importing main
        import sys
        import os
        from pathlib import Path
        from main import app
    except ImportError as e:
        print(f"❌ Failed to import Flask app: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Create Flask server
    server = make_server('127.0.0.1', 5000, app, threaded=True)
    
    # Setup signal handler for Ctrl+C
    def signal_handler(signum, frame):
        print("\n🛑 Received shutdown signal")
        server.shutdown()
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start Flask server in background thread
    def start_flask():
        print("🌐 Starting Flask server...")
        try:
            server.serve_forever()
        except Exception as e:
            print(f"Flask server error: {e}")
    
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # Give Flask a moment to start
    time.sleep(2)
    
    # Auto-open web interface
    try:
        print("🌐 Opening web interface...")
        webbrowser.open("http://127.0.0.1:5000")
    except Exception as e:
        print(f"Could not auto-open browser: {e}")
    
    # Start simple interface (this blocks until user quits)
    try:
        simple_status_interface()
    finally:
        # Cleanup
        try:
            server.shutdown()
        except:
            pass
        os._exit(0)


# Try the full tkinter version, fallback to simple version
def run_app_with_native_window():
    """Try to run with tkinter, fallback to simple interface"""
    try:
        # Set up environment first
        import sys
        import os
        from pathlib import Path
        
        # Ensure app data directory exists
        app_dir = Path.home() / 'GN_Ticket_Automator'
        app_dir.mkdir(exist_ok=True)
        
        # Try to import tkinter
        import tkinter as tk
        from tkinter import ttk, messagebox
        
        # If successful, try the full native window
        print("🖥  Starting with native tkinter window...")
        
        # Import the full native window implementation
        import threading
        from werkzeug.serving import make_server
        
        # Import your Flask app
        try:
            from main import app
        except Exception as e:
            print(f"❌ Failed to import Flask app: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        # Create Flask server
        server = make_server('127.0.0.1', 5000, app, threaded=True)
        
        # Create a very simple tkinter window
        root = tk.Tk()
        root.title("GN Ticket Automator")
        root.geometry("300x200")
        root.resizable(False, False)
        
        # Simple UI
        title_label = tk.Label(root, text="🎫 GN Ticket Automator", font=("Arial", 16, "bold"))
        title_label.pack(pady=20)
        
        status_label = tk.Label(root, text="🟢 Running", font=("Arial", 12))
        status_label.pack(pady=10)
        
        def open_web():
            webbrowser.open("http://127.0.0.1:5000")
            
        def check_updates():
            webbrowser.open("http://127.0.0.1:5000/update/check")
            
        def quit_app():
            if messagebox.askokcancel("Quit", "Quit GN Ticket Automator?"):
                server.shutdown()
                root.quit()
                root.destroy()
                os._exit(0)
        
        # Buttons
        button_frame = tk.Frame(root)
        button_frame.pack(pady=20)
        
        open_btn = tk.Button(button_frame, text="📱 Open Web Interface", command=open_web)
        open_btn.pack(pady=5)
        
        update_btn = tk.Button(button_frame, text="📦 Check Updates", command=check_updates)
        update_btn.pack(pady=5)
        
        quit_btn = tk.Button(button_frame, text="⏹ Quit", command=quit_app)
        quit_btn.pack(pady=5)
        
        # Handle window close
        root.protocol("WM_DELETE_WINDOW", quit_app)
        
        # Bind Cmd+Q
        root.bind('<Command-q>', lambda e: quit_app())
        root.bind('<Control-q>', lambda e: quit_app())
        
        # Start Flask in background
        def start_flask():
            server.serve_forever()
        
        flask_thread = threading.Thread(target=start_flask, daemon=True)
        flask_thread.start()
        
        # Give Flask time to start
        time.sleep(2)
        
        # Auto-open browser
        try:
            webbrowser.open("http://127.0.0.1:5000")
        except:
            pass
        
        # Center window
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (root.winfo_width() // 2)
        y = (root.winfo_screenheight() // 2) - (root.winfo_height() // 2)
        root.geometry(f"+{x}+{y}")
        
        print("✅ Native window interface started")
        
        # Start the GUI loop
        root.mainloop()
        
    except ImportError as e:
        print(f"⚠️  tkinter not available ({e}), using simple interface...")
        run_app_with_simple_interface()
        
    except Exception as e:
        print(f"⚠️  Native window failed ({e}), using simple interface...")
        run_app_with_simple_interface()


if __name__ == "__main__":
    run_app_with_native_window()