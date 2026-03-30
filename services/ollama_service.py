"""Conversation engine for the AI voice marketplace negotiation assistant.

Minimal-state design: the LLM drives the conversation naturally.
The engine only tracks two hard checkpoints:
  1. Payment detection (VR protocol: "pago por qr" triggers FINISHED)
  2. Role/currency violation (safety net)

Everything else — negotiation, product selection, flow — is the LLM's job.
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

ACTIVE_PROFILE_NAME: str = "gpu_hybrid"

# ---------------------------------------------------------------------------
# Conversation states (minimal: only ACTIVE and FINISHED matter)
# ---------------------------------------------------------------------------

STATE_NEGOTIATING: str = "NEGOTIATING"
STATE_BUILDING_ORDER: str = "BUILDING_ORDER"
STATE_READY_TO_PAY: str = "READY_TO_PAY"
STATE_FINISHED: str = "FINISHED"

MAX_HISTORY: int = 20

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
# Price / product detection
# ---------------------------------------------------------------------------

def extract_price_and_product(text: str) -> Optional[Tuple[str, int]]:
    """Extract product name and price from a seller message.

    Example: ``"Aguacate a 3500 pesos"`` -> ``("aguacate", 3500)``
    """
    t = text.lower()

    # First check "X mil" pattern (e.g. "8 mil pesos" -> 8000)
    mil_matches = re.findall(r"(\d+)\s*mil(?:\s*(?:pesos|cop))?", t)
    if mil_matches:
        price_str = str(int(mil_matches[0]) * 1000)
    else:
        # Then check formatted numbers (10.300) or plain numbers with pesos/cop
        price_pattern = (
            r"(?:(?:\$)\s*)?"
            r"((?:\d{1,3}(?:[.,]\d{3})+)|(?:\d+))"
            r"(?:\s*(?:pesos|cop))"
        )
        prices = re.findall(price_pattern, t)

        if not prices:
            prices = re.findall(r"\$\s*(\d[\d.,]*)", t)

        if not prices:
            return None

        price_str = prices[0].replace(".", "").replace(",", "")

    try:
        price = int(price_str)
    except ValueError:
        return None

    if price < 100:
        return None

    matches = re.findall(r"(\w+)\s+(?:a\s+)?(?:en\s+)?(?:\$\s*)?(?:\d+)", t)
    if matches:
        product = matches[0].lower()
        ignore = {"el", "la", "los", "las", "un", "una", "unos", "unas",
                  "tengo", "tiene", "dejo", "de", "lo", "se", "le", "en"}
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
            return ""
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
    """Orchestrates conversation with the LLM using minimal intervention.

    Philosophy: the LLM is the brain, the engine is just plumbing.
    Only two hard interventions exist:
      - Payment detection -> fixed QR response (VR protocol)
      - Role/currency violation -> correction
    """

    # -- Compiled regex --

    _GREETING_RE = re.compile(
        r"\b(buenos\s+días|buenas\s+tardes|buenas\s+noches|hola)\b",
        re.IGNORECASE,
    )
    _GREETING_ALTS: List[str] = [
        "Claro", "Perfecto", "Entiendo", "Muy bien", "De acuerdo",
        "Está bien", "Excelente", "Dale", "Listo",
    ]
    _FILLER_RE = re.compile(
        r'\b[Mm]+\b'
        r'|\b[Mm],\s*[Mm],\s*[Mm]\b'
        r'|\b[Mm]{2,}\b'
        r'|\b[Hh]mm+\b'
        r'|\b[Ee]h+\b'
        r'|\b[Uu]mm+\b',
    )

    # -- System prompt: persona + minimal rules, NO per-turn micromanagement --

    _SYSTEM_PROMPT: str = (
        "Eres Andrea, una campesina colombiana que va a comprar frutas y verduras "
        "en la plaza de mercado. Hablas con naturalidad, eres tranquila, amable y educada. "
        "Usas pesos colombianos.\n\n"
        "CÓMO ERES:\n"
        "- Saludas al vendedor y le preguntas qué tiene o por algún producto\n"
        "- Preguntas sobre calidad, frescura y origen\n"
        "- Cuando te dicen un precio, intentas regatear un poquito de forma amable\n"
        "- Compras varios productos (3 a 5), no solo uno\n"
        "- Cuando ya tienes suficientes productos, pides la cuenta\n"
        "- Respondes natural, como en una conversación real de plaza de mercado\n\n"
        "IMPORTANTE:\n"
        "- Siempre eres la COMPRADORA\n"
        "- Escucha lo que dice el vendedor y responde a lo que dice, no repitas lo mismo\n"
        "- Si el vendedor dice que no tiene algo, pregunta por otra cosa diferente\n"
        "- No menciones pago ni te despidas hasta que ya hayas comprado varios productos "
        "y el vendedor te pregunte cómo quieres pagar\n"
        "- Escribe precios con números y 'pesos', nunca con '$'\n"
        "- Máximo 3 frases por respuesta"
    )

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

        # Seller asks for payment method -> fixed QR response (VR protocol)
        if self._seller_asks_payment(user_text):
            print("[LLM] Payment request detected — finishing conversation")
            self.state = STATE_FINISHED
            return self._payment_response()

        # Build prompt and stream from Ollama
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

        # Light post-processing (cleanup only, no content manipulation)
        assistant_text = self._clean_filler_sounds(assistant_text)
        assistant_text = assistant_text.replace("$", "")
        assistant_text = self._remove_repeated_greetings(assistant_text)
        assistant_text = self._limit_sentences(assistant_text, 3)

        # Only hard guardrail: role/currency violation
        if self._violates_role_or_currency(assistant_text):
            assistant_text = "Vecino, yo estoy comprando y pago en pesos. ¿Qué más tiene por ahí?"

        # Detect if the LLM decided to finish (mentions QR payment)
        if "pago por qr" in assistant_text.lower() or "por qr" in assistant_text.lower():
            print("[LLM] LLM chose QR payment — transitioning to FINISHED")
            self.state = STATE_FINISHED

        return assistant_text

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, user_text: str) -> str:
        trimmed = trim_history(self.history)
        parts: List[str] = []

        # System prompt — persona only
        parts.append(self._SYSTEM_PROMPT)

        # Price tracker context — give the LLM awareness of what's been bought
        tracker_summary = self.price_tracker.get_summary()
        if tracker_summary:
            parts.append(f"\nProductos que llevas hasta ahora:\n{tracker_summary}")

        # Turn count context — gentle nudge, not a command
        turn_count = len(trimmed) // 2
        if turn_count >= 8:
            parts.append(
                "\nYa llevas un buen rato comprando. "
                "Cuando sientas que ya tienes suficiente, puedes pedir la cuenta."
            )

        # Conversation history
        if trimmed:
            parts.append("\n--- Conversación ---")
            for msg in trimmed:
                label = "Vendedor" if msg["role"] == "user" else "Andrea"
                parts.append(f"{label}: {msg['content']}")
            parts.append("---\n")

        parts.append(f"Vendedor: {user_text}")
        parts.append("\nAndrea:")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Text cleanup (non-destructive)
    # ------------------------------------------------------------------

    @classmethod
    def _clean_filler_sounds(cls, text: str) -> str:
        if not text:
            return text
        cleaned = cls._FILLER_RE.sub('', text)
        cleaned = re.sub(r'[,\s]+([,])', r'\1', cleaned)
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)
        cleaned = re.sub(r'^\s*[,.:;]+\s*', '', cleaned)
        return cleaned.strip()

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

    @staticmethod
    def _limit_sentences(text: str, max_sentences: int = 3) -> str:
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
                if prev_c.isdigit() and next_c.isdigit():
                    is_end = False
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

    # ------------------------------------------------------------------
    # Hard guardrails (safety net only)
    # ------------------------------------------------------------------

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
    def _seller_asks_payment(text: str) -> bool:
        """Detect payment question/invitation from seller."""
        t = text.lower()
        patterns = [
            r"\b(c[oó]mo|con qu[eé]|de qu[eé] manera)\b.*\b(paga|pagar|desea pagar|va a pagar)\b",
            r"\b(m[eé]todo|forma)\s+de\s+pago\b",
            r"\b(efectivo)\s+o\s+(mediante\s+)?\b(qr|transferencia|tarjeta)\b",
            r"\b(desea|quiere|va\s+a)\s+pagar\b",
        ]
        for p in patterns:
            if re.search(p, t, re.IGNORECASE):
                return True

        if re.search(r"\b(c[oó]mo\s+desea\s+pagar|c[oó]mo\s+va\s+a\s+pagar)\b", t):
            return True

        return False

    @staticmethod
    def _payment_response() -> str:
        """Fixed QR payment response — VR parses 'pago por qr' to close conversation."""
        return "Prefiero pago por QR, es más cómodo y seguro. Muchas gracias, que tenga buen día."

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    @staticmethod
    def _buyer_chose_payment(text: str) -> bool:
        """Detect if buyer chose a payment method in their response."""
        t = text.lower()
        return "pago por qr" in t or "por qr" in t


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

    history.clear()
    history.extend(engine.history)

    return response, engine.state
