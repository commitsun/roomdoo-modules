import base64
import hashlib
import json
import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, status

from odoo import models

from odoo.addons.pms_bookai.schemas.bookai_config import (
    BookaiConfig,
    BookaiPropertyConfig,
    BookaiUserToken,
)
from odoo.addons.pms_fastapi.dependencies import AuthenticatedEnv, PublicEnv
from odoo.addons.pms_fastapi.models.fastapi_endpoint import pms_api_router

# Lifetime of the BookAI token of a user, in seconds
USER_TOKEN_TTL = 600
USER_TOKEN_AUDIENCE = "bookai"
USER_TOKEN_SCOPE = "chat"
USER_TOKEN_KEY_PARAM = "pms_bookai.user_token_private_key"


@pms_api_router.get(
    "/bookai/config",
    response_model=BookaiConfig,
    tags=["bookai"],
)
async def get_bookai_config(
    env: AuthenticatedEnv,
) -> BookaiConfig:
    """
    Return BookAI configuration for the current instance and
    the authenticated user's properties.
    """
    helper = env["pms_api_bookai.config_router.helper"].new()
    return helper.get_config()


@pms_api_router.get(
    "/bookai/token",
    response_model=BookaiUserToken,
    tags=["bookai"],
)
async def get_bookai_user_token(
    env: AuthenticatedEnv,
) -> BookaiUserToken:
    """
    Return a short-lived BookAI token for the authenticated user.

    The front exchanges its session for this token instead of carrying the
    instance token: it names the user and the hotels it may reach, and BookAI
    checks it against the public key published at /bookai/jwks.
    """
    helper = env["pms_api_bookai.config_router.helper"].new()
    return helper.get_user_token()


@pms_api_router.get(
    "/bookai/jwks",
    tags=["bookai"],
)
async def get_bookai_jwks(
    env: PublicEnv,
) -> dict:
    """Public key that signs the BookAI tokens of the users (JWKS)."""
    helper = env["pms_api_bookai.config_router.helper"].new()
    return helper.get_jwks()


class PmsApiBookaiConfigRouterHelper(models.AbstractModel):
    _name = "pms_api_bookai.config_router.helper"
    _description = "PMS API BookAI Config Helper"

    def _is_bookai_staff(self, user):
        # Staff are internal users and portal users with hotels assigned
        # (receptionists). Customers and suppliers get no access to BookAI.
        return user._is_internal() or bool(user.pms_property_ids)

    def _get_instance_hash(self):
        """Fingerprint of the instance token, which tells BookAI the instance a
        user token belongs to without revealing the instance token."""
        icp = self.env["ir.config_parameter"].sudo()
        instance_token = icp.get_param("pms_bookai.api_token", "")
        if not instance_token:
            return ""
        return hashlib.sha256(instance_token.encode()).hexdigest()[:32]

    def _get_user_token_key(self):
        """Private key that signs the user tokens, created on first use."""
        icp = self.env["ir.config_parameter"].sudo()
        pem = icp.get_param(USER_TOKEN_KEY_PARAM)
        if not pem:
            # Several workers may get here at once: create the key only once.
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [USER_TOKEN_KEY_PARAM]
            )
            self.env.cr.execute(
                "SELECT value FROM ir_config_parameter WHERE key = %s",
                [USER_TOKEN_KEY_PARAM],
            )
            row = self.env.cr.fetchone()
            pem = row and row[0]
            if not pem:
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                pem = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode()
                icp.set_param(USER_TOKEN_KEY_PARAM, pem)
        return serialization.load_pem_private_key(pem.encode(), password=None)

    def _get_key_id(self, public_key):
        der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        digest = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode()
        return digest.rstrip("=")[:16]

    def _issue_user_token(self, user):
        icp = self.env["ir.config_parameter"].sudo()
        key = self._get_user_token_key()
        now = int(time.time())
        claims = {
            "iss": (icp.get_param("web.base.url") or "").rstrip("/"),
            "aud": USER_TOKEN_AUDIENCE,
            "sub": str(user.id),
            "login": user.login,
            "bkh": self._get_instance_hash(),
            "props": sorted(user.pms_property_ids.ids),
            "scope": USER_TOKEN_SCOPE,
            "iat": now,
            "exp": now + USER_TOKEN_TTL,
            "jti": secrets.token_hex(8),
        }
        return jwt.encode(
            claims,
            key,
            algorithm="RS256",
            headers={"kid": self._get_key_id(key.public_key())},
        )

    def _get_bookai_token(self):
        """Return the token for frontend-to-BookAI communication.

        A short-lived token of the current user, never the instance token,
        which only travels between this server and BookAI.
        """
        user = self.env.user
        if not self._is_bookai_staff(user) or not self._get_instance_hash():
            return ""
        return self._issue_user_token(user)

    def get_user_token(self):
        if not self._is_bookai_staff(self.env.user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        token = self._get_bookai_token()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="BookAI is not configured",
            )
        return BookaiUserToken(token=token, expires_in=USER_TOKEN_TTL)

    def get_jwks(self):
        public_key = self._get_user_token_key().public_key()
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
        jwk.update({"kid": self._get_key_id(public_key), "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def get_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("pms_bookai.api_endpoint", "")
        token = self._get_bookai_token()
        bookai_enabled = bool(base_url and token)
        fallback_app_url = icp.get_param("roomdoo_app_url", "")

        properties = (
            self.env["pms.property"]
            .sudo()
            .search([("user_ids", "in", [self.env.user.id])])
        )

        return BookaiConfig(
            bookaiEnabled=bookai_enabled,
            bookaiBaseUrl=base_url if bookai_enabled else "",
            bookaiToken=token if bookai_enabled else "",
            properties=[
                BookaiPropertyConfig.from_pms_property(prop, fallback_app_url)
                for prop in properties
            ],
        )
