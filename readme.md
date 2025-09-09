# GN Ticket Automator - Simplified Edition

Automate Government of Nunavut ticket submission with **one-click Google login** and **guided setup wizard**.

## 🚀 For Users (Simple!)

### **What You Need:**
- TakingITGlobal Google account
- Airtable API key
- ServiceNow password and 2FA secret

### **How to Use:**
1. **Download and run** the app
2. **Click "Sign in with Google"** - works immediately!
3. **Follow the 3-step setup wizard**:
   - Step 1: Get Airtable API key (guided with "Open Airtable" button)
   - Step 2: Enter ServiceNow password
   - Step 3: Set up 2FA secret (guided with step-by-step instructions)
4. **Start automating!** - Select sessions and click "Book Selected Sessions"

### **That's it!** No technical setup required for users.

---

## 🔧 For Administrators (One-Time Setup)

### **Before Distribution:**

**1. Configure Google OAuth (15 minutes):**
- Create Google Cloud project
- Set up OAuth credentials
- Edit `main.py` with your credentials:
  ```python
  GOOGLE_CLIENT_ID = "your_actual_client_id"
  GOOGLE_CLIENT_SECRET = "your_actual_client_secret"
  ```

**2. Customize Settings (optional):**
- Update allowed email domains in `ALLOWED_DOMAINS`
- Change redirect URI for production deployment

**3. Test and Distribute:**
- Test with a few users first
- Package as Mac app (instructions below)
- Distribute to team

### **Detailed Admin Setup:**

#### **Step 1: Google Cloud Setup**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create new project: "GN Ticket Automator"
3. Enable APIs:
   - Go to "APIs & Services" → "Library"
   - Enable "Google+ API"
4. Create OAuth credentials:
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "OAuth client ID"
   - Choose "Web application"
   - Add redirect URI: `http://localhost:5000/oauth/callback`
   - Copy Client ID and Client Secret

#### **Step 2: Configure OAuth Consent Screen**
1. Go to "APIs & Services" → "OAuth consent screen"
2. Choose "Internal" (for organization use)
3. Fill in:
   - App name: "GN Ticket Automator"
   - User support email: Your admin email
   - Authorized domains: `takingitglobal.org`
4. Add scopes: email, profile, openid

#### **Step 3: Update Application Code**
Edit `main.py` and replace these lines:
```python
# Change these:
GOOGLE_CLIENT_ID = "YOUR_GOOGLE_CLIENT_ID_HERE"
GOOGLE_CLIENT_SECRET = "YOUR_GOOGLE_CLIENT_SECRET_HERE"

# To your actual credentials:
GOOGLE_CLIENT_ID = "1234567890-abcdef.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-your_actual_secret_here"
```

#### **Step 4: Test Configuration**
```bash
python main.py
```
- Visit http://localhost:5000
- Test Google login with a @takingitglobal.org account
- Complete the setup wizard to verify everything works

---

## 📦 Installation & Dependencies

### **For Development/Testing:**
```bash
# Clone and setup
git clone <repository>
cd gn-ticket-automator
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure OAuth (see admin section above)
# Edit main.py with your Google OAuth credentials

# Run
python main.py
```

### **Dependencies:**
- Flask (web interface)
- Google OAuth libraries
- Selenium (browser automation)
- Cryptography (secure credential storage)
- Airtable integration libraries

---

## 🖥️ Mac App Packaging (Coming Soon)

**For Easy Distribution:**
```bash
# Install packaging tools
pip install pyinstaller

# Create Mac app
pyinstaller --windowed --onedir main.py

# Distribute the generated .app file
```

---

## ✨ Features

### **User Experience:**
- **One-click Google login** - no technical setup for users
- **Guided setup wizard** with step-by-step instructions
- **"Open in New Tab" buttons** for easy credential gathering
- **Real-time progress tracking** during automation
- **Encrypted credential storage** on user's computer

### **Security:**
- Domain-restricted access (@takingitglobal.org only)
- Local credential encryption using macOS Keychain
- Session-based authentication
- Secure API key storage

### **Automation Features:**
- Automatic ServiceNow login with 2FA
- Form auto-filling from Airtable data
- Zoom meeting verification
- Progress tracking with detailed logging
- Error handling with user-friendly messages

---

## 🔒 Security & Privacy

### **Data Storage:**
- **Local only** - credentials stored on user's computer
- **Encrypted** - all sensitive data encrypted with unique keys
- **No cloud storage** - no external credential storage

### **Access Control:**
- **Domain restriction** - only @takingitglobal.org emails allowed
- **Admin-controlled** - OAuth app managed centrally
- **Session-based** - secure login sessions

### **Network Security:**
- **HTTPS ready** - for production deployment
- **Minimal data transfer** - only necessary API calls
- **No credential transmission** - stored locally only

---

## 🐛 Troubleshooting

### **For Users:**

**"Access Denied" when logging in:**
- Make sure you're using your @takingitglobal.org email
- Try a different Google account
- Contact your administrator

**"Profile Error" when loading sessions:**
- Check your Airtable API key is correct
- Verify you have access to the Sessions table
- Try refreshing the page

**Automation fails:**
- Verify ServiceNow password is correct
- Check 2FA secret is properly configured
- Ensure sessions have Zoom links in Airtable

### **For Administrators:**

**OAuth not working:**
- Verify Client ID and Secret are correct
- Check redirect URI matches exactly
- Ensure OAuth consent screen is configured

**Users can't access:**
- Check their email domain is in `ALLOWED_DOMAINS`
- Verify OAuth consent screen allows their emails
- Test with your own account first

---

## 📞 Support

**For Users:**
- Contact your administrator for access issues
- Use the built-in troubleshooting guides in error pages
- Check the "Profile Error" page for Airtable connection issues

**For Administrators:**
- Review Google Cloud Console setup
- Check application logs for detailed error information
- Verify all OAuth configuration steps are complete

---

## 🔄 Updates & Maintenance

### **Updating the Application:**
1. **Backup user data** (user_profiles.db)
2. **Update code** with new version
3. **Test with admin account** first
4. **Distribute updated version** to users

### **Adding New Users:**
- No technical setup needed - just add their email to OAuth consent screen
- Users complete their own setup through the guided wizard

### **Monitoring:**
- Check application logs for errors
- Monitor OAuth usage in Google Cloud Console
- Review user feedback for UX improvements

---

## 📋 Project Structure

```
gn-ticket-automator/
├── main.py                 # Main Flask app with simplified OAuth
├── user_profiles.py        # Encrypted user profile management
├── airtable_integration.py # Airtable API client
├── gn_ticket.py           # Selenium automation
├── requirements.txt       # Dependencies
├── templates/             # HTML templates
│   ├── home.html         # Landing page with Google login
│   ├── setup_profile.html # Guided setup wizard
│   ├── gn.html           # Main automation interface
│   ├── progress.html     # Real-time progress tracking
│   └── error pages...    # User-friendly error handling
└── static/               # CSS and images
```

---

**Version:** 2.0 - Simplified Google OAuth Edition  
**License:** Internal use - TakingITGlobal Connected North  
**Contact:** System Administrator# gn-ticket-automator
