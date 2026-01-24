"""
Script para probar API Server
=============================
Simula envíos al endpoint /chat
"""

import requests
import json

API_URL = "http://127.0.0.1:8000"

def test_chat(text):
    """Enviar mensaje al API server"""
    try:
        print(f"👤 Vendedor: {text}")
        
        response = requests.post(
            f"{API_URL}/chat",
            json={"text": text},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"🤖 Comprador: {data['response']}")
            print(f"📊 Estado: {data['state']}")
            print(f"🏁 Terminada: {data['conversation_finished']}")
            print(f"❌ Cancelada: {data['conversation_negotiation_cancel']}")
            print("-" * 60)
            return data
        else:
            print(f"❌ Error: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        return None

def main():
    print("🧪 PRUEBA MANUAL DEL API SERVER")
    print("=" * 50)
    
    while True:
        vendedor_msg = input("\n👤 Vendedor (o 'quit' para salir): ")
        
        if vendedor_msg.lower() in ['quit', 'exit', 'q']:
            print("👋 ¡Adiós!")
            break
            
        result = test_chat(vendedor_msg)
        
        # Si terminó la conversación, parar
        if result and result.get('conversation_finished'):
            print("\n🎉 ¡CONVERSACIÓN TERMINADA!")
            print("✅ QR detectado correctamente")
            break

if __name__ == "__main__":
    main()