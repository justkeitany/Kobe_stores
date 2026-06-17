"""
Domain / access-mode management.

Lets the operator choose how the panel and Xtream links are addressed:
  - "ip"     → links use the raw VPS IP:port (server_url left blank).
  - "domain" → links use a custom domain; a Let's Encrypt certificate is
               obtained automatically in the background via certbot.

The actual SSL work runs as root through a single, fixed, root-owned helper
(/usr/local/sbin/iptv-ssl-setup.sh) that the backend is allowed to call via
sudo (see /etc/sudoers.d/iptv-panel). The domain is validated here AND in the
helper before it ever reaches certbot/nginx.
"""
import re
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db, AsyncSessionLocal
from app.models import Settings as SettingsModel

router = APIRouter(prefix="/api/domain", tags=["domain"])
logger = logging.getLogger(__name__)

SSL_HELPER = "/usr/local/sbin/iptv-ssl-setup.sh"

# Hostname validation: labels of a-z0-9-, at least two dot-separated labels,
# TLD of letters. Rejects IPs, ports, paths, schemes.
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?!-)[a-zA-Z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))*"
    r"\.[a-zA-Z]{2,}$"
)


class DomainConfig(BaseModel):
    mode: str           # "ip" | "domain"
    domain: str | None = None


async def _set(db: AsyncSession, key: str, value: str) -> None:
    row = (
        await db.execute(select(SettingsModel).where(SettingsModel.key == key))
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SettingsModel(key=key, value=value))


async def _get_all(db: AsyncSession) -> dict:
    rows = (await db.execute(select(SettingsModel))).scalars().all()
    return {r.key: r.value for r in rows}


async def _run_ssl_setup(domain: str) -> None:
    """Background task: obtain the certificate, then record the outcome."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/sudo", SSL_HELPER, domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        output = (out or b"").decode(errors="replace").strip()

        async with AsyncSessionLocal() as db:
            if proc.returncode == 0:
                await _set(db, "server_url", f"https://{domain}")
                await _set(db, "ssl_status", "active")
                await _set(db, "ssl_message", "HTTPS active")
                logger.info("SSL enabled for %s", domain)
            else:
                # Keep HTTP working on the domain; just report the failure.
                tail = output.splitlines()[-1] if output else "certbot failed"
                await _set(db, "ssl_status", "failed")
                await _set(db, "ssl_message", tail[:500])
                logger.warning("SSL setup failed for %s: %s", domain, tail)
            await db.commit()
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("SSL setup crashed for %s", domain)
        async with AsyncSessionLocal() as db:
            await _set(db, "ssl_status", "failed")
            await _set(db, "ssl_message", f"Internal error: {e}"[:500])
            await db.commit()


@router.get("")
async def get_domain(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    s = await _get_all(db)
    return {
        "mode": s.get("domain_mode", "ip"),
        "domain": s.get("domain", ""),
        "server_url": s.get("server_url", ""),
        "ssl_status": s.get("ssl_status", "none"),
        "ssl_message": s.get("ssl_message", ""),
    }


@router.post("")
async def set_domain(
    cfg: DomainConfig,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    if cfg.mode == "ip":
        await _set(db, "domain_mode", "ip")
        await _set(db, "server_url", "")
        await _set(db, "ssl_status", "none")
        await _set(db, "ssl_message", "")
        await db.commit()
        return {"mode": "ip", "server_url": "", "ssl_status": "none"}

    if cfg.mode == "domain":
        domain = (cfg.domain or "").strip().lower()
        if not DOMAIN_RE.match(domain):
            raise HTTPException(400, "Enter a valid domain, e.g. tv.example.com")

        # Works immediately over HTTP via the catch-all; HTTPS arrives shortly.
        await _set(db, "domain_mode", "domain")
        await _set(db, "domain", domain)
        await _set(db, "server_url", f"http://{domain}")
        await _set(db, "ssl_status", "pending")
        await _set(db, "ssl_message", "Issuing certificate…")
        await db.commit()

        asyncio.create_task(_run_ssl_setup(domain))
        return {
            "mode": "domain",
            "domain": domain,
            "server_url": f"http://{domain}",
            "ssl_status": "pending",
        }

    raise HTTPException(400, "mode must be 'ip' or 'domain'")
