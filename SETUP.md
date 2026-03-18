# Illusion Dashboard - Setup Guide

A personal Trade Suggestion & Tracking platform for Swing Trades and Stock Options (F&O) using Dhan HQ APIs.

## 🚀 Quick Start

### 1. Get Dhan API Credentials

1. Visit [Dhan HQ Developer Portal](https://dhanhq.co/docs/v2/)
2. Generate your **permanent API Key** (this never expires)
3. Note your **Client ID**

### 2. Enable TOTP (Recommended for Automatic Token Management)

1. **Login to Dhan Web**: https://web.dhan.co/
2. **Navigate**: My Profile → "Access DhanHQ APIs"
3. **Setup TOTP**: Click "Setup TOTP" and scan QR code with authenticator app
4. **Note your TOTP secret** during setup (you'll need this)

### 3. Set Environment Variables

**For macOS/Linux (zsh/bash):**

```bash
# Add to your ~/.zshrc or ~/.bashrc
export DHAN_CLIENT_ID="your_client_id_here"
export DHAN_PIN="your_6_digit_dhan_pin"
export DHAN_TOTP_SECRET="your_totp_secret_from_authenticator"

# Reload your shell
source ~/.zshrc  # or source ~/.bashrc
```

**For Windows:**

```cmd
# Set permanently via System Properties > Environment Variables
# Or use PowerShell:
[Environment]::SetEnvironmentVariable("DHAN_CLIENT_ID", "your_client_id_here", "User")
[Environment]::SetEnvironmentVariable("DHAN_API_KEY", "your_permanent_api_key_here", "User")
```

**Verify setup:**
```bash
echo $DHAN_CLIENT_ID
echo $DHAN_PIN
echo $DHAN_TOTP_SECRET
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Dependencies

```bash
pip3 install -r requirements.txt
```

### 5. Configure Application

```bash
# Copy configuration template
cp config.yaml.example config.yaml

# The config.yaml now only contains non-sensitive settings
# All credentials come from environment variables
```

### 6. Run the Application

```bash
# Start the backend server
python -m backend.main

# Open your browser to:
# http://localhost:8000
```

## 🔐 Security Features

### Environment Variables (Recommended)

The application now prioritizes environment variables over config files:

- `DHAN_CLIENT_ID` - Your Dhan client ID
- `DHAN_API_KEY` - Permanent API key from Dhan portal  
- `DHAN_ACCESS_TOKEN` - Optional, auto-generated if not provided
- `TELEGRAM_BOT_TOKEN` - Optional, for notifications
- `TELEGRAM_CHAT_ID` - Optional, for notifications

### Automatic Token Management

- **Daily Auto-Refresh**: Tokens are automatically refreshed at 6:00 AM IST
- **Manual Refresh**: Use the "Refresh Token" button in Settings
- **Fallback Generation**: If renewal fails, generates fresh token using API key
- **No Manual Entry**: Never need to manually update tokens in config files

### Production Deployment

For production environments:

1. **Use environment variables only** - never store credentials in files
2. **Set `APP_ENV=production`** 
3. **Configure proper logging** with `LOG_LEVEL=INFO`
4. **Use secure hosting** with HTTPS

## 📁 File Structure

```
dhan/
├── .env.example          # Environment variables template
├── config.yaml.example   # Configuration template (no secrets)
├── config.yaml          # Your local config (no secrets)
├── backend/
│   ├── config.py         # Reads from env vars first, config second
│   ├── services/
│   │   ├── dhan_client.py    # API client with token management
│   │   └── scheduler.py      # Auto token refresh scheduler
│   └── api/
│       └── settings.py       # Manual token refresh endpoint
└── frontend/             # Web UI
```

## 🔄 Token Lifecycle

1. **Startup**: App reads `DHAN_API_KEY` from environment
2. **First Run**: Generates access token using API key
3. **Daily Refresh**: Scheduler renews token at 6 AM IST
4. **Manual Refresh**: Available via Settings UI
5. **Fallback**: If renewal fails, generates fresh token

## 🛠️ Development vs Production

### Development
- Use `config.yaml` for non-sensitive settings
- Set credentials as environment variables
- Access token auto-generated on startup

### Production  
- All configuration via environment variables
- No config files with secrets
- Automated token management
- Proper logging and monitoring

## 📞 Support

For issues:
1. Check environment variables are set correctly
2. Verify API key is valid in Dhan portal
3. Check logs for detailed error messages
4. Use manual "Refresh Token" button to test connectivity

## 🔒 Security Best Practices

- ✅ **Never commit credentials** to version control
- ✅ **Use environment variables** for all secrets
- ✅ **Rotate API keys** periodically
- ✅ **Monitor token refresh** logs
- ✅ **Use HTTPS** in production
- ❌ **Don't share** API keys or access tokens
- ❌ **Don't store credentials** in config files