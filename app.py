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
#  FUNCION AUXILIAR: CALCULAR IDENTIFICADOR (INC > TAS > SOT > ID)
# =====================================================================
def get_identifier(b_data):
    """
    Determina qué mostrar como identificador principal siguiendo la prioridad:
    1. INCIDENCIA
    2. TAS
    3. SOT
    4. CÓDIGO BD (Correlativo) - Solo si todo lo demás falla
    """
    if not b_data: return "S/N"

    # Extraer y limpiar valores
    inc = str(b_data.get('nroincidencia_bd') or '').strip()
    tas = str(b_data.get('nrotas_bd') or '').strip()
    sot = str(b_data.get('nrosot_bd') or '').strip()
    correlativo = str(b_data.get('codigo_bd') or '').strip()

    invalidos = ['NONE', 'NULL', 'NAN', 'NO TIENE', '', '0']

    if inc and inc.upper() not in invalidos: return inc
    if tas and tas.upper() not in invalidos: return tas
    if sot and sot.upper() not in invalidos: return sot

    return correlativo  # Último recurso


# =====================================================================
#  VISTA 1: TÉCNICO (REGISTRO)
# =====================================================================
@app.route('/materiales/<bitacora_id>')
def materiales_view(bitacora_id):
    if not bitacora_id.isdigit(): return render_template('not_found.html', error="ID inválido"), 400
    try:
        res = supabase.table('bitacoras').select("*").eq('id', int(bitacora_id)).execute()
        if not res.data: return render_template('not_found.html', error="Bitácora no encontrada"), 404
        bitacora = res.data[0]

        historial_res = supabase.table(Config.ACUMULADO_TABLE) \
            .select("*") \
            .eq('bitacora_id', str(bitacora_id)) \
            .order('id', desc=True) \
            .execute()
        historial = historial_res.data if historial_res.data else []

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
#  VISTA 2: SUPERVISOR (REPORTES)
# =====================================================================
@app.route('/reportes')
def reportes_view():
    return render_template('reportes.html')


@app.route('/api/acumulados-data', methods=['GET'])
def get_acumulados_data():
    try:
        # 1. Traer datos acumulados
        res = supabase.table(Config.ACUMULADO_TABLE).select("*").order('id', desc=True).limit(200).execute()
        data = res.data

        if not data: return jsonify([])

        # 2. "Curar" los datos en vivo consultando las bitácoras originales
        # (Esto arregla visualmente registros viejos mal guardados)
        bitacora_ids = list(set([str(r['bitacora_id']) for r in data if r.get('bitacora_id')]))

        if bitacora_ids:
            # Traemos la info real de las bitácoras involucradas
            b_res = supabase.table('bitacoras') \
                .select('id, nroincidencia_bd, nrotas_bd, nrosot_bd, codigo_bd') \
                .in_('id', bitacora_ids) \
                .execute()

            # Crear mapa de acceso rápido { id_bitacora: datos_bitacora }
            b_map = {str(b['id']): b for b in b_res.data}

            # Sobrescribir el campo 'inc' con el dato correcto calculado en vivo
            for r in data:
                bid = str(r.get('bitacora_id'))
                if bid in b_map:
                    # Aquí sucede la magia: Recalcula INC/TAS/SOT
                    r['inc'] = get_identifier(b_map[bid])

        return jsonify(data)
    except Exception as e:
        print(f"Error Reporte: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/exportar-excel')
def exportar_excel():
    try:
        # 1. Descargar Acumulados
        res = supabase.table(Config.ACUMULADO_TABLE).select("*").execute()
        data = res.data
        if not data: return "No hay datos", 404

        # 2. Reparación de datos masiva (Igual que en la vista web)
        bitacora_ids = list(set([str(r['bitacora_id']) for r in data if r.get('bitacora_id')]))
        if bitacora_ids:
            # Traemos info por lotes (si son muchos podría requerir paginación, pero para <1000 ok)
            b_res = supabase.table('bitacoras') \
                .select('id, nroincidencia_bd, nrotas_bd, nrosot_bd, codigo_bd') \
                .in_('id', bitacora_ids) \
                .execute()
            b_map = {str(b['id']): b for b in b_res.data}

            for r in data:
                bid = str(r.get('bitacora_id'))
                if bid in b_map:
                    r['inc'] = get_identifier(b_map[bid])  # Corregimos la columna inc

        df = pd.DataFrame(data)

        # Limpieza de Ceros en código material
        if 'cod_material' in df.columns:
            df['cod_material'] = df['cod_material'].astype(str).str.lstrip('0')

        column_map = {
            'fecha_asign_inc': 'FECHA ASIGN INC',
            'inc': 'INC / TAS / SOT',  # Esta columna ahora tendrá el dato correcto
            'sot': 'SOT REF',
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
            'origen_material': 'ORIGEN (CLARO/CICSA)',
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

        df.rename(columns=column_map, inplace=True)
        cols_finales = [val for key, val in column_map.items() if val in df.columns]
        df = df[cols_finales]

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
#  BÚSQUEDA Y GUARDADO
# =====================================================================
@app.route('/api/search', methods=['GET'])
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 2: return jsonify([])

    try:
        res = supabase.table('catalogo_unificado') \
            .select("*") \
            .or_(f"descripcion.ilike.%{q}%,codigo.ilike.%{q}%,cod_sap.ilike.%{q}%,cod_ax.ilike.%{q}%") \
            .limit(20) \
            .execute()

        resultados = []
        for item in res.data:
            costo_str = str(item.get('costo', '0')).replace('$', '').replace(',', '').strip()
            try:
                costo_val = float(costo_str) if costo_str else 0.0
            except:
                costo_val = 0.0

            ax = str(item.get('cod_ax', '')).strip()
            if ax and ax not in ['NO TIENE']: ax = ax.lstrip('0')

            sap = str(item.get('cod_sap', '')).strip()
            internal = str(item.get('codigo', '')).strip()

            codigo_visual = 'S/C'
            if ax and ax not in ['NO TIENE', 'nan', 'None', '']:
                codigo_visual = ax
            elif sap and sap not in ['NO TIENE', 'nan', 'None', '']:
                codigo_visual = sap
            else:
                codigo_visual = internal

            resultados.append({
                'codigo': internal,
                'codigo_visual': codigo_visual,
                'descripcion': item.get('descripcion'),
                'costo': costo_val,
                'categoria': item.get('categoria', 'GENERAL'),
                'subcategoria': item.get('unidad', ''),
                'origen': item.get('empresa', 'CLARO'),
            })
        return jsonify(resultados)
    except Exception as e:
        return jsonify([])


@app.route('/api/save-single', methods=['POST'])
def save_single():
    d = request.json
    try:
        b = supabase.table('bitacoras').select("*").eq('id', int(d['bid'])).execute().data[0]
        now = datetime.datetime.now()

        # Usamos la misma función auxiliar para consistencia
        identifier = get_identifier(b)

        origen = d['item'].get('origen', 'CLARO')
        cant = float(d['cant'])
        price = float(d['item']['costo'] or 0)

        row = {
            'bitacora_id': str(d['bid']),
            'brigada_responsable': d['bri'],
            'fecha_guardado': now.isoformat(),
            'inc': identifier,  # Guardamos el correcto
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
            'origen_material': origen,
            'precio_unit': price,
            'cant_material': cant,
            'moneda': 'D',
            'tc': 3.75,
            'subtotal': cant * price,
            'total_soles': (cant * price) * 3.75,
            'trabajo_concluido': b.get('estado_trabajo'),
            'porcentaje_ejecucion': 100 if b.get('is_cerrada') else 0,
            'validado_oym': 'PENDIENTE'
        }

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