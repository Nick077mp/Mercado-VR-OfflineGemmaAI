"""Conversation engine for the AI voice marketplace negotiation assistant.

Manages the full negotiation lifecycle: state machine, price tracking,
prompt construction, guardrails and Ollama LLM streaming.
"""

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL: str = "http://localhost:11434/api/generate"
OLLAMA_MODEL: str = "gemma3:12b"

PERFORMANCE_PROFILES: Dict[str, Dict] = {
    "gpu_full": {
        "num_gpu": 999,
        "num_thread": 1,
        "num_ctx": 4096,
        "num_predict": 280,
    },
    "gpu_hybrid": {
        "num_gpu": 30,
        "num_thread": 6,
        "num_ctx": 2048,
        "num_predict": 150,
        "num_batch": 1024,
    },
    "cpu_only": {
        "num_gpu": 0,
        "num_thread": 6,
        "num_ctx": 2048,
        "num_predict": 150,
    },
}

# Change to switch profiles: "gpu_full" | "gpu_hybrid" | "cpu_only"
ACTIVE_PROFILE_NAME: str = "gpu_hybrid"

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

STATE_NEGOTIATING: str = "NEGOTIATING"
STATE_BUILDING_ORDER: str = "BUILDING_ORDER"
STATE_READY_TO_PAY: str = "READY_TO_PAY"
STATE_PAYMENT: str = "PAYMENT_REQUESTED"
STATE_FINISHED: str = "FINISHED"

MAX_HISTORY: int = 20
MAX_PRODUCTS: int = 5


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def sanitize_text(text: Optional[str]) -> str:
    """Strip whitespace; return empty string for ``None``."""
    return text.strip() if text else ""


def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep only the last *MAX_HISTORY* messages."""
    return history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history


# ---------------------------------------------------------------------------
# Price / product detection (module-level, reusable)
# ---------------------------------------------------------------------------

def extract_price_and_product(text: str) -> Optional[Tuple[str, int]]:
    """Extract product name and price from a seller message.

    Example: ``"Aguacate a 3500 pesos"`` -> ``("aguacate", 3500)``
    """
    t = text.lower()

    price_pattern = (
        r"(?:(?:\$)\s*)?"
        r"(?:(?:\d{1,3}(?:[.,]\d{3})+)|(?:\d+))"
        r"(?:\s*(?:pesos|cop))?"
    )
    prices = re.findall(price_pattern, t)

    if not prices:
        mil_matches = re.findall(r"(\d+)\s*mil", t)
        if mil_matches:
            prices = [str(int(m) * 1000) for m in mil_matches]

    if not prices:
        return None

    price_str = prices[0].replace(".", "").replace(",", "")
    try:
        price = int(price_str)
    except ValueError:
        return None

    matches = re.findall(r"(\w+)\s+(?:a\s+)?(?:en\s+)?(?:\$\s*)?(?:\d+)", t)
    if matches:
        product = matches[0].lower()
        ignore = {"el", "la", "los", "las", "un", "una", "unos", "unas", "tengo", "tiene"}
        if product not in ignore:
            return (product, price)

    return None


def detect_seller_total(text: str) -> Optional[int]:
    """Detect when the seller announces a total.

    Example: ``"El total sería 42 mil pesos"`` -> ``42000``
    """
    t = text.lower()
    if "total" not in t and "serían" not in t and "sería" not in t:
        return None

    mil_matches = re.findall(r"(\d+)\s*mil", t)
    if mil_matches:
        return int(mil_matches[0]) * 1000

    price_matches = re.findall(
        r"(?:\$\s*)?(\d+(?:[.,]\d{3})+|\d+)(?:\s*pesos)?", t,
    )
    if price_matches:
        price_str = price_matches[0].replace(".", "").replace(",", "")
        try:
            return int(price_str)
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# PriceTracker
# ---------------------------------------------------------------------------

class PriceTracker:
    """Tracks products, quantities, prices and validates seller totals."""

    def __init__(self) -> None:
        self.products: Dict[str, Dict[str, int]] = {}
        self.total_calculated: int = 0

    def add_product(self, name: str, quantity: int, price: int) -> None:
        self.products[name.lower()] = {
            "quantity": quantity,
            "price": price,
            "subtotal": quantity * price,
        }
        self._recalculate()

    def _recalculate(self) -> None:
        self.total_calculated = sum(
            p["quantity"] * p["price"] for p in self.products.values()
        )

    def validate_seller_total(self, seller_total: int) -> Dict:
        if self.total_calculated == seller_total:
            return {"valid": True, "correct_total": self.total_calculated, "difference": 0}
        return {
            "valid": False,
            "correct_total": self.total_calculated,
            "seller_total": seller_total,
            "difference": seller_total - self.total_calculated,
            "alert": (
                f"Discrepancy: correct={self.total_calculated}, "
                f"seller={seller_total}, diff={seller_total - self.total_calculated}"
            ),
        }

    def get_summary(self) -> str:
        if not self.products:
            return "No products registered"
        lines = [
            f"- {n}: {d['quantity']} x {d['price']} = {d['subtotal']}"
            for n, d in self.products.items()
        ]
        lines.append(f"TOTAL: {self.total_calculated}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ConversationEngine
# ---------------------------------------------------------------------------

class ConversationEngine:
    """Orchestrates the full conversation lifecycle with the LLM.

    Encapsulates state machine, price tracking, prompt construction,
    Ollama streaming, guardrails and greeting deduplication.
    """

    # -- Compiled regex patterns --

    _COP_AMOUNT_RE = re.compile(
        r"(?:(?:\$)\s*)?"
        r"(?:(?:\d{1,3}(?:[.,]\d{3})+)|(?:\d+))"
        r"(?:\s*(?:pesos|cop))?"
        r"|\b\d+\s*mil\b",
        re.IGNORECASE,
    )
    _NEGATION_RE = re.compile(r"\b(no|nunca|jam[aá]s|sin)\b", re.IGNORECASE)
    _GREETING_RE = re.compile(
        r"\b(buenos\s+días|buenas\s+tardes|buenas\s+noches|hola)\b",
        re.IGNORECASE,
    )
    _GREETING_ALTS: List[str] = [
        "Claro", "Perfecto", "Entiendo", "Muy bien", "De acuerdo",
        "Está bien", "Excelente", "Dale", "Listo",
    ]

    # -- System prompt --

    _SYSTEM_PROMPT: str = (
        "Eres José, un campesino colombiano que compra en la plaza de mercado. "
        "Hablas con naturalidad, tranquilo y educado.\n\n"
        "COMPORTAMIENTO:\n"
        "- Eres conversador: haces preguntas genuinas sobre calidad, frescura y origen de productos\n"
        "- Mantienes una lista mental clara de productos y precios (sin inventar datos)\n"
        "- Eres proactivo: confirmas el total antes de pagar, verificando que coincida con lo acordado\n"
        "- Hablas de forma fluida y natural, sin repetir lo ya dicho\n\n"
        "NEGOCIACIÓN DE PRECIOS:\n"
        "- Cuando el vendedor menciona un precio por primera vez, SIEMPRE pides un pequeño descuento de forma amable\n"
        "- Usas frases naturales como: \"¿Me lo deja en X?\", \"¿Y si me rebaja un poquito?\", \"¿No me hace una rebajita?\"\n"
        "- Propones un precio 10-20% menor al ofrecido (ejemplo: si dice 10 mil, ofreces 8 mil)\n"
        "- Si el vendedor rechaza, puedes intentar UNA vez más con un precio intermedio\n"
        "- Si rechaza dos veces, aceptas el precio sin insistir más\n"
        "- Negocias con respeto, sin ser agresivo ni grosero\n\n"
        "RESTRICCIONES MATEMÁTICAS:\n"
        "- Solo suma precios que el vendedor haya confirmado explícitamente\n"
        "- Si no conoces un precio exacto, pregunta antes de asumir\n"
        "- Verifica mentalmente: cantidad × precio = subtotal\n"
        "- Nunca inventes números ni precios que no se hayan mencionado\n"
        "- Si hay dudas en el cálculo, pide que el vendedor confirme\n\n"
        "CONVERSACIÓN NATURAL:\n"
        "- NO repitas \"Buenos días/tardes\" en cada respuesta\n"
        "- Varía: \"Claro\", \"Perfecto\", \"Entiendo\", \"Muy bien\", \"Excelente\"\n"
        "- Haz preguntas específicas: \"¿Cuántos necesitas?\", \"¿Algo más?\"\n"
        "- Sugiere productos: \"¿Necesitas papas, cebolla, algo más?\"\n"
        "- Pregunta preferencias: \"¿Los prefieres bien maduros?\"\n"
        "- Cuando el vendedor dé precio, pregunta cantidad: \"¿Cuántos/cuántos kilos?\"\n\n"
        "LÍMITES DE COMPRA:\n"
        "- Compras entre 3 y 5 productos máximo\n"
        "- Cuando tengas suficientes, dices \"con eso está bien\" o similar\n"
        "- Siempre eres el COMPRADOR, nunca vendes\n"
        "- Usas pesos colombianos (COP)\n\n"
        "GESTIÓN DE CONTEXTO:\n"
        "- Recuerda solo lo que se ha dicho en esta conversación\n"
        "- No repitas preguntas o comentarios anteriores\n"
        "- Mantén coherencia con lo ya acordado\n"
        "- Cuando pagas, te despides y la conversación termina"
    )

    # -- State-specific instructions --

    _STATE_INSTRUCTIONS: Dict[str, str] = {
        STATE_NEGOTIATING: (
            "ESTADO: NEGOCIACIÓN ACTIVA\n"
            "Comportamiento OBLIGATORIO:\n"
            "- Cuando el vendedor dice un precio, DEBES pedir rebaja antes de aceptar\n"
            "- Propón un precio 10-20% menor: si dice 10 mil, ofrece 8 mil o 8500\n"
            "- Usa frases amables: \"¿Me lo deja en X?\", \"¿No me rebaja un poquito?\", "
            "\"¿Y en X me lo da?\"\n"
            "- Si te rechazan, intenta UNA vez más con precio intermedio\n"
            "- Si rechazan 2 veces, acepta con frase como \"Bueno, está bien, me lo llevo\"\n"
            "- NUNCA aceptes el primer precio sin intentar negociar\n"
            "- Puedes preguntar por otros productos mientras negocias\n"
            "Ejemplos de negociación:\n"
            "- Vendedor: \"La docena a 10 mil\" → Tú: \"¿Me la deja en 8 mil?\"\n"
            "- Vendedor: \"El kilo a 15 mil\" → Tú: \"¿Y en 12 mil me lo da?\"\n"
            "- Vendedor: \"No puedo\" → Tú: \"¿Y en 9 mil entonces?\" (intento 2)"
        ),
        STATE_BUILDING_ORDER: (
            "ESTADO: ARMANDO PEDIDO - COMPRANDO MÁS PRODUCTOS\n"
            "Comportamiento:\n"
            "- El precio de productos anteriores YA ESTÁ ACEPTADO Y CERRADO\n"
            "- NO vuelvas a negociar precios que ya aceptaste\n"
            "- Pregunta por otros productos que quieras comprar\n"
            "- Lleva la cuenta de cuántos productos has pedido (máximo 5 en total)\n"
            "- NO repitas productos que ya pediste\n"
            "- Puedes negociar el precio de NUEVOS productos solamente\n"
            "- Cuando tengas entre 3 y 5 productos, procede al pago diciendo "
            "\"con eso es todo\"\n"
            "- Si el vendedor pregunta qué llevas, LISTA todos los productos que has pedido\n"
            "Ejemplo: \"¿Qué más tiene?\" o \"¿Tiene cebollas?\" o "
            "\"Con eso es todo, ¿cuánto sería?\""
        ),
        STATE_READY_TO_PAY: (
            "ESTADO: LISTO PARA PAGAR\n"
            "Comportamiento:\n"
            "- Suma mentalmente el total de tu pedido y compáralo con el precio "
            "que te da el vendedor\n"
            "- Ya aceptaste todos los precios y decidiste comprar\n"
            "- NO vuelvas a pedir descuentos ni a negociar\n"
            "- Solo confirma el total y pregunta cómo pagar\n"
            "- Mantén tu decisión de compra firme\n"
            "Ejemplo: \"Listo, ¿cómo pago?\" o \"Perfecto, ¿me genera el cobro?\""
        ),
        STATE_FINISHED: (
            "ESTADO: CONVERSACIÓN TERMINADA\n"
            "- Ya pagaste y te despediste\n"
            "- No respondas más mensajes"
        ),
    }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, profile_name: str = ACTIVE_PROFILE_NAME) -> None:
        self.history: List[Dict[str, str]] = []
        self.state: str = STATE_NEGOTIATING
        self.price_tracker: PriceTracker = PriceTracker()

        profile = PERFORMANCE_PROFILES[profile_name]
        self._num_gpu: int = profile["num_gpu"]
        self._num_thread: int = profile["num_thread"]
        self._num_ctx: int = profile["num_ctx"]
        self._num_predict: int = profile["num_predict"]
        self._num_batch: int = profile.get("num_batch", 512)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_message(
        self,
        user_text: str,
        on_first_sentence: Optional[Callable[[str], None]] = None,
    ) -> Tuple[Optional[str], str]:
        """Full pipeline: sanitize -> detect prices -> generate -> update state.

        Returns ``(response_text, current_state)``.
        *response_text* is ``None`` when the conversation is finished.
        """
        text = sanitize_text(user_text)
        if not text:
            return None, self.state

        # Register products/prices mentioned by the seller
        product_info = extract_price_and_product(text)
        if product_info:
            name, price = product_info
            self.price_tracker.add_product(name, 1, price)
            print(f"[LLM] Product registered: {name} x1 @ {price} COP")

        # Generate LLM response
        response = self._generate(text, on_first_sentence)

        # Update history
        if response:
            self.history.extend([
                {"role": "user", "content": text},
                {"role": "assistant", "content": response},
            ])
            self.history = trim_history(self.history)

        return response, self.state

    def reset(self) -> None:
        """Reset conversation to initial state."""
        self.history.clear()
        self.state = STATE_NEGOTIATING
        self.price_tracker = PriceTracker()

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    def _generate(
        self,
        user_text: str,
        on_first_sentence: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Generate a response from Ollama with streaming."""

        # Terminal state
        if self.state == STATE_FINISHED:
            print("[LLM] State FINISHED — no response generated")
            return None

        # Seller asks for payment -> controlled response, end conversation
        if self._seller_asks_payment(user_text):
            print("[LLM] Payment request detected — finishing conversation")
            self.state = STATE_FINISHED
            return self._payment_response()

        # Product limit reached
        if self.state == STATE_BUILDING_ORDER:
            count = self._count_products(self.history)
            if count >= MAX_PRODUCTS:
                print(f"[LLM] Product limit reached ({count}/{MAX_PRODUCTS})")
                self.state = STATE_READY_TO_PAY
                return self._ready_to_pay_response()

        # Validate seller total when ready to pay
        if self.state == STATE_READY_TO_PAY:
            seller_total = detect_seller_total(user_text)
            if seller_total:
                validation = self.price_tracker.validate_seller_total(seller_total)
                if not validation["valid"]:
                    print(f"[LLM] {validation['alert']}")

        # Build payload and stream from Ollama
        prompt = self._build_prompt(user_text)
        payload = {
            "model": OLLAMA_MODEL,
            "stream": True,
            "prompt": prompt,
            "temperature": 0.85,
            "options": {
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
                "num_batch": self._num_batch,
                "top_k": 50,
                "top_p": 0.9,
                "num_gpu": self._num_gpu,
                **({"num_thread": self._num_thread} if self._num_thread > 0 else {}),
            },
        }

        assistant_text = ""
        first_sentence_sent = False

        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=120, stream=True)
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    assistant_text += token

                    # Notify caller with the first complete sentence
                    if not first_sentence_sent and on_first_sentence:
                        for end_char in ".?!":
                            if end_char in assistant_text:
                                idx = assistant_text.index(end_char) + 1
                                first = assistant_text[:idx].strip()
                                if len(first) > 5:
                                    on_first_sentence(first)
                                    first_sentence_sent = True
                                    break

                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue

        except Exception as e:
            print(f"[LLM] Ollama error: {e}")
            assistant_text = "Disculpe vecino, no le entendí bien. ¿Me lo repite por favor?"

        # Post-processing
        assistant_text = self._remove_repeated_greetings(assistant_text)
        assistant_text = self._apply_guardrails(assistant_text)

        # State transition
        old_state = self.state
        self.state = self._compute_next_state(user_text, assistant_text)
        if self.state != old_state:
            print(f"[LLM] State: {old_state} -> {self.state}")

        return assistant_text

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, user_text: str) -> str:
        trimmed = trim_history(self.history)
        parts: List[str] = []

        # System prompt
        parts.append(self._SYSTEM_PROMPT)

        # State-specific instructions
        instruction = self._STATE_INSTRUCTIONS.get(
            self.state, self._STATE_INSTRUCTIONS[STATE_NEGOTIATING],
        )
        parts.append("\n" + instruction)

        # Price validation alert
        if self.state == STATE_READY_TO_PAY:
            seller_total = detect_seller_total(user_text)
            if seller_total:
                validation = self.price_tracker.validate_seller_total(seller_total)
                if not validation["valid"]:
                    parts.append("\nALERTA MATEMÁTICA:")
                    parts.append(f"Vendedor dice: {seller_total} pesos")
                    parts.append(f"Cálculo correcto: {validation['correct_total']} pesos")
                    parts.append(f"Diferencia: {validation['difference']} pesos")
                    parts.append("Debes cuestionar este total. Verifica mentalmente.")

        # Product count info
        if self.state in (STATE_BUILDING_ORDER, STATE_READY_TO_PAY):
            count = self._count_products(trimmed)
            product_list = self._extract_product_list(trimmed)
            parts.append(
                f"\nPRODUCTOS PEDIDOS HASTA AHORA: {count} de {MAX_PRODUCTS} máximo"
            )
            if product_list:
                parts.append(f"Lista mental de productos: {', '.join(product_list)}")

            if count >= MAX_PRODUCTS:
                parts.append(
                    "YA ALCANZASTE EL LÍMITE DE PRODUCTOS. "
                    "Di 'con eso es todo' y pide el cobro."
                )
            elif count >= MAX_PRODUCTS - 1:
                parts.append(
                    "Estás cerca del límite. Puedes pedir UN producto más como máximo."
                )

            if self._seller_asks_what_to_buy(user_text):
                parts.append(
                    "El vendedor pregunta qué llevas. LISTA todos los productos que has pedido."
                )

        parts.append("Instrucciones generales: Máximo 2 frases. TÚ ERES EL COMPRADOR.\n")

        # Conversation history
        if trimmed:
            parts.append("--- Conversación previa ---")
            for msg in trimmed:
                label = "Vendedor" if msg["role"] == "user" else "Tú (Comprador)"
                parts.append(f"{label}: {msg['content']}")
            parts.append("--- Fin conversación previa ---\n")

        parts.append(f"Vendedor dice ahora: {user_text}")
        parts.append("\nTu respuesta (máximo 2 frases como comprador):")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _apply_guardrails(self, text: str) -> str:
        s = sanitize_text(text)
        if not s:
            return s

        s = self._limit_to_two_sentences(s)

        if self._violates_role_or_currency(s):
            return "Vecino, yo estoy comprando y pago en pesos. ¿En cuánto me lo deja entonces?"

        if self.state == STATE_READY_TO_PAY:
            if self._is_reopening_negotiation(s):
                return self._ready_to_pay_response()
            if self._buyer_ready_to_pay(s):
                return s

        return s

    @staticmethod
    def _limit_to_two_sentences(text: str) -> str:
        s = sanitize_text(text)
        if not s:
            return s

        out: List[str] = []
        sentence: List[str] = []
        count = 0
        i = 0

        while i < len(s) and count < 2:
            ch = s[i]
            sentence.append(ch)

            is_end = False
            if ch in "?!":
                is_end = True
            elif ch == ".":
                prev_c = s[i - 1] if i > 0 else ""
                next_c = s[i + 1] if i + 1 < len(s) else ""
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

        return " ".join(out).strip()

    @staticmethod
    def _violates_role_or_currency(text: str) -> bool:
        t = text.lower()
        forbidden = ["usd", "dólar", "dolar", "dolares", "dólares"]
        if any(w in t for w in forbidden):
            return True
        if re.search(r"\b(vendo|le\s+vendo|te\s+vendo|vendemos)\b", t):
            return True
        return False

    @staticmethod
    def _is_reopening_negotiation(text: str) -> bool:
        t = text.lower()
        patterns = [
            r"\bme\s+lo\s+deja\b", r"\bme\s+la\s+deja\b",
            r"\bme\s+los\s+deja\b", r"\bme\s+las\s+deja\b",
            r"\bdéjemelo\b", r"\bdéjamelo\b",
            r"\brebaj[ae]\b", r"\bdescuento\b",
            r"\bmás\s+barat[oa]\b", r"\bmenos\b.*\b(plata|precio)\b",
            r"\bregáleme\b", r"\bregálame\b",
            r"\bcafecito\b", r"\bpor\s+ese\s+precio\s+no\b",
        ]
        return any(re.search(p, t, re.IGNORECASE) for p in patterns)

    # ------------------------------------------------------------------
    # Greeting deduplication
    # ------------------------------------------------------------------

    def _remove_repeated_greetings(self, text: str) -> str:
        greetings = self._GREETING_RE.findall(text)
        if len(greetings) <= 1:
            return text

        count = 0

        def _replace(match: re.Match) -> str:
            nonlocal count
            count += 1
            if count == 1:
                return match.group(0)
            return self._GREETING_ALTS[count % len(self._GREETING_ALTS)]

        return self._GREETING_RE.sub(_replace, text)

    # ------------------------------------------------------------------
    # State transition detection
    # ------------------------------------------------------------------

    def _compute_next_state(self, user_text: str, assistant_text: str) -> str:
        """Determine the next conversation state based on the current exchange."""
        current = self.state
        assistant_lower = assistant_text.lower()

        # NEGOTIATING -> BUILDING_ORDER
        if current == STATE_NEGOTIATING:
            accept_phrases = [
                "listo", "bueno", "está bien", "ok", "dale", "sí", "si",
                "me llevo", "me los llevo", "me lo llevo", "perfecto", "de una",
            ]
            if any(p in assistant_lower for p in accept_phrases):
                if self._seller_confirms_price(user_text) or self._has_cop_amount(user_text):
                    return STATE_BUILDING_ORDER

                rejection_count = sum(
                    1 for msg in self.history[-6:]
                    if msg["role"] == "user"
                    and any(w in msg["content"].lower() for w in ["no", "precio fijo", "no puedo"])
                )
                if rejection_count >= 2:
                    return STATE_BUILDING_ORDER

        # BUILDING_ORDER -> READY_TO_PAY
        elif current == STATE_BUILDING_ORDER:
            if self._buyer_ready_to_pay(assistant_text):
                return STATE_READY_TO_PAY

            if self._seller_confirms_price(user_text):
                ready = ["listo", "perfecto", "cómo pago", "como pago", "generar"]
                if any(p in assistant_lower for p in ready):
                    return STATE_READY_TO_PAY

            if self._count_products(self.history) >= MAX_PRODUCTS:
                return STATE_READY_TO_PAY

        # READY_TO_PAY -> FINISHED
        elif current == STATE_READY_TO_PAY:
            if self._seller_asks_payment(user_text):
                return STATE_FINISHED

        return current

    # ------------------------------------------------------------------
    # Text detection helpers
    # ------------------------------------------------------------------

    def _has_cop_amount(self, text: str) -> bool:
        return bool(self._COP_AMOUNT_RE.search(text))

    @staticmethod
    def _seller_asks_payment(text: str) -> bool:
        """Detect payment question/invitation from seller."""
        t = text.lower()
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
                if "?" in t or any(
                    k in t for k in ["pagar", "paga", "recibo", "metodo", "método"]
                ):
                    return True

        if re.search(r"\b(c[oó]mo\s+desea\s+pagar|c[oó]mo\s+va\s+a\s+pagar)\b", t):
            return True

        return False

    @staticmethod
    def _seller_confirms_price(text: str) -> bool:
        """Detect price confirmation/closing from seller."""
        t = text.lower()

        if "precio por" in t or "por el kilo" in t or "por kilo" in t:
            return False

        closing = [
            "listo", "de una", "perfecto", "quedamos", "queda en", "le queda en",
            "entonces serían", "serían en total", "en total", "total sería",
            "confirmado", "dale", "hágale", "hagale", "dejémoslo", "dejamos",
            "lo dejo", "se lo dejo",
        ]
        has_close = any(m in t for m in closing)
        has_amount = (
            ConversationEngine._COP_AMOUNT_RE.search(t) is not None
            or ("total" in t and any(ch.isdigit() for ch in t))
        )
        return bool(has_close and has_amount)

    @staticmethod
    def _buyer_ready_to_pay(text: str) -> bool:
        """Detect if buyer is ready to proceed to payment."""
        t = text.lower()
        phrases = [
            "eso es todo", "nada más", "solo eso", "con eso",
            "con eso es todo", "generar el cobro", "cómo pago", "como pago",
            "listo entonces", "ya está", "ya esta", "perfecto", "dele",
        ]
        return any(p in t for p in phrases)

    @staticmethod
    def _seller_asks_what_to_buy(text: str) -> bool:
        """Detect when seller asks what products the buyer wants."""
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
        return any(re.search(p, t, re.IGNORECASE) for p in patterns)

    @staticmethod
    def _count_products(history: List[Dict[str, str]]) -> int:
        return len(ConversationEngine._extract_product_list(history))

    @staticmethod
    def _extract_product_list(history: List[Dict[str, str]]) -> List[str]:
        products: set = set()
        buy_patterns = [
            r"\b(?:me\s+da|deme|déme|quiero|llevo|me\s+llevo|necesito|dame|también)"
            r"\s+(?:\d+\s+)?(?:kilos?\s+de\s+|libras?\s+de\s+|libra\s+de\s+)?(\w+)",
            r"\b(?:tiene|hay)\s+(\w+)",
            r"\b(\w+)\s+(?:a\s+cuánto|a\s+como|cuánto\s+vale|cuánto\s+cuesta)",
        ]
        ignore = {
            "usted", "algo", "más", "mas", "todo", "eso", "nada", "favor",
            "precio", "plata", "pesos", "cobro", "pago", "total", "cuenta",
            "bien", "bueno", "listo", "dale", "gracias", "señor", "vecino",
            "qué", "que", "cómo", "como", "cuánto", "cuanto", "también",
            "el", "la", "los", "las", "un", "una", "unos", "unas",
        }

        for msg in history:
            if msg["role"] == "assistant":
                text = msg["content"].lower()
                for pattern in buy_patterns:
                    for match in re.findall(pattern, text, re.IGNORECASE):
                        word = match.strip().lower()
                        if word and len(word) > 2 and word not in ignore:
                            products.add(word)

        return list(products)

    @staticmethod
    def _payment_response() -> str:
        return "Prefiero pagar por QR, es más cómodo y seguro que cargar efectivo. Muchas gracias, que tenga buen día."

    @staticmethod
    def _ready_to_pay_response() -> str:
        return "Listo, con eso es todo. ¿Me genera el cobro por favor?"


# ---------------------------------------------------------------------------
# Backward-compatible wrapper (used by legacy callers)
# ---------------------------------------------------------------------------

def ollama_generate(
    history: List[Dict[str, str]],
    user_text: str,
    state: str,
    price_tracker: Optional[PriceTracker] = None,
    on_first_sentence: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[str], str]:
    """Legacy wrapper — prefer ``ConversationEngine.process_message``."""
    engine = ConversationEngine()
    engine.history = list(history)
    engine.state = state
    if price_tracker is not None:
        engine.price_tracker = price_tracker
    response = engine._generate(user_text, on_first_sentence)

    # Propagate state changes back
    history.clear()
    history.extend(engine.history)

    return response, engine.state
