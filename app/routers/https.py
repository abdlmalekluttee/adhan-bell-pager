"""HTTPS settings, DNS provider test, certificate request and removal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import audit, certificates, dns_providers, https_server
from ..deps import Authz, Principal, bad_request

router = APIRouter(prefix="/api/system/https", tags=["https"])
MANAGE = Depends(Authz("system.certificates.manage"))


def _payload() -> dict:
    return {
        "config": certificates.public_config(),
        "status": certificates.status(https_server.status()),
        "providers": dns_providers.available(),
        "ca_options": [{"key": "letsencrypt", "label": "Let's Encrypt"},
                       {"key": "letsencrypt_staging", "label": "Let's Encrypt staging (for testing)"},
                       {"key": "custom", "label": "Another ACME service"}],
        "tos_url": certificates.TOS_URL,
        "https_port": https_server.status()["port"],
    }


@router.get("", dependencies=[MANAGE])
def get_https():
    return _payload()


@router.get("/status", dependencies=[MANAGE])
def get_status():
    return certificates.status(https_server.status())


@router.put("", dependencies=[MANAGE])
def save_https(body: dict, request: Request):
    try:
        res = certificates.save_config(body or {})
    except ValueError as e:
        audit.from_request(request, "https.update", result="error", details={"error": str(e)})
        raise bad_request(str(e))
    cfg = res["config"]
    audit.from_request(request, "https.update", target=",".join(cfg["hostnames"]),
                       details={"changed": res["changed"], "enabled": cfg["enabled"], "provider": cfg["provider"],
                                "redirect_http": cfg["redirect_http"]})
    return _payload()


@router.post("/test", dependencies=[MANAGE])
def test_dns(request: Request, body: dict | None = None):
    body = dict(body or {})
    write_test = bool(body.pop("write_test", False))
    res = certificates.check_provider(body or None, write_test=write_test)
    audit.from_request(request, "https.test_provider", result="ok" if res["ok"] else "error",
                       details={"provider": (body or {}).get("provider") or certificates.get_config()["provider"],
                                "write_test": write_test, "code": res["code"],
                                **({} if res["ok"] else {"error": res["message"]})})
    return res


@router.post("/request", dependencies=[MANAGE])
def request_certificate(request: Request):
    p: Principal = request.state.principal
    try:
        job = certificates.request_certificate(p.username, reason="manual")
    except certificates.CertError as e:
        status = 409 if e.code == "busy" else 400
        raise HTTPException(status, detail={"code": e.code, "message": e.message})
    audit.from_request(request, "https.certificate_request", target=",".join(certificates.get_config()["hostnames"]))
    return {"job": job}


@router.delete("/certificate", dependencies=[MANAGE])
def remove_certificate(request: Request):
    cert = certificates.get_state().get("certificate")
    if not cert:
        raise HTTPException(404, detail={"code": "not_found", "message": "No certificate is installed."})
    job = certificates.current_job()
    if job and job["running"]:
        raise HTTPException(409, detail={"code": "busy", "message": "Wait for the certificate request to finish."})
    certificates.remove_certificate()
    audit.from_request(request, "https.certificate_remove", target=",".join(cert.get("names") or []),
                       details={"serial": cert.get("serial")})
    return _payload()
