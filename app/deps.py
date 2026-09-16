"""Request authentication/authorization helpers shared by all routers.

Every API route must declare an ``Authz`` dependency: ``Authz("perm", ...)``
requires *all* listed permissions, ``Authz(any_of=[...])`` requires at least one,
and ``Authz()`` requires only a signed-in user. A test walks the route table
and fails if any API route lacks one (deny by default).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import rbac


@dataclass
class Principal:
    user: dict
    session: dict
    effective: rbac.Effective

    @property
    def id(self) -> int:
        return self.user["id"]

    @property
    def username(self) -> str:
        return self.user["username"]

    def has(self, perm: str) -> bool:
        return self.effective.has(perm)

    def modules_with(self, action: str) -> set[str]:
        return {m for m in ("adhan", "bells") if self.has(f"{m}.{action}")}


class Authz:
    def __init__(self, *perms: str, any_of: list[str] | None = None):
        unknown = [p for p in (*perms, *(any_of or [])) if p not in rbac.ALL_SET]
        if unknown:
            raise ValueError(f"unknown permissions in route declaration: {unknown}")
        self.perms = perms
        self.any_of = any_of or []

    def __call__(self, request: Request) -> Principal:
        p: Principal | None = getattr(request.state, "principal", None)
        if p is None:
            raise HTTPException(401, detail={"code": "not_authenticated", "message": "Please sign in."})
        missing = [perm for perm in self.perms if not p.has(perm)]
        if missing or (self.any_of and not any(p.has(x) for x in self.any_of)):
            raise HTTPException(403, detail={"code": "forbidden",
                                             "message": "You do not have permission to do this.",
                                             "required": list(self.perms) or self.any_of})
        return p


def bad_request(message: str, code: str = "invalid", **extra) -> HTTPException:
    return HTTPException(400, detail={"code": code, "message": message, **extra})


def not_found(message: str = "Not found.") -> HTTPException:
    return HTTPException(404, detail={"code": "not_found", "message": message})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
