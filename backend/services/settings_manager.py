"""
Settings Manager for Dhan Credentials
Handles secure storage and management of API credentials.
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet
import base64

logger = logging.getLogger(__name__)

class SettingsManager:
    def __init__(self):
        self.settings_dir = Path(__file__).parent.parent.parent / "data" / "settings"
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.settings_dir / "credentials.enc"
        self.key_file = self.settings_dir / ".key"
        
    def _get_or_create_key(self) -> bytes:
        """Get or create encryption key."""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            # Set restrictive permissions
            os.chmod(self.key_file, 0o600)
            return key
    
    def _encrypt_data(self, data: dict) -> bytes:
        """Encrypt settings data."""
        key = self._get_or_create_key()
        f = Fernet(key)
        json_data = json.dumps(data).encode()
        return f.encrypt(json_data)
    
    def _decrypt_data(self, encrypted_data: bytes) -> dict:
        """Decrypt settings data."""
        key = self._get_or_create_key()
        f = Fernet(key)
        decrypted_data = f.decrypt(encrypted_data)
        return json.loads(decrypted_data.decode())
    
    def save_credentials(self, credentials: Dict[str, str]) -> bool:
        """Save encrypted credentials to file."""
        try:
            # Validate required fields
            required_fields = ["client_id", "api_key", "api_secret", "pin", "totp_secret"]
            for field in required_fields:
                if not credentials.get(field):
                    logger.error(f"Missing required field: {field}")
                    return False
            
            encrypted_data = self._encrypt_data(credentials)
            with open(self.settings_file, 'wb') as f:
                f.write(encrypted_data)
            
            # Set restrictive permissions
            os.chmod(self.settings_file, 0o600)
            logger.info("Credentials saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error saving credentials: {e}")
            return False
    
    def load_credentials(self) -> Optional[Dict[str, str]]:
        """Load and decrypt credentials from file."""
        try:
            if not self.settings_file.exists():
                return None
                
            with open(self.settings_file, 'rb') as f:
                encrypted_data = f.read()
            
            credentials = self._decrypt_data(encrypted_data)
            logger.info("Credentials loaded successfully")
            return credentials
            
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")
            return None
    
    def get_masked_credentials(self) -> Optional[Dict[str, str]]:
        """Get credentials with sensitive data masked."""
        credentials = self.load_credentials()
        if not credentials:
            return None
        
        masked = {}
        for key, value in credentials.items():
            if key in ["api_key", "api_secret", "pin"]:
                # Show only last 4 characters
                masked[key] = "*" * (len(value) - 4) + value[-4:] if len(value) > 4 else "*" * len(value)
            elif key == "totp_secret":
                # Show only first 4 and last 4 characters
                if len(value) > 8:
                    masked[key] = value[:4] + "*" * (len(value) - 8) + value[-4:]
                else:
                    masked[key] = "*" * len(value)
            else:
                masked[key] = value
        
        return masked
    
    def update_environment_variables(self, credentials: Dict[str, str]) -> bool:
        """Update environment variables with new credentials."""
        try:
            os.environ["DHAN_CLIENT_ID"] = credentials["client_id"]
            os.environ["DHAN_API_KEY"] = credentials["api_key"]
            os.environ["DHAN_API_SECRET"] = credentials["api_secret"]
            os.environ["DHAN_PIN"] = credentials["pin"]
            os.environ["DHAN_TOTP_SECRET"] = credentials["totp_secret"]
            
            logger.info("Environment variables updated")
            return True
            
        except Exception as e:
            logger.error(f"Error updating environment variables: {e}")
            return False
    
    def test_credentials(self, credentials: Dict[str, str]) -> Dict[str, Any]:
        """Test credentials by attempting TOTP generation."""
        try:
            import pyotp
            
            # Validate required fields
            required_fields = ["client_id", "api_key", "api_secret", "pin", "totp_secret"]
            for field in required_fields:
                if not credentials.get(field):
                    return {
                        "success": False,
                        "message": f"Missing required field: {field}"
                    }
            
            # Test TOTP secret format
            totp_secret = credentials["totp_secret"]
            if len(totp_secret) < 16:
                return {
                    "success": False,
                    "message": "TOTP secret appears to be too short"
                }
            
            # Test TOTP generation
            totp = pyotp.TOTP(totp_secret)
            current_code = totp.now()
            
            # Validate PIN format
            pin = credentials["pin"]
            if len(pin) != 6 or not pin.isdigit():
                return {
                    "success": False,
                    "message": "PIN must be exactly 6 digits"
                }
            
            return {
                "success": True,
                "message": "Credentials validated successfully",
                "totp_code": current_code
            }
            
        except Exception as e:
            logger.error(f"Credential test error: {e}")
            return {
                "success": False,
                "message": f"Credential validation failed: {str(e)}"
            }
    
    def export_settings(self) -> Optional[Dict[str, Any]]:
        """Export settings for backup (without sensitive data)."""
        credentials = self.load_credentials()
        if not credentials:
            return None
        
        return {
            "client_id": credentials.get("client_id"),
            "has_api_key": bool(credentials.get("api_key")),
            "has_api_secret": bool(credentials.get("api_secret")),
            "has_pin": bool(credentials.get("pin")),
            "has_totp_secret": bool(credentials.get("totp_secret")),
            "export_date": str(Path(__file__).stat().st_mtime)
        }
    
    def clear_credentials(self) -> bool:
        """Clear all stored credentials."""
        try:
            if self.settings_file.exists():
                self.settings_file.unlink()
            if self.key_file.exists():
                self.key_file.unlink()
            
            logger.info("Credentials cleared successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error clearing credentials: {e}")
            return False

# Global instance
settings_manager = SettingsManager()