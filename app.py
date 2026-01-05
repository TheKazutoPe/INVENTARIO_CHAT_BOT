from flask import Flask, render_template, request, jsonify
from supabase_client import get_supabase
from config import (
    BITACORAS_TABLE,
    ACUMULADO_TABLE,
    CATALOGO_CLARO_TABLE,
    CATALOGO_CICSA_TABLE,
    CLARO_CODE_COL,
    CLARO_DESC_COL,
    CLARO_SIMPLE_COL,
    CICSA_CODE_COL,
    CICSA_DESC_COL,
    CICSA_SIMPLE_COL,
    CATALOGO_ACTIVO_COL,
)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_material_row(origen: str, row: dict) -> dict:
    """Normaliza el resultado para el frontend."""
    if origen.lower() == "claro":
        codigo = row.get(CLARO_CODE_COL)
        descripcion = row.get(CLARO_DESC_COL)
        simple = row.get(CLARO_SIMPLE_COL)
    else:
        codigo = row.get(CICSA_CODE_COL)
        descripcion = row.get(CICSA_DESC_COL)
        simple = row.get(CICSA_SIMPLE_COL)

    unidad = row.get("unidad")

    # Si hay nombre simple, lo agregamos visualmente
    display_desc = descripcion
    if simple and simple.strip() and simple.lower() not in (descripcion or "").lower():
        display_desc = f"{descripcion} ({simple})"

    return {
        "codigo": codigo,
        "descripcion": display_desc,
        "unidad": unidad,
        "nombre_simple": simple
    }


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.route("/materiales/<int:bitacora_id>")
def materiales_form(bitacora_id: int):
    sb = get_supabase()
    try:
        # Obtenemos datos de la bitácora
        resp = sb.table(BITACORAS_TABLE).select("*").eq("id", bitacora_id).single().execute()
        bitacora = resp.data
        if not bitacora:
            return render_template("not_found.html"), 404

        # --- LÓGICA DE BRIGADAS ---
        # Extraemos las brigadas asignadas (bri1_oficial hasta bri5_oficial)
        brigadas = []
        for i in range(1, 6):
            key = f"bri{i}_oficial"
            val = bitacora.get(key)
            if val and val.strip():
                brigadas.append(val)

        # Fallback si no hay ninguna
        if not brigadas:
            brigadas = ["Sin Asignar"]

    except Exception as e:
        print("Error cargando bitácora:", e)
        return render_template("not_found.html"), 500

    return render_template("materiales_form.html", bitacora=bitacora, brigadas=brigadas)


@app.get("/api/materiales/buscar")
def api_buscar_materiales():
    """Busca en código, descripción Y nombre_simple."""
    origen = request.args.get("origen", "", type=str).lower()
    q = request.args.get("q", "", type=str).strip()

    if origen not in {"claro", "cicsa"}:
        return jsonify({"ok": False, "error": "Origen inválido"}), 400

    if len(q) < 3:
        return jsonify({"ok": True, "items": []})

    sb = get_supabase()

    if origen == "claro":
        tabla = CATALOGO_CLARO_TABLE
        c_code = CLARO_CODE_COL
        c_desc = CLARO_DESC_COL
        c_simple = CLARO_SIMPLE_COL
    else:
        tabla = CATALOGO_CICSA_TABLE
        c_code = CICSA_CODE_COL
        c_desc = CICSA_DESC_COL
        c_simple = CICSA_SIMPLE_COL

    try:
        term = f"%{q}%"
        # Filtro OR: busca si el texto está en el código O descripción O nombre simple
        or_filter = f"{c_code}.ilike.{term},{c_desc}.ilike.{term},{c_simple}.ilike.{term}"

        resp = (
            sb.table(tabla)
            .select(f"{c_code},{c_desc},unidad,{c_simple}")
            .eq(CATALOGO_ACTIVO_COL, True)
            .or_(or_filter)
            .limit(20)
            .execute()
        )

        rows = resp.data or []
        items = [_build_material_row(origen, r) for r in rows]

        return jsonify({"ok": True, "items": items})

    except Exception as e:
        print("Error buscando materiales:", e)
        return jsonify({"ok": False, "error": "Error interno"}), 500


@app.get("/api/materiales/listar/<int:bitacora_id>")
def api_listar_materiales(bitacora_id: int):
    sb = get_supabase()
    try:
        resp = (
            sb.table(ACUMULADO_TABLE)
            .select("*")
            .eq("bitacora_id", bitacora_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = resp.data or []
        # Formatear fecha simple
        for row in rows:
            if row.get("created_at"):
                row["created_at"] = row["created_at"][:16].replace("T", " ")

        return jsonify({"ok": True, "items": rows})
    except Exception as e:
        print("Error listando:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/materiales/guardar")
def api_guardar_material():
    data = request.get_json(silent=True) or {}
    try:
        # Validación
        if not data.get("bitacora_id") or not data.get("codigo"):
            return jsonify({"ok": False, "error": "Faltan datos"}), 400

        sb = get_supabase()
        resp = sb.table(ACUMULADO_TABLE).insert({
            "bitacora_id": data["bitacora_id"],
            "origen": data.get("origen", "Claro").capitalize(),
            "brigada": data.get("brigada"),  # <--- GUARDAMOS LA BRIGADA
            "codigo": data["codigo"],
            "descripcion": data["descripcion"],
            "unidad": data.get("unidad"),
            "cantidad": float(data["cantidad"])
        }).execute()

        return jsonify({"ok": True, "item": (resp.data or [{}])[0]})
    except Exception as e:
        print("Error guardando:", e)
        return jsonify({"ok": False, "error": "Error guardando en BD"}), 500


@app.delete("/api/materiales/borrar/<int:item_id>")
def api_borrar_material(item_id: int):
    """Elimina un registro de material."""
    sb = get_supabase()
    try:
        sb.table(ACUMULADO_TABLE).delete().eq("id", item_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        print("Error borrando:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)