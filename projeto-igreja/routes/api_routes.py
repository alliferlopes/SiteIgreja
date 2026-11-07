"""JSON API used by the native Android app.

Install this file as ``routes/api_routes.py`` in the Flask site and register
``api_bp`` from ``app.py``. Data access uses the site's existing SQLAlchemy
models and the ``igreja_db`` tables.
"""

from datetime import date, time
from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash

from extensions import db
from models.evento import Evento
from models.pedido import PedidoOracao
from models.usuario import Usuario


api_bp = Blueprint("android_api", __name__, url_prefix="/api")
TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60


def _token_serializer():
    return URLSafeTimedSerializer(
        current_app.config["API_TOKEN_SECRET"],
        salt="igreja-android-admin-v1",
    )


def _iso_value(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _json_object():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _serialize_event(event):
    return {
        "id": event.id,
        "titulo": event.titulo,
        "descricao": event.descricao,
        "data": _iso_value(event.data),
        "hora": _iso_value(event.hora),
    }


def _parse_event_payload(payload):
    title = str(payload.get("titulo", "")).strip()
    description = str(payload.get("descricao", "")).strip()
    raw_date = str(payload.get("data", "")).strip()
    raw_time = str(payload.get("hora", payload.get("horario", ""))).strip()

    if not title:
        return None, "Informe o título do evento."
    if len(title) > 150:
        return None, "O título deve ter no máximo 150 caracteres."
    if not description:
        return None, "Informe a descrição do evento."
    try:
        normalized_date = date.fromisoformat(raw_date).isoformat()
    except ValueError:
        return None, "A data deve estar no formato AAAA-MM-DD."
    try:
        normalized_time = time.fromisoformat(raw_time).isoformat(timespec="minutes")
    except ValueError:
        return None, "O horário deve estar no formato HH:MM."

    return {
        "titulo": title,
        "descricao": description,
        "data": normalized_date,
        "hora": normalized_time,
    }, None


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return jsonify(message="Autenticação administrativa necessária."), 401

        try:
            payload = _token_serializer().loads(
                token.strip(), max_age=TOKEN_MAX_AGE_SECONDS
            )
            user_id = int(payload["uid"])
        except SignatureExpired:
            return jsonify(message="A sessão expirou. Entre novamente."), 401
        except (BadSignature, KeyError, TypeError, ValueError):
            return jsonify(message="Token inválido. Entre novamente."), 401

        if Usuario.query.get(user_id) is None:
            return jsonify(message="A conta administrativa não existe mais."), 401
        return view(*args, **kwargs)

    return wrapped


@api_bp.get("/eventos")
def listar_eventos_api():
    eventos = Evento.query.order_by(Evento.data.asc(), Evento.id.asc()).all()
    return jsonify(items=[_serialize_event(event) for event in eventos])


@api_bp.post("/oracoes")
def criar_pedido_oracao_api():
    payload = _json_object()
    nome = str(payload.get("nome", "")).strip()
    email = str(payload.get("email", "")).strip()
    pedido = str(payload.get("pedido", "")).strip()

    if not nome or len(nome) > 100:
        return jsonify(message="Informe um nome com até 100 caracteres."), 400
    if not email or len(email) > 100 or "@" not in email:
        return jsonify(message="Informe um e-mail válido com até 100 caracteres."), 400
    if not pedido:
        return jsonify(message="Escreva seu pedido de oração."), 400

    try:
        prayer = PedidoOracao(nome=nome, email=email, pedido=pedido)
        db.session.add(prayer)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Falha ao salvar pedido de oração pela API")
        return jsonify(message="Não foi possível salvar o pedido de oração."), 500

    return jsonify(message="Pedido recebido.", id=prayer.id), 201


@api_bp.post("/admin/login")
def login_admin_api():
    payload = _json_object()
    email = str(payload.get("email", "")).strip()
    password = str(payload.get("senha", ""))
    if not email or not password:
        return jsonify(message="Informe e-mail e senha."), 400

    user = Usuario.query.filter_by(email=email).first()
    if user is None or not check_password_hash(user.senha, password):
        return jsonify(message="E-mail ou senha inválidos."), 401

    token = _token_serializer().dumps({"uid": user.id})
    return jsonify(token=token, nome=user.nome), 200


@api_bp.get("/admin/eventos")
@_admin_required
def listar_eventos_admin_api():
    eventos = Evento.query.order_by(Evento.data.asc(), Evento.id.asc()).all()
    return jsonify(items=[_serialize_event(event) for event in eventos])


@api_bp.post("/admin/eventos")
@_admin_required
def criar_evento_api():
    payload, error = _parse_event_payload(_json_object())
    if error:
        return jsonify(message=error), 400

    event = Evento(**payload)
    try:
        db.session.add(event)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Falha ao criar evento pela API")
        return jsonify(message="Não foi possível salvar o evento."), 500
    return jsonify(item=_serialize_event(event)), 201


@api_bp.put("/admin/eventos/<int:event_id>")
@_admin_required
def atualizar_evento_api(event_id):
    event = Evento.query.get_or_404(event_id)
    payload, error = _parse_event_payload(_json_object())
    if error:
        return jsonify(message=error), 400

    for field, value in payload.items():
        setattr(event, field, value)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Falha ao atualizar evento pela API")
        return jsonify(message="Não foi possível atualizar o evento."), 500
    return jsonify(item=_serialize_event(event)), 200


@api_bp.delete("/admin/eventos/<int:event_id>")
@_admin_required
def excluir_evento_api(event_id):
    event = Evento.query.get_or_404(event_id)
    try:
        db.session.delete(event)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Falha ao excluir evento pela API")
        return jsonify(message="Não foi possível excluir o evento."), 500
    return jsonify(message="Evento excluído."), 200


@api_bp.get("/admin/oracoes")
@_admin_required
def listar_oracoes_admin_api():
    pedidos = PedidoOracao.query.order_by(PedidoOracao.id.desc()).all()
    return jsonify(
        items=[
            {
                "id": prayer.id,
                "nome": prayer.nome,
                "email": prayer.email,
                "pedido": prayer.pedido,
            }
            for prayer in pedidos
        ]
    )
