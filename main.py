"""
Sistema de Asistente de Voz con IA - 100% Offline
================================================
Autor: Nicolás
Descripción: Asistente de voz que funciona completamente offline
            usando Vosk (STT), Mistral (LLM) y pyttsx3 (TTS)

Componentes:
- STT: Vosk (modelo español offline)
- LLM: Ollama/Mistral (inferencia local)
- TTS: pyttsx3 (voces del sistema Windows)

Uso:
    python main.py
"""

import sys
import os
import argparse

# ====================================================================
# CONFIGURACIÓN CRÍTICA OFFLINE - EVITA CONEXIONES A INTERNET
# ====================================================================
def configure_offline_environment():
    """Configura el entorno para funcionar 100% offline"""
    offline_vars = {
        'HF_HUB_OFFLINE': '1',           # Hugging Face Hub offline
        'TRANSFORMERS_OFFLINE': '1',     # Transformers offline  
        'HF_DATASETS_OFFLINE': '1',      # Datasets offline
        'TORCH_HOME': os.path.expanduser('~/.cache/torch'),  # Cache local de PyTorch
        'HUGGINGFACE_HUB_CACHE': os.path.expanduser('~/.cache/huggingface'),  # Cache HF
        'HF_DATASETS_CACHE': os.path.expanduser('~/.cache/huggingface/datasets'),  # Cache datasets
        'CURL_CA_BUNDLE': '',            # Evita verificaciones SSL
        'REQUESTS_CA_BUNDLE': '',        # Evita verificaciones SSL en requests
    }
    
    for var, value in offline_vars.items():
        os.environ[var] = value
    
    print("Entorno configurado para funcionar 100% OFFLINE")
# ====================================================================

def main():
    """Punto de entrada principal del sistema"""
    
    # CRÍTICO: Configurar entorno offline ANTES que cualquier otra cosa
    configure_offline_environment()
    
    # Parsear argumentos de línea de comandos
    parser = argparse.ArgumentParser(description='Sistema de Asistente de Voz con IA')
    parser.add_argument('--api-server', action='store_true', 
                       help='Iniciar en modo servidor API para comunicación con VR')
    parser.add_argument('--port', type=int, default=8000,
                       help='Puerto para el servidor API (default: 8000)')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                       help='Host para el servidor API (default: 127.0.0.1)')
    
    args = parser.parse_args()
    
    if args.api_server:
        # Modo servidor API para VR
        print("=" * 70)
        print("🌐 SERVIDOR API PARA APLICACIÓN VR - MODO BACKGROUND")
        print("=" * 70)
        print(f"🔗 Escuchando en: http://{args.host}:{args.port}")
        print("📱 Listo para recibir requests desde aplicación VR")
        print("💡 Presiona Ctrl+C para detener")
        print("=" * 70)
        
        try:
            # Importar y ejecutar servidor API
            from api_server import start_api_server
            start_api_server(host=args.host, port=args.port)
        except ImportError:
            print("❌ Error: api_server.py no encontrado")
            print("💡 Verifica que el archivo api_server.py esté en el directorio")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n\n🛑 Servidor API detenido por el usuario")
            sys.exit(0)
        except Exception as e:
            print(f"❌ Error iniciando servidor API: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # Modo standalone normal
        print("=" * 70)
        print("🎙️  SISTEMA DE ASISTENTE DE VOZ CON IA - 100% OFFLINE")
        print("=" * 70)
        print()
        
        # Importar y ejecutar el grabador de voz
        try:
            from voice_recorder import main as voice_main
            voice_main()
        except KeyboardInterrupt:
            print("\n\n Sistema detenido por el usuario")
            sys.exit(0)
        except Exception as e:
            print(f"\n Error crítico: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == "__main__":
    main()
