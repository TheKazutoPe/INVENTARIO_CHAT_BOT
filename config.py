import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    # Nombres de Tablas
    CATALOGO_CLARO_TABLE = os.getenv('CATALOGO_CLARO_TABLE', 'materiales_claro')
    ACUMULADO_TABLE = os.getenv('ACUMULADO_TABLE', 'materiales_acumulado')

    # Columnas Claro
    CLARO_CODE_COL = os.getenv('CLARO_CODE_COL', 'codigo')
    CLARO_DESC_COL = os.getenv('CLARO_DESC_COL', 'descripcion')
    CLARO_UNIDAD_COL = os.getenv('CLARO_UNIDAD_COL', 'moneda')
    CLARO_COSTO_COL = os.getenv('CLARO_COSTO_COL', 'costo')