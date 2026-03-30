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
OLLAMA_MODEL: str = "gemma3:4b"

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
        "Eres Andrea, una campesina colombiana comprando en la plaza de mercado. "
        "Hablas con naturalidad, tranquila y educada. Usas pesos colombianos.\n\n"
        "PERSONALIDAD:\n"
        "- Preguntas sobre calidad, frescura y origen de los productos\n"
        "- Llevas la cuenta mental de lo que has pedido y los precios acordados\n"
        "- Hablas fluido y natural, sin repetir saludos ni lo ya dicho\n"
        "- Respondes en máximo 3 frases cortas\n\n"
        "NEGOCIACIÓN:\n"
        "- Cuando te dicen un precio nuevo, pides descuento amable (10-20% menos)\n"
        "- Si rechazan, intentas UNA vez más con precio intermedio\n"
        "- Si rechazan dos veces, aceptas sin insistir\n"
        "- Frases: \"¿Me lo deja en X?\", \"¿No me rebaja un poquito?\"\n\n"
        "REGLAS FIRMES:\n"
        "- Siempre eres COMPRADORA, nunca vendedora\n"
        "- Nunca inventes precios que no se hayan mencionado\n"
        "- Escribe precios con números y 'pesos' (ej: 2500 pesos), NUNCA con '$'\n"
        "- Compras entre 3 y 5 productos máximo\n"
        "- Cuando el vendedor pregunte cómo pagas, elige pago por QR y despídete"
    )

    # -- State-specific instructions --

    _STATE_INSTRUCTIONS: Dict[str, str] = {
        STATE_NEGOTIATING: (
            "ESTADO: NEGOCIANDO\n"
            "- NUNCA aceptes el primer precio, pide rebaja amable\n"
            "- Si rechazan 2 veces, acepta: \"Bueno, me lo llevo\"\n"
            "- Puedes preguntar por otros productos\n"
            "Ej: Vendedor dice 10 mil → Tú: \"¿Me lo deja en 8 mil?\""
        ),
        STATE_BUILDING_ORDER: (
            "ESTADO: ARMANDO PEDIDO\n"
            "- Precios anteriores YA están cerrados, NO renegocies\n"
            "- Pregunta por nuevos productos, negocia solo los nuevos\n"
            "- Si te preguntan qué llevas, lista tus productos\n"
            "- Cuando tengas suficientes, di \"con eso es todo, ¿cuánto sería?\""
        ),
        STATE_READY_TO_PAY: (
            "ESTADO: LISTO PARA PAGAR\n"
            "- Ya decidiste comprar, NO pidas más descuentos\n"
            "- Confirma el total y pregunta cómo pagar\n"
            "Ej: \"Listo, ¿me genera el cobro?\""
        ),
        STATE_FINISHED: (
            "ESTADO: TERMINADO\n"
            "- Ya pagaste. Despídete brevemente y no respondas más."
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

        # Terminal state — no more responses
        if self.state == STATE_FINISHED:
            print("[LLM] State FINISHED — no response generated")
            return None

        # Seller asks for payment -> transition state, let LLM respond naturally
        if self._seller_asks_payment(user_text) and self.state == STATE_READY_TO_PAY:
            print("[LLM] Payment request detected — transitioning to FINISHED")
            self.state = STATE_FINISHED

        # Product limit reached -> transition state, let LLM respond naturally
        if self.state == STATE_BUILDING_ORDER:
            count = self._count_products(self.history)
            if count >= MAX_PRODUCTS:
                print(f"[LLM] Product limit reached ({count}/{MAX_PRODUCTS})")
                self.state = STATE_READY_TO_PAY

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
        assistant_text = self._clean_filler_sounds(assistant_text)
        assistant_text = self._strip_dollar_sign(assistant_text)
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

        # Price tracker context — give the LLM real data
        tracker_summary = self.price_tracker.get_summary()
        if self.price_tracker.products:
            parts.append(f"\nTU LISTA DE COMPRAS ACTUAL:\n{tracker_summary}")

        # Product count info
        count = self._count_products(trimmed)
        product_list = self._extract_product_list(trimmed)

        if self.state in (STATE_BUILDING_ORDER, STATE_READY_TO_PAY):
            parts.append(
                f"\nProductos pedidos: {count} de {MAX_PRODUCTS} máximo."
            )
            if product_list:
                parts.append(f"Productos: {', '.join(product_list)}")

            if count >= MAX_PRODUCTS:
                parts.append(
                    "LÍMITE ALCANZADO. Di 'con eso es todo' y pide el cobro."
                )
            elif count >= MAX_PRODUCTS - 1:
                parts.append("Cerca del límite. Máximo UN producto más.")

            if self._seller_asks_what_to_buy(user_text):
                parts.append("El vendedor pregunta qué llevas. Lista tus productos.")

        # Conversation history
        if trimmed:
            parts.append("\n--- Conversación ---")
            for msg in trimmed:
                label = "Vendedor" if msg["role"] == "user" else "Andrea"
                parts.append(f"{label}: {msg['content']}")
            parts.append("---\n")

        parts.append(f"Vendedor: {user_text}")
        parts.append("\nAndrea (máximo 3 frases):")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Filler sound removal
    # ------------------------------------------------------------------

    _FILLER_RE = re.compile(
        r'\b[Mm]+\b'
        r'|\b[Mm],\s*[Mm],\s*[Mm]\b'
        r'|\b[Mm]{2,}\b'
        r'|\b[Hh]mm+\b'
        r'|\b[Ee]h+\b'
        r'|\b[Uu]mm+\b',
    )

    @staticmethod
    def _strip_dollar_sign(text: str) -> str:
        """Remove '$' symbols so TTS does not read them as 'dólar'."""
        if not text:
            return text
        return text.replace("$", "")

    @classmethod
    def _clean_filler_sounds(cls, text: str) -> str:
        """Remove filler sounds like 'mmm', 'm, m, m', 'hmm', etc."""
        if not text:
            return text
        cleaned = cls._FILLER_RE.sub('', text)
        # Collapse multiple spaces / leading commas left behind
        cleaned = re.sub(r'[,\s]+([,])', r'\1', cleaned)
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        cleaned = re.sub(r'^\s*[,.:;]+\s*', '', cleaned)
        return cleaned.strip()

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _apply_guardrails(self, text: str) -> str:
        s = sanitize_text(text)
        if not s:
            return s

        s = self._limit_sentences(s)

        # Hard guardrail: role/currency violation (safety net only)
        if self._violates_role_or_currency(s):
            return "Vecino, yo estoy comprando y pago en pesos. ¿En cuánto me lo deja entonces?"

        # Soft guardrail: in READY_TO_PAY, warn if LLM tries to reopen negotiation
        if self.state == STATE_READY_TO_PAY:
            if self._is_reopening_negotiation(s):
                # Append a redirect instead of replacing the entire response
                return "Listo, con eso es todo. ¿Me genera el cobro por favor?"

        return s

    @staticmethod
    def _limit_sentences(text: str, max_sentences: int = 3) -> str:
        """Limit text to max_sentences. Respects decimal numbers and abbreviations."""
        s = sanitize_text(text)
        if not s:
            return s

        out: List[str] = []
        sentence: List[str] = []
        count = 0
        i = 0

        while i < len(s) and count < max_sentences:
            ch = s[i]
            sentence.append(ch)

            is_end = False
            if ch in "?!":
                is_end = True
            elif ch == ".":
                prev_c = s[i - 1] if i > 0 else ""
                next_c = s[i + 1] if i + 1 < len(s) else ""
                # Don't split on decimal numbers (3.500)
                if prev_c.isdigit() and next_c.isdigit():
                    is_end = False
                # Don't split on ellipsis (...)
                elif next_c == ".":
                    is_end = False
                else:
                    is_end = True

            if is_end:
                seg = "".join(sentence).strip()
                if seg:
                    out.append(seg)
                    count += 1
                sentence = []

            i += 1

        if count < max_sentences:
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
        # Only match clear, unambiguous negotiation attempts
        patterns = [
            r"\bme\s+lo\s+deja\s+en\b", r"\bme\s+la\s+deja\s+en\b",
            r"\bme\s+los\s+deja\s+en\b", r"\bme\s+las\s+deja\s+en\b",
            r"\bdéjemelo\s+en\b", r"\bdéjamelo\s+en\b",
            r"\brebaj[ae]\b", r"\bdescuento\b",
            r"\bmás\s+barat[oa]\b",
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

        # Already FINISHED (set by _generate on payment detection)
        if current == STATE_FINISHED:
            return STATE_FINISHED

        # NEGOTIATING -> BUILDING_ORDER
        if current == STATE_NEGOTIATING:
            # Only transition if the assistant explicitly accepts a price
            accept_phrases = [
                "me llevo", "me los llevo", "me lo llevo",
                "bueno, está bien", "de una", "listo, me",
            ]
            soft_accept = ["listo", "bueno", "está bien", "dale", "perfecto"]

            # Strong accept: explicit acceptance phrase
            if any(p in assistant_lower for p in accept_phrases):
                if self._seller_confirms_price(user_text) or self._has_cop_amount(user_text):
                    return STATE_BUILDING_ORDER

            # Soft accept: only with confirmed price + seller rejection history
            if any(p in assistant_lower for p in soft_accept):
                if self._seller_confirms_price(user_text):
                    return STATE_BUILDING_ORDER

                rejection_count = sum(
                    1 for msg in self.history[-6:]
                    if msg["role"] == "user"
                    and any(w in msg["content"].lower() for w in [
                        "no puedo", "precio fijo", "no le puedo", "imposible",
                    ])
                )
                if rejection_count >= 2 and self._has_cop_amount(user_text):
                    return STATE_BUILDING_ORDER

        # BUILDING_ORDER -> READY_TO_PAY
        elif current == STATE_BUILDING_ORDER:
            if self._buyer_ready_to_pay(assistant_text):
                return STATE_READY_TO_PAY

            if self._count_products(self.history) >= MAX_PRODUCTS:
                return STATE_READY_TO_PAY

        # READY_TO_PAY -> FINISHED
        elif current == STATE_READY_TO_PAY:
            if self._seller_asks_payment(user_text):
                return STATE_FINISHED
            # Also detect if Andrea herself chose payment method
            if self._buyer_chose_payment(assistant_text):
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
    def _buyer_chose_payment(text: str) -> bool:
        """Detect if buyer chose a payment method in their response."""
        t = text.lower()
        payment_methods = ["pago por qr", "por qr", "con qr", "transferencia", "efectivo"]
        farewell = ["buen día", "buen dia", "gracias", "hasta luego", "chao"]
        has_payment = any(p in t for p in payment_methods)
        has_farewell = any(f in t for f in farewell)
        return has_payment and has_farewell


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
