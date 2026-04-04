import pandas as pd
import os

def crear_plantilla():
    # Estructura plana solicitada por el usuario
    data = {
        'CODIGO AX': ['000010136', '000018738'],
        'DESCRIPCION DEL MATERIAL': ['MUFA VERTICA TERMOCONTRAIBLE', 'INVERSOR 1 A 2'],
        'CONTRATA': ['EJEMPLO_CONTRATA', 'EJEMPLO_CONTRATA'],
        'BRIGADA': ['LIMA B1', 'HUANCAYO B2'],
        'UNIDAD': ['UNI', 'UNI'],
        'CANTIDAD': [10, 50]
    }
    
    df = pd.DataFrame(data)
    ruta_salida = 'PLANTILLA_MASIVA_V2.xlsx'
    
    with pd.ExcelWriter(ruta_salida, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='CARGA_STOCK')
        worksheet = writer.sheets['CARGA_STOCK']
        worksheet.column_dimensions['A'].width = 15
        worksheet.column_dimensions['B'].width = 45
        worksheet.column_dimensions['C'].width = 20
        worksheet.column_dimensions['D'].width = 20
        worksheet.column_dimensions['E'].width = 15
        worksheet.column_dimensions['F'].width = 15
            
    print(f"Plantilla generada exitosamente en: {os.path.abspath(ruta_salida)}")

if __name__ == '__main__':
    crear_plantilla()
