import logging
import os
from functools import wraps

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

load_dotenv()

# Auth0 configuration
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-lzz2sk107uunqvja.us.auth0.com")
AUTH0_AUDIENCE = "https://esf-dash-rag-api"
ALGORITHMS = ["RS256"]

# Initialize the JWKS client
jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
jwks_client = PyJWKClient(jwks_url)

# HTTPBearer instance for extracting tokens
security = HTTPBearer()

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuthError(Exception):
    def __init__(self, error, status_code):
        self.error = error
        self.status_code = status_code


def verify_token(token: str) -> dict:
    """Verify the JWT token"""
    try:
        # Get the signing key from Auth0
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        # Decode and verify the token
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )

        return payload
    except jwt.ExpiredSignatureError as e:
        logger.warning(f"Token expired: {e}")
        raise AuthError({"code": "token_expired", "description": "Token is expired"}, 401) from e
    except jwt.InvalidAudienceError as e:
        logger.warning(f"Invalid audience: {e}")
        raise AuthError(
            {
                "code": "invalid_audience",
                "description": f"Invalid audience. Expected: {AUTH0_AUDIENCE}",
            },
            401,
        ) from e
    except jwt.InvalidIssuerError as e:
        logger.warning(f"Invalid issuer: {e}")
        raise AuthError(
            {
                "code": "invalid_issuer",
                "description": f"Invalid issuer. Expected: https://{AUTH0_DOMAIN}/",
            },
            401,
        ) from e
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise AuthError(
            {
                "code": "invalid_token",
                "description": f"Unable to parse authentication token: {e!s}",
            },
            401,
        ) from e


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Dependency to get the current user from the JWT token"""

    if not credentials:
        logger.error("Authorization credentials not provided")
        raise HTTPException(status_code=401, detail="Authorization credentials not provided")

    token = credentials.credentials

    try:
        payload = verify_token(token)
        return payload
    except AuthError as e:
        logger.error(f"Auth error in get_current_user: {e.error}")
        raise HTTPException(status_code=e.status_code, detail=e.error) from e
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {e!s}")
        raise HTTPException(status_code=500, detail="Authentication failed") from e


def requires_auth(f):
    """Decorator for protecting endpoints"""

    @wraps(f)
    async def decorated(*args, **kwargs):
        request = kwargs.get("request")
        if not request:
            raise HTTPException(status_code=500, detail="Request object not found")

        auth_header = request.headers.get("Authorization", None)
        if not auth_header:
            raise HTTPException(status_code=401, detail="Authorization header is missing")

        parts = auth_header.split()
        if parts[0].lower() != "bearer" or len(parts) != 2:
            raise HTTPException(status_code=401, detail="Invalid authorization header format")

        token = parts[1]
        try:
            payload = verify_token(token)
            kwargs["current_user"] = payload
        except AuthError as e:
            raise HTTPException(status_code=e.status_code, detail=e.error) from e

        return await f(*args, **kwargs)

    return decorated
