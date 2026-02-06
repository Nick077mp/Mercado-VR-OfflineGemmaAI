import re
import requests
from typing import List, Dict, Optional, Tuple

# ==============================
# CONFIGURACIÓN
# ==============================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "customer"
MAX_HISTORY = 20
MAX_PRODUCTS = 5  # Límite de productos por compra

# ==============================
# ESTADOS
# ==============================
STATE_NEGOTIATING = "NEGOTIATING"
STATE_BUILDING_ORDER = "BUILDING_ORDER"
STATE_READY_TO_PAY = "READY_TO_PAY"
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
- Compra entre 3 y 5 productos en total (NO MÁS)
- Mantén una lista mental de lo que has pedido
- Cuando ya tengas suficientes productos, di que con eso es todo
- Ten una conversación natural, se fluido, conversador pero sin exagerar
- Nunca vendes
- Usas pesos colombianos (COP), nunca dólares
- Respeta el estado actual de la conversación
- Cuando pagas, te despides y no hablas más
"""

# ==============================
# INSTRUCCIONES POR ESTADO
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
    
    STATE_BUILDING_ORDER: """
🔹 ESTADO: ARMANDO PEDIDO - COMPRANDO MÁS PRODUCTOS
Comportamiento:
- El precio de productos anteriores YA ESTÁ ACEPTADO Y CERRADO
- NO vuelvas a negociar precios que ya aceptaste
- Pregunta por otros productos que quieras comprar
- Lleva la cuenta de cuántos productos has pedido (máximo 5 en total)
- NO repitas productos que ya pediste
- Puedes negociar el precio de NUEVOS productos solamente
- Cuando tengas entre 3 y 5 productos, procede al pago diciendo "con eso es todo"
- Si el vendedor pregunta qué llevas, LISTA todos los productos que has pedido
Ejemplo: "¿Qué más tiene?" o "¿Tiene cebollas?" o "Con eso es todo, ¿cuánto sería?"
    """,
    
    STATE_READY_TO_PAY: """
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
# CONTEO Y TRACKING DE PRODUCTOS
# ==============================
def count_products_purchased(history: List[Dict[str, str]]) -> int:
    """
    Cuenta productos comprados analizando el historial.
    Busca patrones de compra en mensajes del comprador (assistant).
    """
    products = extract_product_list(history)
    return len(products)

def extract_product_list(history: List[Dict[str, str]]) -> List[str]:
    """
    Extrae nombres de productos mencionados por el comprador en el historial.
    """
    products = set()
    
    # Patrones de compra del comprador
    buy_patterns = [
        r"\b(?:me\s+da|deme|déme|quiero|llevo|me\s+llevo|necesito|dame|también)\s+(?:\d+\s+)?(?:kilos?\s+de\s+|libras?\s+de\s+|libra\s+de\s+)?(\w+)",
        r"\b(?:tiene|hay)\s+(\w+)",
        r"\b(\w+)\s+(?:a\s+cuánto|a\s+como|cuánto\s+vale|cuánto\s+cuesta)",
    ]
    
    # Palabras a ignorar (no son productos)
    ignore_words = {
        "usted", "algo", "más", "mas", "todo", "eso", "nada", "favor",
        "precio", "plata", "pesos", "cobro", "pago", "total", "cuenta",
        "bien", "bueno", "listo", "dale", "gracias", "señor", "vecino",
        "qué", "que", "cómo", "como", "cuánto", "cuanto", "también",
        "el", "la", "los", "las", "un", "una", "unos", "unas",
    }
    
    for msg in history:
        if msg["role"] == "assistant":  # Solo mensajes del comprador (bot)
            text = msg["content"].lower()
            for pattern in buy_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    word = match.strip().lower()
                    if word and len(word) > 2 and word not in ignore_words:
                        products.add(word)
    
    return list(products)

def seller_asks_what_to_buy(text: str) -> bool:
    """
    Detecta cuando el vendedor pregunta qué productos se lleva el comprador.
    """
    t = text.lower()
    patterns = [
        r"\bqu[eé]\s+(se\s+)?lleva",
        r"\bqu[eé]\s+le\s+(empaco|alisto)",
        r"\bqu[eé]\s+m[aá]s\s+le\s+(doy|empaco)",
        r"\bqu[eé]\s+productos",
        r"\bqu[eé]\s+necesita",
        r"\balgo\s+m[aá]s",
        r"\bnecesita\s+algo\s+m[aá]s",
    ]
    for p in patterns:
        if re.search(p, t, re.IGNORECASE):
            return True
    return False

# ==============================
# DETECCIÓN (REGEX / HEURÍSTICAS)
# ==============================

_COP_AMOUNT_RE = re.compile(
    r"(?:(?:\$)\s*)?"
    r"(?:(?:\d{1,3}(?:[.,]\d{3})+)|(?:\d+))"
    r"(?:\s*(?:pesos|cop))?" 
    r"|\b\d+\s*mil\b",
    re.IGNORECASE
)

_NEGATION_RE = re.compile(r"\b(no|nunca|jam[aá]s|sin)\b", re.IGNORECASE)

def _has_cop_amount(text: str) -> bool:
    return bool(_COP_AMOUNT_RE.search(text))

def seller_asks_payment(text: str) -> bool:
    """Detecta pregunta/invitación de pago del vendedor."""
    t = text.lower()

    if _NEGATION_RE.search(t) and any(k in t for k in ["qr", "transferencia", "efectivo", "tarjeta"]):
        pass

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

    if re.search(r"\b(c[oó]mo\s+desea\s+pagar|c[oó]mo\s+va\s+a\s+pagar)\b", t):
        return True

    return False

def seller_confirms_price(text: str) -> bool:
    """Detecta confirmación/cierre del precio."""
    t = text.lower()

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
    """Detecta si el comprador quiere comprar más productos."""
    t = text.lower()
    more_shopping_phrases = [
        "qué más", "que mas", "algo más", "algo mas", "también quiero",
        "y dame", "y me da", "necesito", "tiene", "hay",
        "¿qué tiene", "que tiene", "¿me da", "me da"
    ]
    return any(phrase in t for phrase in more_shopping_phrases)

def buyer_ready_to_pay(text: str) -> bool:
    """Detecta si el comprador está listo para proceder al pago."""
    t = text.lower()
    payment_ready_phrases = [
        "eso es todo", "nada más", "solo eso", "con eso",
        "con eso es todo", "generar el cobro", "cómo pago", "como pago",
        "listo entonces", "ya está", "ya esta", "perfecto", "dele"
    ]
    return any(phrase in t for phrase in payment_ready_phrases)

# ==============================
# RESPUESTAS CONTROLADAS
# ==============================
def payment_response() -> str:
    return "Pago por QR porque mi nieta me enseñó. Muchas gracias, que tenga buen día."

def ready_to_pay_response() -> str:
    return "Listo, con eso es todo. ¿Me genera el cobro por favor?"

# ==============================
# GUARDRAILS
# ==============================
def _limit_to_two_sentences(text: str) -> str:
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
        if ch in "?!":
            is_end = True
        elif ch == ".":
            prev_c = s[i-1] if i > 0 else ""
            next_c = s[i+1] if i + 1 < len(s) else ""
            if not (prev_c.isdigit() and next_c.isdigit()):
                is_end = True

        if is_end:
            seg = "".join(sentence).strip()
            if seg:
                out.append(seg)
                count += 1
            sentence = []

        i += 1

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
    if re.search(r"\b(vendo|le\s+vendo|te\s+vendo|vendemos)\b", t):
        return True
    return False

def _is_reopening_negotiation(text: str) -> bool:
    """
    Detecta si el BOT está intentando REABRIR negociación.
    """
    t = text.lower()
    
    negotiation_patterns = [
        r"\bme\s+lo\s+deja\b",
        r"\bme\s+la\s+deja\b",
        r"\bme\s+los\s+deja\b",
        r"\bme\s+las\s+deja\b",
        r"\bdéjemelo\b",
        r"\bdéjamelo\b",
        r"\brebaj[ae]\b",
        r"\bdescuento\b",
        r"\bmás\s+barat[oa]\b",
        r"\bmenos\b.*\b(plata|precio)\b",
        r"\bregáleme\b",
        r"\bregálame\b",
        r"\bcafecito\b",
        r"\bpor\s+ese\s+precio\s+no\b",
    ]
    
    for pattern in negotiation_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    
    return False

def _apply_guardrails(text: str, state: str) -> str:
    s = sanitize_text(text)
    if not s:
        return s

    s = _limit_to_two_sentences(s)

    if _violates_role_or_currency(s):
        return "Vecino, yo estoy comprando y pago en pesos. ¿En cuánto me lo deja entonces?"

    # Guardrail para READY_TO_PAY - solo bloquea reapertura de negociación
    if state == STATE_READY_TO_PAY:
        if _is_reopening_negotiation(s):
            return ready_to_pay_response()
        if buyer_ready_to_pay(s):
            return s

    return s

# ==============================
# CONSTRUCCIÓN DE PROMPT CON ESTADOS ACTIVOS + CONTEO DE PRODUCTOS
# ==============================
def build_prompt_with_history(
    history: List[Dict[str, str]], 
    user_text: str, 
    state: str
) -> str:
    trimmed_history = trim_history(history)
    
    prompt_parts = []
    
    # 1. System prompt base
    prompt_parts.append(SYSTEM_PROMPT.strip())
    
    # 2. Instrucciones específicas del estado actual
    state_instruction = STATE_INSTRUCTIONS.get(state, STATE_INSTRUCTIONS[STATE_NEGOTIATING])
    prompt_parts.append("\n" + state_instruction)
    
    # 3. Conteo de productos (para BUILDING_ORDER y READY_TO_PAY)
    if state in (STATE_BUILDING_ORDER, STATE_READY_TO_PAY):
        product_count = count_products_purchased(trimmed_history)
        product_list = extract_product_list(trimmed_history)
        
        prompt_parts.append(f"\n📦 PRODUCTOS PEDIDOS HASTA AHORA: {product_count} de {MAX_PRODUCTS} máximo")
        if product_list:
            prompt_parts.append(f"📋 Lista mental de productos: {', '.join(product_list)}")
        
        if product_count >= MAX_PRODUCTS:
            prompt_parts.append("⚠️ YA ALCANZASTE EL LÍMITE DE PRODUCTOS. Di 'con eso es todo' y pide el cobro.")
        elif product_count >= MAX_PRODUCTS - 1:
            prompt_parts.append("⚠️ Estás cerca del límite. Puedes pedir UN producto más como máximo.")
        
        # Si el vendedor pregunta qué lleva, instruir al bot a listar
        if seller_asks_what_to_buy(user_text):
            prompt_parts.append("ℹ️ El vendedor pregunta qué llevas. LISTA todos los productos que has pedido.")
    
    # 4. Instrucciones generales
    prompt_parts.append("Instrucciones generales: Máximo 2 frases. TÚ ERES EL COMPRADOR.\n")
    
    # 5. Historial de conversación
    if trimmed_history:
        prompt_parts.append("--- Conversación previa ---")
        for msg in trimmed_history:
            role_label = "Vendedor" if msg["role"] == "user" else "Tú (Comprador)"
            prompt_parts.append(f"{role_label}: {msg['content']}")
        prompt_parts.append("--- Fin conversación previa ---\n")
    
    # 6. Mensaje actual
    prompt_parts.append(f"Vendedor dice ahora: {user_text}")
    prompt_parts.append("\nTu respuesta (máximo 2 frases como comprador):")
    
    return "\n".join(prompt_parts)

# ==============================
# DETECCIÓN DE TRANSICIONES DE ESTADO
# ==============================
def detect_state_transition(
    current_state: str,
    user_text: str,
    assistant_text: str,
    history: List[Dict[str, str]]
) -> str:
    user_lower = user_text.lower()
    assistant_lower = assistant_text.lower()
    
    # NEGOTIATING → BUILDING_ORDER
    if current_state == STATE_NEGOTIATING:
        # Si el comprador acepta el primer producto (precio aceptado)
        accept_phrases = ["listo", "bueno", "está bien", "ok", "dale", "sí", "si",
                         "me llevo", "me los llevo", "me lo llevo", "perfecto", "de una"]
        if any(phrase in assistant_lower for phrase in accept_phrases):
            # Verificar que hay contexto de precio/producto
            if seller_confirms_price(user_text) or _has_cop_amount(user_text):
                print(f"🔄 Transición: {STATE_NEGOTIATING} → {STATE_BUILDING_ORDER}")
                return STATE_BUILDING_ORDER
            
            # También transicionar si el comprador acepta después de rechazos
            rejection_count = sum(1 for msg in history[-6:] 
                                if msg["role"] == "user" and 
                                any(word in msg["content"].lower() for word in ["no", "precio fijo", "no puedo"]))
            if rejection_count >= 2:
                print(f"🔄 Transición: {STATE_NEGOTIATING} → {STATE_BUILDING_ORDER} (rechazos múltiples)")
                return STATE_BUILDING_ORDER
    
    # BUILDING_ORDER → READY_TO_PAY
    elif current_state == STATE_BUILDING_ORDER:
        # Si el comprador dice que terminó de comprar
        if buyer_ready_to_pay(assistant_text):
            print(f"🔄 Transición: {STATE_BUILDING_ORDER} → {STATE_READY_TO_PAY}")
            return STATE_READY_TO_PAY
        
        # Si vendedor da total y comprador confirma
        if seller_confirms_price(user_text):
            ready_phrases = ["listo", "perfecto", "cómo pago", "como pago", "generar"]
            if any(phrase in assistant_lower for phrase in ready_phrases):
                print(f"🔄 Transición: {STATE_BUILDING_ORDER} → {STATE_READY_TO_PAY}")
                return STATE_READY_TO_PAY
        
        # Si alcanzó el límite de productos
        product_count = count_products_purchased(history)
        if product_count >= MAX_PRODUCTS:
            print(f"🔄 Transición: {STATE_BUILDING_ORDER} → {STATE_READY_TO_PAY} (límite de productos)")
            return STATE_READY_TO_PAY
    
    # READY_TO_PAY → FINISHED
    elif current_state == STATE_READY_TO_PAY:
        if seller_asks_payment(user_text):
            print(f"🔄 Transición: {STATE_READY_TO_PAY} → {STATE_FINISHED}")
            return STATE_FINISHED
    
    return current_state

# ==============================
# MOTOR PRINCIPAL
# ==============================
def ollama_generate(
    history: List[Dict[str, str]],
    user_text: str,
    state: str
) -> Tuple[Optional[str], str]:
    user_text = sanitize_text(user_text)

    # ESTADO FINAL → SILENCIO ABSOLUTO
    if state == STATE_FINISHED:
        print("🔇 Estado FINISHED - No se genera respuesta")
        return None, STATE_FINISHED

    # Si vendedor pide pago → responde controlado y termina
    if seller_asks_payment(user_text):
        payment_resp = payment_response()
        print("💳 Solicitud de pago detectada - Finalizando conversación")
        return payment_resp, STATE_FINISHED

    # Chequeo suave de límite de productos
    if state == STATE_BUILDING_ORDER:
        product_count = count_products_purchased(history)
        if product_count >= MAX_PRODUCTS:
            print(f"📦 Límite de productos alcanzado ({product_count}/{MAX_PRODUCTS})")
            return ready_to_pay_response(), STATE_READY_TO_PAY

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

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        assistant_text = response.json().get("response", "")
    except Exception as e:
        print(f"❌ Error Ollama: {e}")
        assistant_text = "Disculpe vecino, no le entendí bien. ¿Me lo repite por favor?"

    # Aplicar guardrails
    assistant_text = _apply_guardrails(assistant_text, state)
    
    # Detectar transición de estado
    new_state = detect_state_transition(state, user_text, assistant_text, history)
    
    if new_state != state:
        print(f"📊 Estado actualizado: {state} → {new_state}")
    
    return assistant_text, new_state
