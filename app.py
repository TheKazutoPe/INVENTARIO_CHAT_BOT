import os
import datetime
import io
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file
from supabase import create_client, Client
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

# --- CONEXIÓN SUPABASE ---
try:
    supabase: Client = create_client(app.config['SUPABASE_URL'], app.config['SUPABASE_KEY'])
    print("✅ Conexión a Supabase establecida.")
except Exception as e:
    print(f"❌ Error crítico conectando a Supabase: {e}")


# =====================================================================
#  VISTA 1: TÉCNICO (REGISTRO PURO)
# =====================================================================
@app.route('/materiales/<bitacora_id>')
def materiales_view(bitacora_id):
    if not bitacora_id.isdigit(): return render_template('not_found.html', error="ID inválido"), 400
    try:
        # A. Datos Bitácora
        res = supabase.table('bitacoras').select("*").eq('id', int(bitacora_id)).execute()
        if not res.data: return render_template('not_found.html', error="Bitácora no encontrada"), 404
        bitacora = res.data[0]

        # B. Historial Local (Solo de esta bitácora)
        historial_res = supabase.table(Config.ACUMULADO_TABLE) \
            .select("*") \
            .eq('bitacora_id', str(bitacora_id)) \
            .order('id', desc=True) \
            .execute()
        historial = historial_res.data if historial_res.data else []

        # C. Brigadas (Mapeo Oficial -> Corto)
        brigadas = []
        raw_names = [bitacora.get(f'bri{i}_oficial') for i in range(1, 6) if bitacora.get(f'bri{i}_oficial')]

        if raw_names:
            try:
                map_res = supabase.table('brigada_tabla').select('name_brigada_bd, brigada_main').in_('name_brigada_bd',
                                                                                                      raw_names).execute()
                mapping = {x['name_brigada_bd']: x['brigada_main'] for x in map_res.data}
                for name in raw_names:
                    short = mapping.get(name, name)
                    brigadas.append({'val': short, 'lbl': short})
            except:
                brigadas = [{'val': n, 'lbl': n} for n in raw_names]

        return render_template('materiales_form.html', bid=bitacora_id, b=bitacora, brigadas=brigadas,
                               historial=historial)
    except Exception as e:
        print(e)
        return f"Error servidor: {e}", 500


# =====================================================================
#  VISTA 2: SUPERVISOR (REPORTES Y EXCEL)
# =====================================================================
@app.route('/reportes')
def reportes_view():
    return render_template('reportes.html')


@app.route('/api/acumulados-data', methods=['GET'])
def get_acumulados_data():
    try:
        # Trae los últimos 200 registros para visualización rápida
        res = supabase.table(Config.ACUMULADO_TABLE).select("*").order('id', desc=True).limit(200).execute()
        return jsonify(res.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/exportar-excel')
def exportar_excel():
    try:
        # 1. Descargar TODO
        res = supabase.table(Config.ACUMULADO_TABLE).select("*").execute()
        data = res.data
        if not data: return "No hay datos", 404

        df = pd.DataFrame(data)

        # 2. Mapeo Columnas BD -> Excel Final
        column_map = {
            'fecha_asign_inc': 'FECHA ASIGN INC',
            'inc': 'INC',
            'sot': 'SOT',
            'red_afect': 'RED AFECT',
            'region': 'REGION',
            'subregion': 'SUBREGION',
            'base_cuadrilla': 'BASE CUADRILLA',
            'sup_claro': 'SUP. CLARO',
            'sup_contrata': 'SUP. CONTRATA',
            'id_site_inicio': 'ID SITE INICIO',
            'name_site_inicio': 'NAME SITE INICIO',
            'otdr': 'OTDR',
            'causa_averia': 'CAUSA DE AVERÍA',
            'tipo_mmto': 'TIPO DE MMTO',
            'categoria': 'CATEGORIA',
            'subcategoria': 'SUBCATEGORIA',
            'cod_material': 'COD MATERIAL',
            'nombre_material': 'NOMBRE MATERIAL',
            # COLUMNA NUEVA SOLICITADA ------------------
            'origen_material': 'ORIGEN (CLARO/CICSA)',
            # -------------------------------------------
            'precio_unit': 'PRECIO UNIT.',
            'cant_material': 'CANT. MATERIAL',
            'moneda': 'MONEDA',
            'tc': 'TC',
            'subtotal': 'SUBTOTAL',
            'total_soles': 'TOTAL S/',
            'fecha_guardado': 'FECHA_GUARDADO',
            'id_site_fin': 'ID SITE FIN',
            'name_site_fin': 'NAME SITE FIN',
            'trabajo_concluido': 'TRABAJO CONCLUIDO?',
            'porcentaje_ejecucion': '% EJECUCION',
            'comentario': 'COMENTARIO',
            'validado_oym': 'VALIDADO OYM',
            'validado_jefatura': 'VALIDADO JEFATURA',
            'observaciones_cicsa': 'OBSERVACIONES CICSA',
            'observaciones_bd': 'OBSERVACIONES BD',
            'observacion_oym': 'OBSERVACION OYM',
            'fecha_validado': 'FECHA_VALIDADO',
            'mes_liq': 'MES_LIQ',
            'sem_uso': 'SEM_USO',
            'pago': 'PAGO',
            'brigada_responsable': 'BRIGADA'
        }

        # Renombrar columnas que existan en el DataFrame
        df.rename(columns=column_map, inplace=True)

        # 3. Filtrar y Ordenar columnas finales (Solo las que existen)
        cols_finales = [val for key, val in column_map.items() if val in df.columns]
        df = df[cols_finales]

        # 4. Generar Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Base_Acumulada')
        output.seek(0)

        filename = f"Reporte_Materiales_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=filename)

    except Exception as e:
        print(f"Error exportando: {e}")
        return f"Error: {str(e)}", 500


# =====================================================================
#  BÚSQUEDA HÍBRIDA (CLARO + CICSA)
# =====================================================================
@app.route('/api/search', methods=['GET'])
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 3: return jsonify([])

    resultados = []
    try:
        # 1. Buscar en CLARO
        res_claro = supabase.table(Config.CATALOGO_CLARO_TABLE).select("*").or_(
            f"{Config.CLARO_DESC_COL}.ilike.%{q}%,{Config.CLARO_CODE_COL}.ilike.%{q}%").limit(10).execute()
        for item in res_claro.data:
            resultados.append({
                'codigo': item.get(Config.CLARO_CODE_COL),
                'descripcion': item.get(Config.CLARO_DESC_COL),
                'costo': float(item.get('costo') or 0),
                'categoria': item.get('categoria', 'TELECOM'),
                'subcategoria': item.get('subcategoria', ''),
                'origen': 'CLARO'  # <--- Marca de origen
            })

        # 2. Buscar en CICSA (Tabla Nueva)
        res_cicsa = supabase.table('catalogo_cicsa_materiales').select("*").or_(
            f"descripcion.ilike.%{q}%,cod_ax.ilike.%{q}%").eq('activo', True).limit(10).execute()
        for item in res_cicsa.data:
            resultados.append({
                'codigo': item.get('cod_ax'),
                'descripcion': item.get('descripcion'),
                'costo': float(item.get('costo_unitario') or 0),
                'categoria': item.get('tipo_uso', 'FERRETERIA'),  # Usamos tipo_uso como categoría
                'subcategoria': item.get('unidad', ''),
                'origen': 'CICSA'  # <--- Marca de origen
            })

        return jsonify(resultados)
    except Exception as e:
        print(e)
        return jsonify([])


@app.route('/api/save-single', methods=['POST'])
def save_single():
    d = request.json
    try:
        # Recuperar datos frescos de la bitácora
        b = supabase.table('bitacoras').select("*").eq('id', int(d['bid'])).execute().data[0]
        now = datetime.datetime.now()

        # Detectar Origen (Si no viene, asumimos Claro)
        origen = d['item'].get('origen', 'CLARO')

        cant = float(d['cant'])
        price = float(d['item']['costo'] or 0)
        sub = cant * price
        tc = 3.75

        row = {
            'bitacora_id': str(d['bid']),
            'brigada_responsable': d['bri'],
            'fecha_guardado': now.isoformat(),
            'inc': b.get('nroincidencia_bd') or str(b.get('codigo_bd')),
            'fecha_asign_inc': b.get('fecha_asignacion_bd'),
            'sot': b.get('nrosot_bd'),
            'red_afect': b.get('red1_bd'),
            'region': b.get('zona_bd'),
            'subregion': b.get('departamento_bd'),
            'base_cuadrilla': b.get('base_bd'),
            'sup_claro': b.get('responsable_claro_bd'),
            'sup_contrata': b.get('responsable_cicsa_bd'),
            'id_site_inicio': b.get('nombresite_bd'),
            'name_site_inicio': b.get('nombresite_bd'),
            'otdr': b.get('otdr_bd'),
            'causa_averia': b.get('causa_bd'),
            'tipo_mmto': b.get('tipoaveria_bd'),
            'categoria': d['item'].get('categoria'),
            'subcategoria': d['item'].get('subcategoria'),
            'cod_material': d['item'].get('codigo'),
            'nombre_material': d['item'].get('descripcion'),
            'origen_material': origen,  # <--- GUARDADO EN BD
            'precio_unit': price,
            'cant_material': cant,
            'moneda': 'D',
            'tc': tc,
            'subtotal': sub,
            'total_soles': sub * tc,
            'trabajo_concluido': b.get('estado_trabajo'),
            'porcentaje_ejecucion': 100 if b.get('is_cerrada') else 0,
            'mes_liq': '',  # Se calculan si existen fechas
            'sem_uso': '',
            'validado_oym': 'PENDIENTE'
        }

        # Calcular mes/semana si hay fecha
        fa_str = b.get('fecha_asignacion_bd')
        if fa_str:
            try:
                dt = datetime.datetime.fromisoformat(str(fa_str).replace('Z', ''))
                row['mes_liq'] = dt.strftime('%B').upper()
                row['sem_uso'] = dt.strftime('%Y%U')
            except:
                pass

        final_res = supabase.table(Config.ACUMULADO_TABLE).insert([row]).execute()
        return jsonify({'ok': True, 'saved': final_res.data[0]})
    except Exception as e:
        print(f"Error save: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/delete-item', methods=['POST'])
def delete_item():
    d = request.json
    try:
        supabase.table(Config.ACUMULADO_TABLE).delete().eq('id', d.get('item_id')).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')