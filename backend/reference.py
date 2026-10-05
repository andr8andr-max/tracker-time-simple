"""Справочники, доступные всем авторизованным пользователям."""

from flask import Blueprint, jsonify, request

from .db import query_all
from .security import api_login_required

reference_bp = Blueprint("reference", __name__, url_prefix="/api")


@reference_bp.get("/projects")
@api_login_required
def active_projects():
    """
    Проекты для выпадающих списков.

    По умолчанию — только активные. Параметр all=1 доступен руководителю
    (проверка роли выполняется в admin_bp, здесь просто отдаём активные).
    """
    include_inactive = request.args.get("all") == "1"
    if include_inactive:
        rows = query_all("SELECT id, name, is_active FROM projects ORDER BY name")
    else:
        rows = query_all(
            "SELECT id, name, is_active FROM projects WHERE is_active = 1 ORDER BY name"
        )
    return jsonify({"projects": rows})
