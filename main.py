# main.py
# Este archivo contiene el "cerebro" del bot. Define toda la lógica de negocio
# para procesar un gasto, desde leer la imagen hasta registrarlo en la API.
# Está diseñado para ser independiente del servidor web, lo que facilita las pruebas.

import os
import json
import pytesseract
import cv2
import google.generativeai as genai
import requests
from datetime import date
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader

# --- CONFIGURACIÓN CENTRALIZADA ---
# Carga las variables de entorno desde el archivo .env.
load_dotenv()

# Leemos todas las claves secretas y URLs de las variables de entorno.
# Esto mantiene la configuración separada del código.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
COMANDA_CENTRAL_API_URL = os.getenv("COMANDA_CENTRAL_API_URL")
COMANDA_CENTRAL_API_TOKEN = os.getenv("COMANDA_CENTRAL_API_TOKEN") 
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# --- FUNCIONES AUXILIARES ---

def configurar_servicios():
    """
    Función de chequeo inicial. Valida que todas las credenciales necesarias
    estén presentes y configura los clientes de las APIs de Gemini y Cloudinary.
    Si falta alguna clave, detiene el programa con un error claro.
    """
    print("Iniciando configuración de servicios...")
    if not GEMINI_API_KEY: raise ValueError("GEMINI_API_KEY no encontrada.")
    if not COMANDA_CENTRAL_API_URL: raise ValueError("COMANDA_CENTRAL_API_URL no encontrada.")
    if not COMANDA_CENTRAL_API_TOKEN: raise ValueError("COMANDA_CENTRAL_API_TOKEN no encontrado.")
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
        raise ValueError("Credenciales de Cloudinary no encontradas en .env")
    
    genai.configure(api_key=GEMINI_API_KEY)
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET
    )
    print("-> Servicios configurados con éxito.")

def extraer_texto_de_imagen(ruta_imagen: str) -> str:
    """
    ESTACIÓN 1: El Digitalizador (OCR - Reconocimiento Óptico de Caracteres).
    Usa la librería OpenCV para leer y pre-procesar la imagen (convertirla a escala de grises)
    y luego usa Tesseract para "leer" el texto de la imagen.
    """
    if not os.path.exists(ruta_imagen): raise FileNotFoundError(f"Imagen no encontrada: {ruta_imagen}")
    # Leer la imagen desde la ruta del archivo.
    imagen_cv = cv2.imread(ruta_imagen)
    # Convertir a escala de grises, que a menudo mejora la precisión del OCR.
    gris = cv2.cvtColor(imagen_cv, cv2.COLOR_BGR2GRAY)
    # Ejecutar Tesseract sobre la imagen procesada.
    return pytesseract.image_to_string(gris, lang='spa', config=r'--oem 3 --psm 4')

def analizar_texto_con_gemini(texto_ticket: str) -> dict | None:
    """
    ESTACIÓN 2: El Analista Inteligente (IA).
    Envía el texto extraído a la IA de Google (Gemini) con instrucciones precisas
    para que devuelva un JSON estructurado con el total y la categoría del gasto.
    """
    # Configuramos el modelo para que sea determinista (temperature=0.0) y
    # para que intente devolver directamente un JSON.
    generation_config = {"temperature": 0.0, "response_mime_type": "application/json"}
    model = genai.GenerativeModel('gemini-1.5-flash', generation_config=generation_config)
    
    # El "prompt" es el conjunto de instrucciones que le damos a la IA.
    # Es muy detallado para guiar a la IA y asegurar una respuesta consistente.
    prompt = f"""
    Analiza el siguiente texto de un ticket de compra.
    Tu única y exclusiva salida debe ser un objeto JSON válido, sin texto adicional, explicaciones ni markdown.
    El JSON debe tener esta estructura: {{ "total": float, "categoria": "string" }}
    Elige la categoría estrictamente de esta lista: ["Materia Prima", "Descartables", "Servicios", "Gastos Fijos", "Gastos Operativos", "Gastos de Mantenimiento", "Otros Gastos"].
    Si no puedes determinar un valor, usa 0.0 para el total o "Otros Gastos" para la categoría.

    Texto del ticket:
    ---
    {texto_ticket}
    ---
    """
    try:
        print("-> Solicitando análisis a la IA...")
        response = model.generate_content(prompt)
        
        # El modelo debería devolver JSON, pero por seguridad, limpiamos la respuesta
        # para extraer solo el contenido entre '{' y '}'.
        texto_json_crudo = response.text.strip()
        inicio_json = texto_json_crudo.find('{')
        fin_json = texto_json_crudo.rfind('}') + 1

        if inicio_json != -1 and fin_json != 0:
            texto_json = texto_json_crudo[inicio_json:fin_json]
            print(f"-> IA respondió con JSON: {texto_json}")
            # Convertimos la cadena de texto JSON en un diccionario de Python.
            return json.loads(texto_json)
        else:
            print(f"-> ERROR: La IA no devolvió un JSON válido. Respuesta: {texto_json_crudo}")
            return None
            
    except Exception as e:
        print(f"-> ERROR: Excepción durante el análisis de la IA: {e}")
        return None

def subir_imagen_a_cloudinary(ruta_imagen: str) -> str | None:
    """
    ESTACIÓN 3: El Archivador de Imágenes.
    Sube la imagen original del ticket a Cloudinary, un servicio de hosting de imágenes.
    Esto nos da una URL pública que podemos guardar como referencia.
    """
    try:
        print("Subiendo imagen a Cloudinary...")
        # Llama a la API de Cloudinary para subir el archivo.
        upload_result = cloudinary.uploader.upload(ruta_imagen, folder="tickets_gastos")
        print(f"-> Imagen subida con éxito. URL: {upload_result['secure_url']}")
        # Devolvemos la URL segura (https) de la imagen subida.
        return upload_result['secure_url']
    except Exception as e:
        print(f"Error al subir imagen a Cloudinary: {e}")
        return None

def guardar_gasto_en_api(datos_gasto: dict) -> bool:
    """
    ESTACIÓN 4: El Registrador Final.
    Toma los datos finales y hace una petición POST a la API de Comanda Central
    para registrar el gasto de forma permanente. Actúa como un cliente de la API.
    """
    try:
        url_endpoint = f"{COMANDA_CENTRAL_API_URL}/api/gastos"
        # Preparamos las cabeceras, incluyendo el token de autorización.
        # Así, la API de Comanda Central sabe que la petición es legítima.
        headers = {"Authorization": f"Bearer {COMANDA_CENTRAL_API_TOKEN}"}
        
        # Hacemos la petición POST con los datos del gasto en formato JSON.
        response = requests.post(url_endpoint, json=datos_gasto, headers=headers)
        
        # Esta línea es muy útil: si la respuesta fue un error (4xx o 5xx),
        # lanzará una excepción, activando el bloque 'except'.
        response.raise_for_status() 
        print(f"-> Gasto registrado con éxito en Comanda Central. Status: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error al contactar la API de Comanda Central: {e}")
        if e.response is not None: 
            print(f"Respuesta del servidor ({e.response.status_code}): {e.response.text}")
        return False

# --- FUNCIÓN ORQUESTADORA ---
def procesar_gasto_completo(ruta_imagen: str) -> bool:
    """
    Esta función es el "director de orquesta". Llama a cada "estación" en el
    orden correcto y maneja los fallos en cada paso del proceso.
    """
    try:
        print("-" * 50)
        print(f"Iniciando procesamiento para: {ruta_imagen}")

        # Paso 1: Extraer texto. Si falla, el proceso termina.
        texto_crudo = extraer_texto_de_imagen(ruta_imagen)
        if not texto_crudo or not texto_crudo.strip(): 
            print("-> Fallo: No se pudo extraer texto de la imagen (OCR).")
            return False
        
        # Paso 2: Analizar con IA. Si falla, el proceso termina.
        datos_ia = analizar_texto_con_gemini(texto_crudo)
        if not datos_ia or 'total' not in datos_ia or 'categoria' not in datos_ia: 
            print("-> Fallo: El análisis de la IA no produjo resultados válidos.")
            return False
            
        # Paso 3: Subir imagen. Si falla, usamos un placeholder pero continuamos.
        url_imagen_publica = subir_imagen_a_cloudinary(ruta_imagen)
        if not url_imagen_publica: 
            print("-> Fallo: No se pudo subir la imagen a Cloudinary.")
            url_imagen_publica = "Error al subir imagen"

        # Paso 4: Construir el objeto final para la API.
        datos_finales = {
            "fecha": date.today().isoformat(), # Usamos la fecha actual.
            "concepto": url_imagen_publica,    # El 'concepto' es la URL de la imagen.
            "categoria": datos_ia['categoria'],
            "monto": datos_ia['total']
        }
        
        # Paso 5: Guardar el gasto. El resultado de esta función es el resultado final.
        return guardar_gasto_en_api(datos_finales)

    except Exception as e:
        print(f"[ERROR CRÍTICO] Proceso falló: {e}")
        return False

# Este bloque solo se ejecuta si corres 'python main.py' directamente en la terminal.
# Sirve para hacer pruebas locales sin necesidad de un servidor web.
if __name__ == "__main__":
    try:
        configurar_servicios()
        IMAGEN_A_PROCESAR = "ticket1.jpg" # Cambia esto por el nombre de tu imagen de prueba.
        
        if os.path.exists(IMAGEN_A_PROCESAR):
            exito = procesar_gasto_completo(IMAGEN_A_PROCESAR)
            # ... (Lógica para imprimir un resumen de la prueba)
        else:
            print(f"No se encontró la imagen de prueba '{IMAGEN_A_PROCESAR}'.")
    except Exception as e:
        print(f"El programa no pudo iniciar: {e}")