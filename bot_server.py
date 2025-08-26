# bot_server.py
# Este archivo es el "recepcionista". Utiliza el micro-framework Flask para crear
# un servidor web que escucha las peticiones de Twilio (cuando llega un WhatsApp).
# Su trabajo es recibir las imágenes y orquestar el procesamiento en hilos
# separados para no bloquear el servidor.

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests
import os
import threading
import time
from main import procesar_gasto_completo, configurar_servicios
from collections import defaultdict

# --- CONFIGURACIÓN E INICIALIZACIÓN ---
try:
    # Llama a la función de chequeo de 'main.py' para asegurar que todo está listo.
    configurar_servicios()
except Exception as e:
    print(f"ERROR FATAL AL INICIAR: {e}")
    exit()

# Creamos la aplicación web con Flask.
app = Flask(__name__)

# Cargamos las credenciales de Twilio desde las variables de entorno.
ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_NUMBER = 'whatsapp:+14155238886' # El número de WhatsApp de Twilio.

if not ACCOUNT_SID or not AUTH_TOKEN:
    raise ValueError("Credenciales de Twilio no encontradas en .env")

# Creamos un cliente de Twilio para poder ENVIAR mensajes.
twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN)

# --- LÓGICA DE PROCESAMIENTO CONCURRENT E ---

# Usamos 'defaultdict' para crear un diccionario que manejará los contadores
# de procesamiento para cada usuario que envíe imágenes.
resultados_por_usuario = defaultdict(lambda: {'exitos': 0, 'fallos': 0, 'total': 0})
# Un 'Lock' es un mecanismo para prevenir que dos hilos modifiquen los contadores
# al mismo tiempo, lo que podría causar resultados incorrectos.
lock = threading.Lock()

def procesar_y_contar(media_url: str, sender_number: str):
    """
    Esta función es el trabajo que realiza cada hilo. Descarga una imagen,
    la procesa llamando a la lógica de 'main.py' y actualiza los contadores.
    """
    # Creamos un nombre de archivo temporal único para evitar colisiones.
    id_usuario = sender_number.split(':')[-1]
    timestamp = int(time.time() * 1000)
    url_hash = hash(media_url) & 0xffffffff
    ruta_temporal_imagen = f"temp_{id_usuario}_{timestamp}_{url_hash}.jpg"
    
    exito_final = False
    try:
        # Descargamos la imagen de la URL que nos da Twilio.
        response = requests.get(media_url, auth=(ACCOUNT_SID, AUTH_TOKEN))
        if response.status_code == 200:
            # Guardamos la imagen en el archivo temporal.
            with open(ruta_temporal_imagen, 'wb') as f:
                f.write(response.content)
            
            # Llamamos al "cerebro" en 'main.py' para que haga todo el trabajo pesado.
            if procesar_gasto_completo(ruta_temporal_imagen):
                exito_final = True
            
            # Borramos el archivo temporal para no ocupar espacio en disco.
            os.remove(ruta_temporal_imagen)
        else:
            print(f"Fallo al descargar imagen. Status: {response.status_code}")
    except Exception as e:
        print(f"Error crítico en el hilo de procesamiento: {e}")

    # --- SECCIÓN CRÍTICA ---
    # Usamos 'with lock:' para asegurar que solo un hilo a la vez pueda
    # modificar el diccionario de resultados.
    with lock:
        if exito_final:
            resultados_por_usuario[sender_number]['exitos'] += 1
        else:
            resultados_por_usuario[sender_number]['fallos'] += 1
        
        # Comprobamos si este fue el último ticket del lote.
        procesados = resultados_por_usuario[sender_number]['exitos'] + resultados_por_usuario[sender_number]['fallos']
        total_a_procesar = resultados_por_usuario[sender_number]['total']

        if procesados == total_a_procesar:
            # Si terminamos, construimos y enviamos el mensaje de resumen al usuario.
            exitos = resultados_por_usuario[sender_number]['exitos']
            fallos = resultados_por_usuario[sender_number]['fallos']
            mensaje_resumen = f"🧾 *Resumen de tickets procesados:*\n\n..."
            
            twilio_client.messages.create(body=mensaje_resumen, from_=TWILIO_NUMBER, to=sender_number)
            
            # Limpiamos los contadores, listos para el próximo envío.
            del resultados_por_usuario[sender_number]

# --- RUTAS DEL SERVIDOR WEB (ENDPOINTS) ---
@app.route("/")
def index():
    """Ruta de chequeo para saber si el servidor está vivo."""
    return "Bot de Gastos para Comanda Central - Funcionando.", 200

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    """
    Este es el endpoint de webhook. Twilio hará una petición POST aquí cada vez
    que alguien envíe un mensaje a tu número de WhatsApp.
    """
    num_media = int(request.values.get("NumMedia", 0)) # Obtenemos cuántas imágenes se enviaron.
    sender_number = request.values.get("From") # El número del remitente.

    if num_media > 0:
        with lock:
            # Inicializamos los contadores para este nuevo lote de imágenes.
            resultados_por_usuario[sender_number]['total'] = num_media
            # ... (reseteo de exitos y fallos)

        # Enviamos una única confirmación inmediata al usuario.
        resp = MessagingResponse()
        resp.message(f"¡Recibí {num_media} imágenes! Te enviaré un resumen al terminar.")
        
        # --- MULTI-HILO (CONCURRENCIA) ---
        # Por cada imagen, creamos y lanzamos un nuevo hilo de ejecución.
        # Esto permite que el bot procese varias imágenes a la vez,
        # sin que el usuario tenga que esperar a que termine una para que empiece la otra.
        for i in range(num_media):
            media_url = request.values.get(f"MediaUrl{i}")
            if media_url:
                # 'target' es la función que el hilo ejecutará.
                # 'args' son los argumentos que le pasamos a esa función.
                thread = threading.Thread(target=procesar_y_contar, args=(media_url, sender_number))
                thread.start() # Inicia la ejecución del hilo.
        
        return str(resp) # Devolvemos la respuesta de confirmación a Twilio.
    else:
        # Si no se enviaron imágenes, enviamos un mensaje de bienvenida.
        resp = MessagingResponse()
        resp.message("Hola! Envíame fotos de tus tickets para registrar un gasto.")
        return str(resp)
    
    return '', 204 # Retorno por defecto.

# Este bloque solo se ejecuta si corres 'python bot_server.py'
if __name__ == "__main__":
    # Inicia el servidor Flask. 'debug=False' es importante para producción.
    app.run(port=5000, debug=False)