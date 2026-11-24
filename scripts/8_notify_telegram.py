"""Script 8 — Avisos por bot de Telegram cuando una operacion en papel llega a +25%, +50%,
+75% o +100% de ganancia de la prima.

Cada aviso dice el ticker, el nivel, cuando paso, el precio del subyacente en ese momento y
su distancia al strike del protocolo y al strike publicado por Vicente Luz, para saber si se
gano llegando al strike o cerca de el. La regla oficial no cambia (se cierra en +50%); un
nivel alcanzado despues del cierre se avisa como "en sombra" (PROTOCOLO §10 nota 2).

Credenciales (GitHub Secrets; sin ellas el script no hace nada):
  TELEGRAM_BOT_TOKEN  token del bot (lo da @BotFather)
  TELEGRAM_CHAT_ID    id del chat donde llegan los avisos
Primera activacion: manda un mensaje de bienvenida y marca como avisados los niveles ya
alcanzados, para no mandar el historico. Si en una corrida aparecen mas de MAX_EACH niveles
nuevos (por ejemplo tras un reprocesamiento), manda un solo resumen.
Tambien avisa cuando una señal ACIERTA segun el contrato de Vicente (el precio toco su strike
antes de su vencimiento); la primera vez marca las historicas sin enviarlas.
Estado: data/state.json -> levels_notified y vl_notified. El token nunca se escribe en el log.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import get_config, load_state, log_event, update_state  # noqa: E402
from report_lib import distance_label, level_events, load_data, ops_of, vl_easy  # noqa: E402

MAX_EACH = 20          # mas niveles nuevos que esto en una corrida: un solo resumen
TELEGRAM_LIMIT = 3800  # caracteres por mensaje (el limite de Telegram es 4096)


def event_key(e: dict) -> str:
    return f"{e['op']['signal_key']}|{e['nivel']}"


def _money(x) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "sin dato"


def format_event(e: dict, near: float) -> str:
    o = e["op"]
    d_prot, d_vl = e["dist_prot"], e["dist_vl"]
    lines = [
        f"📈 {o['ticker']} {o['option_type']} llegó a +{e['nivel']}% (ganancia de la prima, modelo)",
        f"Operación #{o['id']} · entrada {o['timestamp_entry'][:16]} UTC a {_money(o.get('spot_entry'))}",
        f"Momento: {e['ts'][:16]} UTC, {e['dias']:.1f} días después de la entrada",
        f"Precio del subyacente: {_money(e['spot'])}",
        f"Strike del protocolo {_money(o.get('strike_usd'))}: "
        + ("sin dato" if d_prot is None else f"{d_prot:+.1f}% → {distance_label(d_prot, near)}"),
        (f"Strike de Vicente {_money(o.get('strike_vl'))}: {d_vl:+.1f}% → {distance_label(d_vl, near)}"
         if d_vl is not None else "Strike de Vicente: sin dato"),
        f"Estado: {e['estado']}",
    ]
    return "\n".join(lines)


def vl_key(o: dict) -> str:
    return f"{o['signal_key']}|vl"


def format_vl(o: dict) -> str:
    result = {"take_profit": "take-profit (+50%)", "stop_loss": "stop-loss (−50%)", "expiry": "vencimiento"}.get(
        o.get("reason_exit"), "operación abierta" if o.get("estado") == "open" else "sin dato")
    lines = [f"🎯 Vicente acertó: {o['ticker']} {o['option_type']}",
             f"Señal del {o['signal_timestamp'][:10]} · strike de Vicente {_money(o.get('strike_vl'))} · vencía el "
             f"{o.get('vl_vencimiento') or 'sin dato'}",
             f"Tocó su strike el {o.get('vl_fecha_toque')} ({o.get('vl_dias_toque')} días después de la señal)"
             + (" — ya estaba en el dinero al publicarse" if vl_easy(o) else ""),
             f"Resultado del protocolo (45 días, +50% / −50%): {result}"]
    return "\n".join(lines)


def plan(events: list[dict], notified: list[str] | None) -> tuple[str, list[dict]]:
    """('init' | 'nada' | 'resumen' | 'cada_uno', eventos nuevos)."""
    if notified is None:
        return "init", []
    done = set(notified)
    new = [e for e in events if event_key(e) not in done]
    if not new:
        return "nada", []
    return ("resumen" if len(new) > MAX_EACH else "cada_uno"), new


def pack(texts: list[str], limit: int = TELEGRAM_LIMIT) -> list[list[int]]:
    """Agrupa avisos en mensajes de hasta `limit` caracteres; devuelve indices por mensaje."""
    groups, cur, size = [], [], 0
    for i, t in enumerate(texts):
        if cur and size + len(t) + 2 > limit:
            groups.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t) + 2
    if cur:
        groups.append(cur)
    return groups


def send(token: str, chat_id: str, text: str) -> bool:
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except urllib.error.HTTPError as e:   # sin URL en el log: llevaria el token
        log_event(f"Script 8: Telegram respondio HTTP {e.code}", "WARNING")
    except Exception as e:
        log_event(f"Script 8: no pude enviar a Telegram ({type(e).__name__})", "WARNING")
    return False


def notify_levels(token: str, chat_id: str, events: list[dict], near: float) -> bool:
    notified = load_state().get("levels_notified")
    mode, new = plan(events, notified)
    if mode == "init":
        text = ("✅ Avisos activados. Te aviso cuando una operación en papel de Vicente Luz llegue a +25%, +50%, "
                f"+75% o +100%. Ya había {len(events)} niveles alcanzados (están en el reporte semanal). "
                "Dinero ficticio; análisis de datos, no asesoría financiera.")
        if not send(token, chat_id, text):
            return False
        update_state(levels_notified=sorted(event_key(e) for e in events))
        log_event(f"Script 8: avisos activados; {len(events)} niveles historicos marcados sin enviar")
        return True
    if mode == "nada":
        return True
    done = set(notified)
    if mode == "resumen":
        by_level = {lv: sum(1 for e in new if e["nivel"] == lv) for lv in sorted({e["nivel"] for e in new})}
        text = (f"📊 {len(new)} niveles nuevos alcanzados (posible reprocesamiento de datos): "
                + ", ".join(f"+{lv}%: {n}" for lv, n in by_level.items()) + ". Detalle en el reporte semanal.")
        if send(token, chat_id, text):
            done |= {event_key(e) for e in new}
    else:
        texts = [format_event(e, near) for e in new]
        for group in pack(texts):
            if send(token, chat_id, "\n\n".join(texts[i] for i in group)):
                done |= {event_key(new[i]) for i in group}
    sent = len(done) - len(set(notified))
    update_state(levels_notified=sorted(done))
    log_event(f"Script 8: {sent} de {len(new)} niveles nuevos avisados por Telegram")
    return sent == len(new)


def notify_vl(token: str, chat_id: str, ops: list[dict]) -> bool:
    hits = sorted((o for o in ops if o.get("vl_estado") == "acerto"), key=lambda o: o.get("vl_fecha_toque") or "")
    notified = load_state().get("vl_notified")
    if notified is None:   # primera vez: sin enviar el historico
        update_state(vl_notified=sorted(vl_key(o) for o in hits))
        log_event(f"Script 8: {len(hits)} aciertos historicos de Vicente marcados sin enviar")
        return True
    done = set(notified)
    new = [o for o in hits if vl_key(o) not in done]
    if not new:
        return True
    if len(new) > MAX_EACH:
        if send(token, chat_id, f"🎯 {len(new)} aciertos nuevos de Vicente (posible reprocesamiento). "
                                "Detalle con /aciertos o en el reporte semanal."):
            done |= {vl_key(o) for o in new}
    else:
        texts = [format_vl(o) for o in new]
        for group in pack(texts):
            if send(token, chat_id, "\n\n".join(texts[i] for i in group)):
                done |= {vl_key(new[i]) for i in group}
    sent = len(done) - len(set(notified))
    update_state(vl_notified=sorted(done))
    log_event(f"Script 8: {sent} de {len(new)} aciertos de Vicente avisados por Telegram")
    return sent == len(new)


def main() -> int:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log_event("Script 8: sin TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID; avisos por Telegram desactivados")
        return 0
    cfg = get_config()
    ops = ops_of(load_data(cfg), "VICENTE_LUZ")
    ok_levels = notify_levels(token, chat_id, level_events(ops), float(cfg.get("strike_near_pct", 2)))
    ok_vl = notify_vl(token, chat_id, ops)
    return 0 if ok_levels and ok_vl else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_event(f"Script 8 fallo: {type(e).__name__}", "ERROR")
        sys.exit(1)
