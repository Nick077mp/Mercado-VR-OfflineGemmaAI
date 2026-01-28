import re
import requests
from typing import List, Dict, Optional, Tuple

# ==============================
# CONFIGURACIÓN
# ==============================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "customer"
MAX_HISTORY = 20  # ✅ Aumentado para mejor contexto (gemma3:12b puede manejar más)


def check_ollama_available(timeout: float = 2.0) -> bool:
    """Chequeo ligero de salud de Ollama.

    En lugar de generar texto completo (caro y lento), hace una llamada
    rápida a la API de Ollama para verificar que el servidor responde.
    """
    try:
        # Derivar URL base a partir de OLLAMA_URL (e.g. http://localhost:11434)
        base_url = OLLAMA_URL.split("/api/")[0]
        # /api/tags es un endpoint muy liviano que lista modelos
        resp = requests.get(f"{base_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Error en check_ollama_available: {e}")
        return False
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
- Respeta el estado actual de la conversación
- Cuando pagas, te despides y no hablas más
"""

# ==============================
# INSTRUCCIONES POR ESTADO ✅ NUEVO
# ==============================
STATE_INSTRUCTIONS = {
    STATE_NEGOTIATING: """
🔹 ESTADO: NEGOCIACIÓN ACTIVA
Comportamiento:
- Puedes pedir descuentos y regatear precios
- Si te rechazan un descuento, puedes insistir 1-2 veces más con ofertas diferentes
- Si te rechazan 2 veces, acepta el precio o retírate educadamente
- No aceptes precios muy altos sin intentar negociar
- Puedes preguntar por otros productos mientras negocias
Ejemplo: "¿Me lo deja en 4 mil?" o "¿Y si compro más me hace descuento?"
    """,
    
    STATE_ADDITIONAL_SHOPPING: """
🔹 ESTADO: PRECIO ACEPTADO - COMPRANDO MÁS PRODUCTOS
Comportamiento:
- El precio de productos anteriores YA ESTÁ ACEPTADO Y CERRADO
- NO vuelvas a negociar precios que ya aceptaste
- Pregunta por otros productos que quieras comprar (mínimo 2 productos en total)
- Puedes negociar el precio de NUEVOS productos solamente
- Cuando termines de comprar, procede al pago
Ejemplo: "¿Qué más tiene?" o "¿Tiene cebollas?" o "Listo, con eso es todo"
    """,
    
    STATE_ACCEPTED: """
🔹 ESTADO: LISTO PARA PAGAR
Comportamiento:
- Ya aceptaste todos los precios y decidiste comprar
- NO vuelvas a pedir descuentos ni a negociar
- Solo confirma el total y pregunta cómo pagar
- Mantén tu decisión de compra firme
Ejemplo: "Listo, ¿cómo pago?" o "Perfecto, ¿me genera el cobro?"
    """,
    
    STATE_FINISHED: """
🔹 ESTADO: CONVERSACIÓN TERMINADA
- Ya pagaste y te despediste
- No respondas más mensajes
    """
}

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
    if _NEGATION_RE.search(t) and any(k in t for k in ["qr", "transferencia", "efectivo", "tarjeta"]):
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
        return False

    closing_markers = [
        "listo", "de una", "perfecto", "quedamos", "queda en", "le queda en",
        "entonces serían", "serían en total", "en total", "total sería", "confirmado",
        "dale", "hágale", "hagale", "dejémoslo", "dejamos", "lo dejo", "se lo dejo"
    ]

    has_close = any(m in t for m in closing_markers)
    has_amount = _has_cop_amount(t) or ("total" in t and any(ch.isdigit() for ch in t))

    return bool(has_close and has_amount)

def buyer_wants_more_products(text: str) -> bool:
    """ 
    Detecta si el comprador quiere comprar más productos
    """
    t = text.lower()
    more_shopping_phrases = [
        "qué más", "que mas", "algo más", "algo mas", "también quiero",
        "y dame", "y me da", "necesito", "tiene", "hay",
        "¿qué tiene", "que tiene", "¿me da", "me da"
    ]
    return any(phrase in t for phrase in more_shopping_phrases)

def buyer_ready_to_pay(text: str) -> bool:
    """
    Detecta si el comprador está listo para proceder al pago
    """
    t = text.lower()
    payment_ready_phrases = [
        "me llevo", "eso es todo", "nada más", "solo eso", "con eso",
        "generar el cobro", "cómo pago", "como pago", "listo entonces",
        "ya está", "ya esta", "perfecto", "dele"
    ]
    return any(phrase in t for phrase in payment_ready_phrases)

# ==============================
# RESPUESTAS CONTROLADAS
# ==============================
def payment_response() -> str:
    # Máximo 2 frases (estricto)
    return "Pago por QR porque mi nieta me enseñó. Muchas gracias, que tenga buen día."

def accepted_ack_response() -> str:
    # Para estado ACCEPTED si el LLM se desvía
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
    if re.search(r"\b(vendo|le\s+vendo|te\s+vendo|vendemos)\b", t):
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

def _is_reopening_negotiation(text: str) -> bool:
    """ 
    ✅ NUEVO: Detecta si el BOT está intentando REABRIR negociación 
    (pedir más descuento después de aceptar precio).
    
    DIFERENCIA con _mentions_price_or_negotiation:
    - Ese detecta CUALQUIER mención de precio/números
    - Este detecta INTENCIÓN de negociar más
    
    Ejemplo bloqueado: "¿Me lo deja en 16.000?"
    Ejemplo permitido: "Me llevo dos kilos de tomate y uno de zanahoria"
    """
    t = text.lower()
    
    # Patrones de REABRIR negociación (bot pidiendo más descuento)
    negotiation_patterns = [
        r"\bme\s+lo\s+deja\b",           # "¿Me lo deja en...?"
        r"\bme\s+la\s+deja\b",           # "¿Me la deja en...?"
        r"\bme\s+los\s+deja\b",          # "¿Me los deja en...?"
        r"\bme\s+las\s+deja\b",          # "¿Me las deja en...?"
        r"\bdéjemelo\b",                  # "Déjemelo en..."
        r"\bdéjamelo\b",                  # "Déjamelo en..."
        r"\brebaj[ae]\b",                 # "rebaja", "rebaje"
        r"\bdescuento\b",                 # "descuento"
        r"\bmás\s+barat[oa]\b",          # "más barato"
        r"\bmenos\b.*\b(plata|precio)\b", # "menos plata/precio"
        r"\bregáleme\b",                  # "regáleme un poco"
        r"\bregálame\b",                  # "regálame"
        r"\bcafecito\b",                  # forma coloquial de pedir rebaja
        r"\bpor\s+ese\s+precio\s+no\b",   # rechazo de precio
    ]
    
    for pattern in negotiation_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    
    return False

def _apply_guardrails(text: str, state: str) -> str:
    """ 
    ✅ MEJORADO: Guardrails más estrictos basados en estados
    """
    s = sanitize_text(text)
    if not s:
        return s

    s = _limit_to_two_sentences(s)

    # Violaciones de rol/moneda
    if _violates_role_or_currency(s):
        return "Vecino, yo estoy comprando y pago en pesos. ¿En cuánto me lo deja entonces?"

    # ✅ NUEVO: Guardrail para ADDITIONAL_SHOPPING
    # Si está en compras adicionales y menciona precio de productos ya aceptados
    if state == STATE_ADDITIONAL_SHOPPING:
        if _mentions_price_or_negotiation(s):
            # Verificar si está pidiendo descuento en productos YA aceptados
            negotiation_words = ["rebaja", "descuento", "menos", "más barato", "regalar", "cafecito"]
            if any(word in s.lower() for word in negotiation_words):
                return "¿Qué más tiene para llevar?"  # Redirigir a comprar más
    
    # ✅ CORREGIDO: Guardrail para ACCEPTED - Menos agresivo
    # Solo bloquea si el bot intenta REABRIR negociación, no por cualquier número
    if state == STATE_ACCEPTED:
        # Solo bloquear si intenta negociar MÁS (no por confirmar productos/totales)
        if _is_reopening_negotiation(s):
            return accepted_ack_response()
        # Si el vendedor pregunta y el bot quiere pagar, permitirlo
        if buyer_ready_to_pay(s):
            return s  # Permitir respuesta natural, no forzar plantilla

    return s

# ==============================
# CONSTRUCCIÓN DE PROMPT CON ESTADOS ACTIVOS ✅ MEJORADO
# ==============================
def build_prompt_with_history(
    history: List[Dict[str, str]], 
    user_text: str, 
    state: str
) -> str:
    """ 
    ✅ MEJORADO: Construye prompt con instrucciones específicas por estado
    
    Formato optimizado para gemma3:
    - System prompt al inicio
    - Instrucciones específicas del estado actual
    - Historial completo de conversación
    - Mensaje actual del usuario
    - Instrucción de respuesta
    """
    # Recortar historial si es muy largo
    trimmed_history = trim_history(history)
    
    # Construir prompt con formato claro
    prompt_parts = []
    
    # 1. System prompt base
    prompt_parts.append(SYSTEM_PROMPT.strip())
    
    # 2. ✅ NUEVO: Instrucciones específicas del estado actual
    state_instruction = STATE_INSTRUCTIONS.get(state, STATE_INSTRUCTIONS[STATE_NEGOTIATING])
    prompt_parts.append("\n" + state_instruction)
    
    # 3. Instrucciones generales
    prompt_parts.append("Instrucciones generales: Máximo 2 frases. TÚ ERES EL COMPRADOR.\n")
    
    # 4. Historial de conversación
    if trimmed_history:
        prompt_parts.append("--- Conversación previa ---")
        for msg in trimmed_history:
            role_label = "Vendedor" if msg["role"] == "user" else "Tú (Comprador)"
            prompt_parts.append(f"{role_label}: {msg['content']}")
        prompt_parts.append("--- Fin conversación previa ---\n")
    
    # 5. Mensaje actual
    prompt_parts.append(f"Vendedor dice ahora: {user_text}")
    prompt_parts.append("\nTu respuesta (máximo 2 frases como comprador):")
    
    return "\n".join(prompt_parts)

# ==============================
# DETECCIÓN DE TRANSICIONES DE ESTADO ✅ MEJORADO
# ==============================
def detect_state_transition(
    current_state: str,
    user_text: str,
    assistant_text: str,
    history: List[Dict[str, str]]
) -> str:
    """ 
    ✅ NUEVO: Detecta transiciones de estado basándose en el contexto completo
    
    Lógica de transiciones:
    NEGOTIATING → ADDITIONAL_SHOPPING: Vendedor confirma precio
    ADDITIONAL_SHOPPING → ACCEPTED: Comprador dice que está listo para pagar
    ACCEPTED → FINISHED: Vendedor solicita pago
    """
    user_lower = user_text.lower()
    assistant_lower = assistant_text.lower()
    
    # NEGOTIATING → ADDITIONAL_SHOPPING
    if current_state == STATE_NEGOTIATING:
        # Si vendedor confirma precio con cierre
        if seller_confirms_price(user_text):
            # Contar cuántas veces se ha rechazado al comprador
            rejection_count = sum(1 for msg in history[-6:] 
                                if msg["role"] == "user" and 
                                any(word in msg["content"].lower() for word in ["no", "precio fijo", "no puedo"]))
            
            # Si el comprador acepta (explícita o implícitamente)
            accept_phrases = ["listo", "bueno", "está bien", "ok", "dale", "sí", "si"]
            if any(phrase in assistant_lower for phrase in accept_phrases):
                print(f"🔄 Transición: {STATE_NEGOTIATING} → {STATE_ADDITIONAL_SHOPPING}")
                return STATE_ADDITIONAL_SHOPPING
            
            # O si ya rechazaron 3+ veces y menciona llevarse productos
            if rejection_count >= 3 and any(phrase in assistant_lower for phrase in ["me llevo", "me los llevo"]):
                print(f"🔄 Transición: {STATE_NEGOTIATING} → {STATE_ADDITIONAL_SHOPPING} (rechazos múltiples)")
                return STATE_ADDITIONAL_SHOPPING
    
    # ADDITIONAL_SHOPPING → ACCEPTED
    elif current_state == STATE_ADDITIONAL_SHOPPING:
        # Si el comprador indica que está listo para pagar
        if buyer_ready_to_pay(assistant_text):
            print(f"🔄 Transición: {STATE_ADDITIONAL_SHOPPING} → {STATE_ACCEPTED}")
            return STATE_ACCEPTED
        
        # Si vendedor da total final y comprador no pide más
        if seller_confirms_price(user_text):
            ready_phrases = ["listo", "perfecto", "cómo pago", "como pago", "generar"]
            if any(phrase in assistant_lower for phrase in ready_phrases):
                print(f"🔄 Transición: {STATE_ADDITIONAL_SHOPPING} → {STATE_ACCEPTED}")
                return STATE_ACCEPTED
    
    # ACCEPTED → FINISHED
    elif current_state == STATE_ACCEPTED:
        # Si vendedor solicita método de pago
        if seller_asks_payment(user_text):
            print(f"🔄 Transición: {STATE_ACCEPTED} → {STATE_FINISHED}")
            return STATE_FINISHED
    
    # Sin transición
    return current_state

# ==============================
# MOTOR PRINCIPAL ✅ ACTUALIZADO
# ==============================
def ollama_generate(
    history: List[Dict[str, str]],
    user_text: str,
    state: str
) -> Tuple[Optional[str], str]:
    user_text = sanitize_text(user_text)

    # 🔇 ESTADO FINAL → SILENCIO ABSOLUTO
    if state == STATE_FINISHED:
        print("🔇 Estado FINISHED - No se genera respuesta")
        return None, STATE_FINISHED

    # 💳 Si vendedor pide pago → responde controlado y termina
    if seller_asks_payment(user_text):
        payment_resp = payment_response()
        print("💳 Solicitud de pago detectada - Finalizando conversación")
        return payment_resp, STATE_FINISHED

    # ✅ USAR PROMPT CON HISTORIAL Y ESTADOS ACTIVOS
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "prompt": build_prompt_with_history(history, user_text, state),
        "temperature": 0.7,
        "options": {
            "num_ctx": 4096,
            "num_predict": 200,
            "top_k": 40,
            "top_p": 0.9
        }
    }

    # Log simple para confirmar modelo usado en pruebas
    print(f"🧠 Llamando a Ollama con modelo: {OLLAMA_MODEL}")

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        assistant_text = response.json().get("response", "")
    except Exception as e:
        print(f"❌ Error Ollama: {e}")
        assistant_text = "Disculpe vecino, no le entendí bien. ¿Me lo repite por favor?"

    # Aplicar guardrails
    assistant_text = _apply_guardrails(assistant_text, state)
    
    # ✅ NUEVO: Detectar transición de estado basándose en contexto completo
    new_state = detect_state_transition(state, user_text, assistant_text, history)
    
    # Debug de estado
    if new_state != state:
        print(f"📊 Estado actualizado: {state} → {new_state}")
    
    return assistant_text, new_state
