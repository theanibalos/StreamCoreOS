import os
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from core.base_tool import BaseTool

class AuthTool(BaseTool):
    def __init__(self):
        self._secret_key = os.getenv("AUTH_SECRET_KEY")
        if not self._secret_key:
            raise EnvironmentError("AUTH_SECRET_KEY is required. Set it in your .env file.")
        
        if len(self._secret_key) < 32:
            raise ValueError("AUTH_SECRET_KEY must be at least 32 characters long for security.")

        self._algorithm = os.getenv("AUTH_ALGORITHM", "HS256")
        self._access_token_expire_minutes = int(os.getenv("AUTH_TOKEN_EXPIRE_MINUTES", 60))

    @property
    def name(self) -> str:
        return "auth"

    def setup(self):
        print("[AuthTool] Initializing Security Infrastructure...")

    def get_interface_description(self) -> str:
        return """
        Authentication Tool (auth):
        - PURPOSE: Manage system security, password hashing, and JWT token lifecycle.
        - CAPABILITIES:
            - hash_password(password: str) -> str: Securely hashes a plain-text password using bcrypt.
            - verify_password(password: str, hashed_password: str) -> bool: Verifies if a password matches its hash.
            - create_token(data: dict, expires_delta: Optional[int] = None) -> str: 
                Generates a JWT signed token. 'data' should contain claims (e.g. {'sub': user_id}). 
                'expires_delta' is optional minutes until expiration.
            - decode_token(token: str) -> dict: 
                Verifies and decodes a JWT token. Returns the payload dictionary. 
                Raises Exception if token is expired or invalid.
            - validate_token(token: str) -> dict | None:
                Safe, non-throwing token validation. Returns the decoded payload
                if valid, or None if expired/invalid. Ideal for middleware guards.
        """

    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify_password(self, password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(password.encode(), hashed_password.encode())

    def create_token(self, data: dict, expires_delta: Optional[int] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + timedelta(minutes=expires_delta)
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=self._access_token_expire_minutes)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self._secret_key, algorithm=self._algorithm)
        return encoded_jwt

    def decode_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[self._algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            raise Exception("Token has expired")
        except jwt.InvalidTokenError:
            raise Exception("Invalid token")
        except Exception:
            raise Exception("Could not validate credentials")

    def validate_token(self, token: str) -> dict | None:
        try:
            return self.decode_token(token)
        except Exception:
            return None

    def shutdown(self):
        pass
