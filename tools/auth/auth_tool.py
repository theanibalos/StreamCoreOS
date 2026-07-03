"""
Auth Tool — Reference Implementation for Authentication in MicroCoreOS
=======================================================================

This is the REFERENCE IMPLEMENTATION for auth tools. Any replacement
(Keycloak/OIDC-backed, Auth0, paseto tokens, ...) MUST follow this contract
and register under the same injection name: "auth".

PUBLIC CONTRACT (what plugins use):
────────────────────────────────────────────────────────────────────────────────
    hashed  = await auth.hash_password("plain")          # async — CPU-bound
    ok      = await auth.verify_password("plain", hashed) # async — CPU-bound
    token   = auth.create_token({"sub": "1"}, expires_delta=60)   # sync
    payload = auth.decode_token(token)    # sync — raises on invalid/expired
    payload = auth.validate_token(token)  # sync — returns None, never raises

REPLACEMENT STANDARD (plugins unaffected):
────────────────────────────────────────────────────────────────────────────────
    1. name = "auth".
    2. hash_password / verify_password MUST stay async: password hashing is
       CPU-bound by design (~100ms) and must run off the event loop.
    3. decode_token / validate_token MUST stay sync AND local: validate
       signatures with a locally-held key (HS256 secret or cached OIDC public
       key fetched in setup()). NEVER do remote introspection per request —
       a network call inside these sync methods blocks the event loop.
       validate_token is used as http auth_validator on EVERY request.
    4. Exceptions: raise TokenExpiredError / InvalidTokenError / AuthError
       from decode_token. validate_token returns None instead of raising.
"""

import os
import asyncio
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from core.base_tool import BaseTool


class AuthError(Exception):
    """Base class for authentication failures."""


class TokenExpiredError(AuthError):
    """The JWT token's expiration time has passed."""


class InvalidTokenError(AuthError):
    """The JWT token is malformed or its signature does not verify."""


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

    async def setup(self):
        print("[AuthTool] Initializing Security Infrastructure...")

    def get_interface_description(self) -> str:
        return """
        Authentication Tool (auth):
        - PURPOSE: Manage system security, password hashing, and JWT token lifecycle.
        - CAPABILITIES:
            - await hash_password(password: str) -> str: Securely hashes a plain-text
                password using bcrypt. Async — runs in a thread (bcrypt is CPU-bound).
            - await verify_password(password: str, hashed_password: str) -> bool:
                Verifies if a password matches its hash. Async — runs in a thread.
            - create_token(data: dict, expires_delta: Optional[int] = None) -> str:
                Generates a JWT signed token. 'data' should contain claims (e.g. {'sub': user_id}).
                'expires_delta' is optional minutes until expiration.
            - decode_token(token: str) -> dict:
                Verifies and decodes a JWT token. Returns the payload dictionary.
                Raises TokenExpiredError / InvalidTokenError / AuthError on failure.
            - validate_token(token: str) -> dict | None:
                Safe, non-throwing token validation. Returns the decoded payload
                if valid, or None if expired/invalid. Ideal for middleware guards.
        """

    async def hash_password(self, password: str) -> str:
        # bcrypt is CPU-bound (~100ms by design) — run in a thread so it
        # never blocks the event loop under concurrent requests.
        return await asyncio.to_thread(
            lambda: bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        )

    async def verify_password(self, password: str, hashed_password: str) -> bool:
        return await asyncio.to_thread(
            bcrypt.checkpw, password.encode(), hashed_password.encode()
        )

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
            raise TokenExpiredError("Token has expired")
        except jwt.InvalidTokenError:
            raise InvalidTokenError("Invalid token")
        except Exception:
            raise AuthError("Could not validate credentials")

    def validate_token(self, token: str) -> dict | None:
        try:
            return self.decode_token(token)
        except Exception:
            return None

    def shutdown(self):
        pass
