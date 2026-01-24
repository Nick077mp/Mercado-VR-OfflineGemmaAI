import re
import requests
from typing import List, Dict, Optional, Tuple

# ==============================
# CONFIGURACIÓN
# ==============================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "cliente"
MAX_HISTORY = 8

# ==============================
# ESTADOS
# ==============================
STATE_NEGOTIATING = "NEGOTIATING"
STATE_ADDITIONAL_SHOPPING = "ADDITIONAL_SHOPPING"
STATE_ACCEPTED = "PRICE_ACCEPTED"
STATE_PAYMENT = "PAYMENT_REQUESTED"
STATE_FINISHED = "FINISHED"

# ==============================
# SYSTEM PROMPT
# ==============================
SYSTEM_PROMPT = """
Eres un campesino colombiano que compra en la plaza de mercado.
Hablas natural, tranquilo y educado.

Reglas estrictas:
- Siempre eres el COMPRADOR
- Compra minimo 2 productos
- Ten una conversación natural, se fluido, conversador pero sin exagerar
- Nunca vendes
- Usas pesos colombianos (COP), nunca dólares
- Si el precio está aceptado, NO vuelvas a discutirlo
- Si no logras negociar el precio, responde educadamente y termina la conversación
- Cuando pagas, te despides y no hablas más
"""

# ==============================
# UTILIDADES
# ==============================
def sanitize_text(text: Optional[str]) -> str:
    return text.strip() if text else ""

def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history

# ==============================
# DETECCIÓN (REGEX / HEURÍSTICAS)
# ==============================

# COP: 3000 / 3.000 / 3,000 / $3.000 / 10 mil / 10mil / 10.000
_COP_AMOUNT_RE = re.compile(
    r"(?:(?:\$)\s*)?"
    r"(?:(?:\d{1,3}(?:[.,]\d{3})+)|(?:\d+))"
    r"(?:\s*(?:pesos|cop))?"
    r"|\b\d+\s*mil\b",
    re.IGNORECASE
)

# Negación (simple): para evitar gatillar "qr" en "no tengo qr"
_NEGATION_RE = re.compile(r"\b(no|nunca|jam[aá]s|sin)\b", re.IGNORECASE)

def _has_cop_amount(text: str) -> bool:
    return bool(_COP_AMOUNT_RE.search(text))

def seller_asks_payment(text: str) -> bool:
    """
    Detecta pregunta/invitación de pago del vendedor de forma más robusta.
    Evita falsos positivos por menciones no interrogativas o negadas.
    """
    t = text.lower()

    # Si hay negación fuerte y solo menciona métodos, no lo tomes como solicitud
    # (ej: "no tengo qr", "sin transferencia")
    if _NEGATION_RE.search(t) and any(k in t for k in ["qr", "transferencia", "efectivo", "tarjeta"]):
        # Aun puede ser pregunta, pero preferimos NO cerrar por error.
        # Se seguirá el flujo normal del LLM.
        pass

    # Patrones típicos de pregunta de pago
    patterns = [
        r"\b(c[oó]mo|con qu[eé]|de qu[eé] manera)\b.*\b(paga|pagar|desea pagar|va a pagar)\b",
        r"\b(m[eé]todo|forma)\s+de\s+pago\b",
        r"\b(es|ser[ií]a)\b.*\b(efectivo|qr|transferencia|tarjeta)\b.*\b(o)\b",
        r"\b(efectivo)\s+o\s+(mediante\s+)?\b(qr|transferencia|tarjeta)\b",
        r"\b(le)\s+recibo\b.*\b(efectivo|qr|transferencia|tarjeta)\b",
        r"\b(desea|quiere|va\s+a)\s+pagar\b.*\b(efectivo|qr|transferencia|tarjeta)\b.*\b(o)\b",
    ]
    for p in patterns:
        if re.search(p, t, re.IGNORECASE):
            # Requiere señal de pregunta o intención de cobro
            if "?" in t or "pagar" in t or "paga" in t or "recibo" in t or "metodo" in t or "método" in t:
                return True

    # Casos cortos muy comunes
    if re.search(r"\b(c[oó]mo\s+desea\s+pagar|c[oó]mo\s+va\s+a\s+pagar)\b", t):
        return True

    return False

def seller_confirms_price(text: str) -> bool:
    """
    Detecta confirmación/cierre del precio.
    Requiere:
    - lenguaje de cierre (queda / listo / serían / en total / entonces / le queda)
    - y presencia de monto (COP) o total
    """
    t = text.lower()

    # Evita tomar como confirmación una mera aclaración "precio por kilo"
    if "precio por" in t or "por el kilo" in t or "por kilo" in t:
        # esto suele ser explicación, no cierre
        return False

    closing_markers = [
    "listo", "de una", "perfecto", "quedamos", "queda en", "le queda en",
    "entonces serían", "serían en total", "en total", "total sería", "confirmado",
    "dale", "hágale", "hagale", "dejémoslo", "dejamos", "lo dejo", "se lo dejo"
    ]

    has_close = any(m in t for m in closing_markers)
    has_amount = _has_cop_amount(t) or ("total" in t and any(ch.isdigit() for ch in t))

    return bool(has_close and has_amount)

# ==============================
# RESPUESTAS CONTROLADAS
# ==============================
def payment_response() -> str:
    # Máximo 2 frases (estricto)
    return "Pago por QR porque mi nieta me enseñó. Muchas gracias, que tenga buen día."

def accepted_ack_response() -> str:
    # Para estado ACCEPTED si el LLM se desvía, usamos plantilla segura
    return "Listo, quedamos con ese precio. ¿Me genera el cobro por favor?"

def additional_shopping_ready() -> str:
    # Cuando está listo para proceder al pago después de compras adicionales
    return "Listo, entonces me llevo todo. ¿Me genera el cobro por favor?"

# ==============================
# GUARDRAILS
# ==============================
def _limit_to_two_sentences(text: str) -> str:
    """
    Recorta a máximo 2 oraciones sin cortar números tipo 3.000.
    Considera . ? ! como fin de oración si el punto NO está entre dígitos.
    """
    s = sanitize_text(text)
    if not s:
        return s

    out = []
    sentence = []
    count = 0
    i = 0
    while i < len(s) and count < 2:
        ch = s[i]
        sentence.append(ch)

        is_end = False
        if ch in "?!" :
            is_end = True
        elif ch == ".":
            prev_c = s[i-1] if i > 0 else ""
            next_c = s[i+1] if i + 1 < len(s) else ""
            # no cortar 3.000 / 10.000
            if not (prev_c.isdigit() and next_c.isdigit()):
                is_end = True

        if is_end:
            seg = "".join(sentence).strip()
            if seg:
                out.append(seg)
                count += 1
            sentence = []

        i += 1

    # Si no llegamos a 2 oraciones, agrega resto
    if count < 2:
        tail = "".join(sentence).strip()
        if tail:
            out.append(tail)

    result = " ".join(out).strip()
    return result

def _violates_role_or_currency(text: str) -> bool:
    t = text.lower()
    forbidden = ["usd", "dólar", "dolar", "dolares", "dólares"]
    if any(w in t for w in forbidden):
        return True
    # evitar vender
    if re.search(r"\b(vendo|le\s+vendo|te\s+vendo|vendemos|vendo)\b", t):
        return True
    return False

def _mentions_price_or_negotiation(text: str) -> bool:
    t = text.lower()
    # números / símbolos / negociación
    if _has_cop_amount(t) or "$" in t:
        return True
    if any(w in t for w in ["rebaja", "descuento", "le dejo", "ofrezco", "negoci", "cuánto", "cuanto", "más barato", "menos"]):
        return True
    return False

def _wants_to_proceed_to_payment(text: str) -> bool:
    """Detecta si el comprador quiere proceder al pago sin más compras"""
    t = text.lower()
    payment_ready_phrases = [
        "me llevo", "eso es todo", "nada más", "solo eso", "con eso",
        "generar el cobro", "proceder", "pagar", "listo entonces"
    ]
    return any(phrase in t for phrase in payment_ready_phrases)

def _apply_guardrails(text: str, state: str) -> str:
    """
    Enforce:
    - max 2 sentences
    - never sell
    - COP only
    - if accepted: no price negotiation
    """
    s = sanitize_text(text)
    if not s:
        return s

    s = _limit_to_two_sentences(s)

    if _violates_role_or_currency(s):
        # Plantilla segura en español, 2 frases
        return "Vecino, yo estoy comprando y pago en pesos. ¿En cuánto me lo deja entonces?"

    if state == STATE_ACCEPTED and _mentions_price_or_negotiation(s):
        return accepted_ack_response()
    
    if state == STATE_ADDITIONAL_SHOPPING and _wants_to_proceed_to_payment(s):
        return additional_shopping_ready()

    return s

# ==============================
# PROMPT SIMPLIFICADO (SIN HISTORIAL COMPLETO)
# ==============================
def build_simple_prompt(user_text: str, state: str) -> str:
    """
    Construye un prompt simple sin historial completo para evitar confusión.
    Solo incluye el contexto mínimo necesario.
    """
    prompt = SYSTEM_PROMPT.strip() + "\n\n"
    prompt += f"Estado actual: {state}\n"
    prompt += "Recuerda: máximo 2 frases. TÚ ERES EL COMPRADOR.\n\n"
    prompt += f"El vendedor dice: {user_text}\n"
    prompt += "Tu respuesta (máximo 2 frases como comprador):"
    return prompt

# ==============================
# MOTOR PRINCIPAL
# ==============================
def ollama_generate(
    history: List[Dict[str, str]],
    user_text: str,
    state: str
) -> Tuple[Optional[str], str]:
    user_text = sanitize_text(user_text)
    # No necesitamos el historial complejo, solo el estado actual

    # 🔇 ESTADO FINAL → SILENCIO ABSOLUTO
    if state == STATE_FINISHED:
        return None, STATE_FINISHED

    # 💳 Si vendedor pide pago → responde controlado y termina
    if seller_asks_payment(user_text):
        payment_resp = payment_response()
        return payment_resp, STATE_FINISHED

    # 🧠 Actualización de estado por confirmación del vendedor
    new_state = state
    if state == STATE_NEGOTIATING and seller_confirms_price(user_text):
        new_state = STATE_ADDITIONAL_SHOPPING
    
    # Transición de compras adicionales a precio aceptado
    if state == STATE_ADDITIONAL_SHOPPING and _wants_to_proceed_to_payment(user_text):
        new_state = STATE_ACCEPTED

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "prompt": build_simple_prompt(user_text, new_state),
        "temperature": 0.6
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        assistant_text = response.json().get("response", "")
    except Exception:
        # No “hablar” errores técnicos largos: respuesta humana y corta
        assistant_text = "Disculpe vecino, no le entendí bien. ¿Me lo repite por favor?"

    assistant_text = _apply_guardrails(assistant_text, new_state)
    return assistant_text, new_state
