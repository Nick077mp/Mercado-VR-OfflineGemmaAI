"""Generate PDF documentation for the VR AI Voice Server system.

Creates two PDF files:
  1. Guia_Inicio_Servidor.pdf  -- How to start and stop the server
  2. Guia_Flujo_Modelo.pdf     -- How the AI model flow works
"""

from fpdf import FPDF

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


# ---------------------------------------------------------------------------
# Shared PDF helpers
# ---------------------------------------------------------------------------

class MercadoPDF(FPDF):
    """Custom PDF with consistent styling for Mercado VR documentation."""

    # Brand colors
    PRIMARY = (44, 62, 80)
    ACCENT = (39, 174, 96)
    LIGHT_BG = (236, 240, 241)
    WHITE = (255, 255, 255)
    TEXT_DARK = (33, 33, 33)

    def __init__(self):
        super().__init__()
        # Register Unicode fonts
        self.add_font("DejaVu", "", f"{FONT_DIR}/DejaVuSans.ttf")
        self.add_font("DejaVu", "B", f"{FONT_DIR}/DejaVuSans-Bold.ttf")

    def header(self):
        self.set_font("DejaVu", "B", 10)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 8, "Mercado VR - Sistema de IA de Voz", align="R",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.ACCENT)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"P\u00e1gina {self.page_no()}/{{nb}}", align="C")

    def title_page(self, title: str, subtitle: str):
        self.add_page()
        self.ln(50)
        self.set_font("DejaVu", "B", 28)
        self.set_text_color(*self.PRIMARY)
        self.multi_cell(0, 14, title, align="C")
        self.ln(8)
        self.set_font("DejaVu", "", 14)
        self.set_text_color(100, 100, 100)
        self.multi_cell(0, 8, subtitle, align="C")
        self.ln(20)
        self.set_draw_color(*self.ACCENT)
        self.set_line_width(1)
        self.line(60, self.get_y(), 150, self.get_y())

    def section_title(self, text: str):
        self.ln(6)
        self.set_font("DejaVu", "B", 16)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.ACCENT)
        self.set_line_width(0.4)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(4)

    def subsection_title(self, text: str):
        self.ln(3)
        self.set_font("DejaVu", "B", 13)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str):
        self.set_font("DejaVu", "", 11)
        self.set_text_color(*self.TEXT_DARK)
        self.multi_cell(0, 6, text)
        self.ln(2)

    def bullet_list(self, items: list):
        self.set_font("DejaVu", "", 11)
        self.set_text_color(*self.TEXT_DARK)
        for item in items:
            self.cell(8)
            self.cell(5, 6, "\u2022 ")
            self.multi_cell(0, 6, item)
            self.ln(1)
        self.ln(2)

    def numbered_list(self, items: list):
        self.set_font("DejaVu", "", 11)
        self.set_text_color(*self.TEXT_DARK)
        for i, item in enumerate(items, 1):
            self.cell(8)
            self.set_font("DejaVu", "B", 11)
            self.cell(8, 6, f"{i}.")
            self.set_font("DejaVu", "", 11)
            self.multi_cell(0, 6, f" {item}")
            self.ln(1)
        self.ln(2)

    def info_box(self, text: str, color=None):
        if color is None:
            color = self.ACCENT
        self.set_fill_color(*color)
        self.set_draw_color(*color)
        x = self.get_x()
        y = self.get_y()
        self.rect(x, y, 3, 24, "F")
        self.set_xy(x + 6, y + 2)
        self.set_font("DejaVu", "B", 10)
        self.set_text_color(*color)
        self.multi_cell(175, 6, text)
        self.set_y(y + 28)
        self.ln(2)

    def warning_box(self, text: str):
        self.info_box(text, color=(231, 76, 60))

    def step_box(self, step_num: str, title: str, description: str):
        y = self.get_y()
        # Green circle with number
        self.set_fill_color(*self.ACCENT)
        self.set_text_color(*self.WHITE)
        self.set_font("DejaVu", "B", 14)
        self.ellipse(12, y, 14, 14, "F")
        self.set_xy(12, y + 1)
        self.cell(14, 12, step_num, align="C")
        # Title
        self.set_xy(30, y)
        self.set_font("DejaVu", "B", 12)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        # Description
        self.set_x(30)
        self.set_font("DejaVu", "", 10)
        self.set_text_color(*self.TEXT_DARK)
        self.multi_cell(165, 5.5, description)
        self.ln(4)


# ---------------------------------------------------------------------------
# Document 1: Server Start/Stop Guide
# ---------------------------------------------------------------------------

def create_server_guide():
    pdf = MercadoPDF()
    pdf.alias_nb_pages()

    # Title page
    pdf.title_page(
        "Gu\u00eda de Inicio y Apagado\ndel Servidor",
        "Sistema de IA de Voz para Realidad Virtual\nMercado VR"
    )

    # --- Qu\u00e9 es este sistema ---
    pdf.add_page()
    pdf.section_title("\u00bfQu\u00e9 es este sistema?")
    pdf.body_text(
        "Este sistema permite que un personaje virtual (Andrea) converse con el "
        "usuario en una experiencia de Realidad Virtual ambientada en una plaza de "
        "mercado colombiana. El sistema funciona 100% sin conexi\u00f3n a internet, "
        "usando inteligencia artificial local."
    )
    pdf.body_text(
        "Para que la experiencia funcione, es necesario iniciar dos servidores "
        "antes de abrir la aplicaci\u00f3n de VR:"
    )
    pdf.bullet_list([
        "Servidor de IA (Ollama): Procesa el lenguaje natural y genera las respuestas de Andrea.",
        "Servidor de Voz (Python): Recibe el audio del micr\u00f3fono, lo transcribe y coordina todo.",
    ])
    pdf.info_box("IMPORTANTE: Ambos servidores deben estar activos ANTES de iniciar la aplicaci\u00f3n de VR.")

    # --- Requisitos previos ---
    pdf.section_title("Requisitos previos")
    pdf.body_text(
        "Antes de usar el sistema por primera vez, aseg\u00farese de que su computador "
        "tiene instalado lo siguiente (esto ya deber\u00eda estar configurado):"
    )
    pdf.bullet_list([
        "Ollama (motor de inteligencia artificial) - ollama.com",
        "Python 3.10 o superior con entorno virtual configurado",
        "Windows 10 o superior",
        "Tarjeta gr\u00e1fica NVIDIA con soporte CUDA (recomendado)",
    ])
    pdf.info_box("Si el sistema ya fue configurado por el equipo t\u00e9cnico, no necesita instalar nada adicional.")

    # --- C\u00f3mo iniciar ---
    pdf.add_page()
    pdf.section_title("C\u00f3mo iniciar el servidor")
    pdf.body_text(
        "Iniciar el sistema es muy sencillo. Solo necesita hacer doble clic en un "
        "archivo y esperar a que el sistema confirme que est\u00e1 listo."
    )

    pdf.subsection_title("Paso a paso:")
    pdf.ln(2)

    pdf.step_box("1", "Ubique el archivo INICIAR_SISTEMA.bat",
        "En la carpeta principal del proyecto, busque el archivo llamado "
        "INICIAR_SISTEMA.bat (tiene icono de engranaje o ventana de comandos).")

    pdf.step_box("2", "Haga doble clic en INICIAR_SISTEMA.bat",
        "Se abrir\u00e1 una ventana negra (terminal) que mostrar\u00e1 el progreso del inicio. "
        "El sistema verificar\u00e1 autom\u00e1ticamente que todo est\u00e9 instalado.")

    pdf.step_box("3", "Espere la verificaci\u00f3n de requisitos",
        "El sistema revisar\u00e1 que Ollama, Python y el entorno virtual est\u00e9n "
        "correctamente instalados. Ver\u00e1 mensajes como:\n"
        "  [OK] Ollama instalado\n"
        "  [OK] Python instalado\n"
        "  [OK] Entorno virtual encontrado")

    pdf.step_box("4", "El servidor de IA (Ollama) se inicia autom\u00e1ticamente",
        "Ver\u00e1 el mensaje: [PASO 1/2] Iniciando servidor de IA (Ollama)...\n"
        "Espere hasta que aparezca: [OK] Ollama listo\n"
        "Esto puede tardar entre 10 y 30 segundos.")

    pdf.step_box("5", "El servidor de voz (Python) se inicia autom\u00e1ticamente",
        "Ver\u00e1 el mensaje: [PASO 2/2] Iniciando servidor de voz (Python)...\n"
        "Espere hasta que aparezca: [OK] Servidor de voz listo")

    # --- Confirmaci\u00f3n ---
    pdf.add_page()
    pdf.step_box("6", "Confirmaci\u00f3n: SISTEMA LISTO",
        "Cuando todo est\u00e9 funcionando, ver\u00e1 un mensaje grande que dice:\n\n"
        "  SISTEMA LISTO - TODO FUNCIONANDO\n"
        "  Servidor IA   : ACTIVO\n"
        "  Servidor Voz  : ACTIVO\n\n"
        "Ya puede abrir la aplicaci\u00f3n de VR.")

    pdf.ln(4)
    pdf.warning_box("NO cierre las ventanas minimizadas que se abren. Si las cierra, el sistema dejar\u00e1 de funcionar.")
    pdf.ln(2)
    pdf.info_box("Puede cerrar la ventana principal (la que muestra 'SISTEMA LISTO'). Los servidores seguir\u00e1n funcionando en segundo plano.")

    # --- C\u00f3mo apagar ---
    pdf.add_page()
    pdf.section_title("C\u00f3mo apagar el servidor")
    pdf.body_text(
        "Cuando termine de usar la experiencia de VR, es importante apagar "
        "correctamente el sistema para liberar recursos del computador."
    )

    pdf.subsection_title("Paso a paso:")
    pdf.ln(2)

    pdf.step_box("1", "Cierre la aplicaci\u00f3n de VR",
        "Primero cierre la aplicaci\u00f3n de Realidad Virtual normalmente.")

    pdf.step_box("2", "Ubique el archivo APAGAR_SISTEMA.bat",
        "En la misma carpeta donde est\u00e1 INICIAR_SISTEMA.bat, busque "
        "el archivo APAGAR_SISTEMA.bat.")

    pdf.step_box("3", "Haga doble clic en APAGAR_SISTEMA.bat",
        "Se abrir\u00e1 una ventana que detendr\u00e1 todos los servidores autom\u00e1ticamente.\n"
        "Ver\u00e1 mensajes como:\n"
        "  [OK] Servidor de voz detenido\n"
        "  [OK] Servidor de IA detenido\n\n"
        "Al final ver\u00e1: SISTEMA APAGADO CORRECTAMENTE")

    pdf.step_box("4", "Cierre la ventana",
        "Presione cualquier tecla para cerrar la ventana. El sistema queda completamente apagado.")

    # --- Soluci\u00f3n de problemas ---
    pdf.add_page()
    pdf.section_title("Soluci\u00f3n de problemas")

    pdf.subsection_title("El sistema muestra [ERROR] al iniciar")
    pdf.body_text(
        "Si ve un mensaje de error rojo al ejecutar INICIAR_SISTEMA.bat, "
        "puede deberse a:"
    )
    pdf.bullet_list([
        "Ollama no est\u00e1 instalado: Contacte al equipo t\u00e9cnico para que lo instale.",
        "Python no est\u00e1 instalado: Contacte al equipo t\u00e9cnico.",
        "Entorno virtual no encontrado: Contacte al equipo t\u00e9cnico.",
        "Ollama no responde despu\u00e9s de 40 segundos: Reinicie el computador e intente de nuevo.",
    ])

    pdf.subsection_title("La aplicaci\u00f3n VR no conecta con el servidor")
    pdf.bullet_list([
        "Verifique que ejecut\u00f3 INICIAR_SISTEMA.bat ANTES de abrir la app VR.",
        "Verifique que el mensaje 'SISTEMA LISTO' apareci\u00f3 antes de abrir VR.",
        "Si el problema persiste, cierre todo, ejecute APAGAR_SISTEMA.bat, y vuelva a iniciar.",
    ])

    pdf.subsection_title("El avatar no responde o tarda mucho")
    pdf.bullet_list([
        "Es normal que la primera respuesta tarde unos segundos (el modelo se carga en memoria).",
        "Las respuestas siguientes son m\u00e1s r\u00e1pidas.",
        "Si pasan m\u00e1s de 30 segundos sin respuesta, apague y reinicie el sistema.",
    ])

    pdf.subsection_title("Cerr\u00f3 accidentalmente una ventana minimizada")
    pdf.body_text(
        "Si cerr\u00f3 una de las ventanas minimizadas (Ollama o Python), el sistema "
        "dejar\u00e1 de funcionar. La soluci\u00f3n es:"
    )
    pdf.numbered_list([
        "Ejecute APAGAR_SISTEMA.bat para asegurarse de que todo est\u00e9 cerrado.",
        "Ejecute INICIAR_SISTEMA.bat para reiniciar todo desde cero.",
    ])

    # Save
    pdf.output("docs/Guia_Inicio_Servidor.pdf")
    print("PDF 1 created: docs/Guia_Inicio_Servidor.pdf")


# ---------------------------------------------------------------------------
# Document 2: Model Flow Guide
# ---------------------------------------------------------------------------

def create_model_flow_guide():
    pdf = MercadoPDF()
    pdf.alias_nb_pages()

    # Title page
    pdf.title_page(
        "Gu\u00eda del Flujo del Modelo\nde Inteligencia Artificial",
        "C\u00f3mo funciona la experiencia conversacional\nMercado VR"
    )

    # --- Qu\u00e9 es el modelo ---
    pdf.add_page()
    pdf.section_title("\u00bfQu\u00e9 es el modelo de IA?")
    pdf.body_text(
        "El sistema utiliza un modelo de inteligencia artificial llamado Gemma 3 4B, "
        "desarrollado por Google, que se ejecuta localmente en su computador a trav\u00e9s "
        "de Ollama. Esto significa que:"
    )
    pdf.bullet_list([
        "Funciona 100% sin internet (offline).",
        "Toda la informaci\u00f3n se procesa localmente, sin enviar datos a la nube.",
        "La privacidad del usuario est\u00e1 completamente protegida.",
        "No requiere suscripciones ni pagos recurrentes.",
    ])
    pdf.info_box("El modelo es completamente offline. No necesita conexi\u00f3n a internet para funcionar.")

    # --- Qui\u00e9n es Andrea ---
    pdf.section_title("\u00bfQui\u00e9n es Andrea?")
    pdf.body_text(
        "Andrea es el personaje virtual con el que interact\u00faa el usuario. "
        "Es una campesina colombiana que va a comprar frutas y verduras "
        "en la plaza de mercado. Sus caracter\u00edsticas son:"
    )
    pdf.bullet_list([
        "Habla de forma natural, tranquila, amable y educada.",
        "Usa pesos colombianos para todas las transacciones.",
        "Saluda al vendedor, pregunta por productos, negocia precios y compra.",
        "Intenta regatear de forma amable cuando le dan un precio.",
        "Compra varios productos (3 a 5) antes de terminar la conversaci\u00f3n.",
    ])

    # --- Flujo de la conversaci\u00f3n ---
    pdf.add_page()
    pdf.section_title("\u00bfC\u00f3mo fluye la conversaci\u00f3n?")
    pdf.body_text(
        "La conversaci\u00f3n sigue un flujo natural de compra en una plaza de mercado. "
        "El usuario toma el rol de VENDEDOR y Andrea es la COMPRADORA."
    )

    pdf.subsection_title("Flujo t\u00edpico de una conversaci\u00f3n:")
    pdf.ln(2)

    pdf.step_box("1", "Saludo",
        "Andrea saluda al vendedor y pregunta qu\u00e9 productos tiene disponibles o "
        "pregunta directamente por alg\u00fan producto espec\u00edfico.")

    pdf.step_box("2", "Consulta de precios",
        "Andrea pregunta el precio de los productos que le interesan. "
        "Preguntar el precio NO significa que est\u00e9 comprando.")

    pdf.step_box("3", "Negociaci\u00f3n",
        "Andrea intenta regatear un poco de forma amable. Por ejemplo:\n"
        "\u00abAy, eso est\u00e1 un poquito caro, \u00bfno me lo deja en 3000?\u00bb")

    pdf.step_box("4", "Confirmaci\u00f3n de compra",
        "Cuando Andrea decide comprar, lo dice expl\u00edcitamente:\n"
        "\u00abDeme dos kilos\u00bb o \u00abMe llevo una libra\u00bb\n"
        "Solo en este momento se registra como compra.")

    pdf.step_box("5", "M\u00e1s productos",
        "Andrea repite los pasos 2-4 con otros productos. Normalmente "
        "compra entre 3 y 5 productos diferentes.")

    pdf.add_page()
    pdf.step_box("6", "Pago y despedida",
        "Cuando el vendedor pregunta c\u00f3mo desea pagar, Andrea responde que "
        "prefiere pago por QR. Esto cierra la conversaci\u00f3n autom\u00e1ticamente.\n\n"
        "IMPORTANTE: Andrea NO menciona el pago por s\u00ed sola. Espera a que "
        "el vendedor le pregunte c\u00f3mo quiere pagar.")

    # --- C\u00f3mo debe actuar el usuario ---
    pdf.ln(4)
    pdf.section_title("\u00bfC\u00f3mo debe actuar el usuario (vendedor)?")
    pdf.body_text(
        "El usuario toma el rol de vendedor en la plaza de mercado. "
        "Para obtener la mejor experiencia, tenga en cuenta:"
    )

    pdf.subsection_title("Recomendaciones:")
    pdf.bullet_list([
        "Ofrezca productos cuando Andrea pregunte qu\u00e9 tiene disponible.",
        "Mencione precios claros: \u00abEl aguacate est\u00e1 a 3500 pesos el kilo\u00bb.",
        "Responda a las preguntas de Andrea sobre calidad, frescura y origen.",
        "Si Andrea intenta regatear, puede aceptar, rechazar o proponer un precio intermedio.",
        "Cuando Andrea ya haya comprado varios productos, preg\u00fantele c\u00f3mo desea pagar.",
        "No se apresure: la conversaci\u00f3n debe sentirse natural y relajada.",
    ])

    pdf.warning_box("CLAVE: Cuando quiera terminar la conversaci\u00f3n, preg\u00fantele a Andrea '\u00bfC\u00f3mo desea pagar?' Esto activar\u00e1 el cierre autom\u00e1tico.")

    # --- Flujo t\u00e9cnico ---
    pdf.add_page()
    pdf.section_title("Flujo t\u00e9cnico simplificado")
    pdf.body_text(
        "A continuaci\u00f3n se describe c\u00f3mo funciona el sistema internamente, "
        "paso a paso, cada vez que el usuario habla:"
    )

    pdf.ln(2)
    pdf.step_box("1", "Captura de audio",
        "El usuario presiona el bot\u00f3n en el control de VR para hablar. "
        "Al soltar el bot\u00f3n, el audio se env\u00eda al servidor.")

    pdf.step_box("2", "Transcripci\u00f3n (STT)",
        "El audio se convierte a texto usando Faster-Whisper, un modelo "
        "de reconocimiento de voz offline. Soporta espa\u00f1ol.")

    pdf.step_box("3", "Procesamiento por la IA (LLM)",
        "El texto transcrito se env\u00eda al modelo Gemma 3 4B a trav\u00e9s de Ollama. "
        "El modelo genera la respuesta de Andrea considerando toda la "
        "conversaci\u00f3n previa.")

    pdf.step_box("4", "Respuesta al VR",
        "La respuesta de Andrea se env\u00eda de vuelta a la aplicaci\u00f3n de VR, "
        "donde el avatar la reproduce con voz sintetizada y lip sync.")

    pdf.ln(4)
    pdf.body_text(
        "Todo este proceso toma entre 3 y 8 segundos dependiendo de la "
        "longitud de lo que dijo el usuario y la capacidad del computador."
    )

    # --- Consejos ---
    pdf.add_page()
    pdf.section_title("Consejos para la mejor experiencia")

    pdf.subsection_title("Para el usuario (vendedor):")
    pdf.bullet_list([
        "Hable claro y a un volumen normal.",
        "Mantenga el bot\u00f3n presionado mientras habla y su\u00e9ltelo al terminar.",
        "Espere a que Andrea responda antes de volver a hablar.",
        "Use frases cortas y naturales, como hablar\u00eda en una plaza real.",
        "Mencione precios con n\u00fameros claros: \u00abtres mil quinientos pesos\u00bb.",
    ])

    pdf.subsection_title("Sobre la conversaci\u00f3n:")
    pdf.bullet_list([
        "No hay respuestas 'correctas' o 'incorrectas'. La conversaci\u00f3n es libre.",
        "Andrea se adapta a lo que usted diga. Si le dice que no tiene un producto, preguntar\u00e1 por otro.",
        "Si Andrea no entiende algo, le pedir\u00e1 que repita de forma natural.",
        "La primera respuesta puede tardar un poco m\u00e1s (el modelo se est\u00e1 cargando).",
        "Las respuestas siguientes son m\u00e1s r\u00e1pidas.",
    ])

    pdf.subsection_title("Para terminar la conversaci\u00f3n:")
    pdf.body_text(
        "La forma correcta de cerrar la experiencia es preguntarle a Andrea "
        "c\u00f3mo desea pagar. Ella responder\u00e1 que prefiere pago por QR y se "
        "despedir\u00e1. La aplicaci\u00f3n VR detecta esta respuesta y cierra la "
        "conversaci\u00f3n autom\u00e1ticamente."
    )
    pdf.info_box("Recuerde: la experiencia est\u00e1 dise\u00f1ada para ser natural y relajada. No hay prisa, disfrute la interacci\u00f3n.")

    # Save
    pdf.output("docs/Guia_Flujo_Modelo.pdf")
    print("PDF 2 created: docs/Guia_Flujo_Modelo.pdf")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_server_guide()
    create_model_flow_guide()
    print("\nBoth PDFs generated successfully in docs/ folder.")
