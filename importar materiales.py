import pandas as pd
from supabase import create_client, Client
import re
import os
from dotenv import load_dotenv

# 1. Cargar variables de entorno desde el archivo .env
load_dotenv()

# Obtener credenciales (Usamos SERVICE_ROLE para tener permisos completos de escritura)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Verificación rápida
if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: No se encontraron las claves en el archivo .env")
    print("Asegúrate de tener SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY definidos.")
    exit()

# Configuración del archivo y tabla
ARCHIVO_EXCEL = "MATERIALES CLARO.xlsx"
NOMBRE_TABLA = "materiales_claro"  # Usaremos la tabla específica para estos materiales con costos

print(f"Conectando a Supabase: {SUPABASE_URL}...")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def limpiar_costo(valor):
    """Convierte el string de costo (ej. '$ 14.55') a float (14.55)"""
    if pd.isna(valor) or valor == '':
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', str(valor))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def cargar_datos():
    print(f"Leyendo archivo: {ARCHIVO_EXCEL}...")

    try:
        df = pd.read_excel(ARCHIVO_EXCEL)
    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{ARCHIVO_EXCEL}' en la carpeta actual.")
        return

    # Renombrar columnas para que coincidan con la base de datos
    df.rename(columns={
        'Categoria': 'categoria',
        'Subcategoria': 'subcategoria',
        'Codigo': 'codigo',
        'Partidas de Materiales Utilizadas Por Mantenimiento PEXT': 'descripcion',
        'Moneda': 'moneda',
        'Codigo2': 'codigo_secundario',
        'Costo': 'costo_raw',
        'Subcategoria2': 'subcategoria_secundaria'
    }, inplace=True)

    print("Limpiando datos...")
    # Limpiar costos y textos
    df['costo'] = df['costo_raw'].apply(limpiar_costo)

    text_cols = ['categoria', 'subcategoria', 'codigo', 'descripcion', 'moneda', 'codigo_secundario',
                 'subcategoria_secundaria']
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str)

    # Definir columnas finales
    columnas_finales = ['categoria', 'subcategoria', 'codigo', 'descripcion', 'moneda', 'codigo_secundario', 'costo',
                        'subcategoria_secundaria']
    columnas_a_insertar = [c for c in columnas_finales if c in df.columns]

    registros = df[columnas_a_insertar].to_dict(orient='records')

    print(f"Preparando para insertar {len(registros)} registros en '{NOMBRE_TABLA}'...")

    # Insertar en lotes
    batch_size = 100
    for i in range(0, len(registros), batch_size):
        batch = registros[i:i + batch_size]
        try:
            response = supabase.table(NOMBRE_TABLA).insert(batch).execute()
            print(f"Lote {i // batch_size + 1} insertado correctamente.")
        except Exception as e:
            print(f"Error insertando lote {i // batch_size + 1}: {e}")

    print("¡Carga completada!")


if __name__ == "__main__":
    cargar_datos()