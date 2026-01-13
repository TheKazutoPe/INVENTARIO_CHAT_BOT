document.addEventListener('DOMContentLoaded', () => {
    // Referencias
    const searchInput = document.getElementById('search-material');
    const resultsContainer = document.getElementById('results-container');
    const selectedBody = document.getElementById('selected-body');
    const totalElem = document.getElementById('total-general');
    const itemCountElem = document.getElementById('item-count');
    const btnGuardar = document.getElementById('btn-guardar');
    const bitacoraId = document.getElementById('bitacora_id').value;
    
    // Referencias Multi-Brigada
    const brigadaSelector = document.getElementById('brigada-selector');
    const brigadaDisplay = document.getElementById('brigada-display');

    let seleccionados = [];

    // Actualizar visualmente la brigada seleccionada en el footer del carrito
    if(brigadaSelector) {
        brigadaDisplay.innerText = brigadaSelector.value || 'Ninguna';
        brigadaSelector.addEventListener('change', () => {
            brigadaDisplay.innerText = brigadaSelector.value;
        });
    }

    // --- BUSCADOR ---
    searchInput.addEventListener('input', async (e) => {
        const q = e.target.value.trim();
        if (q.length < 3) { 
            resultsContainer.innerHTML = `<div class="flex flex-col items-center justify-center py-10 text-slate-400"><i class="fa-regular fa-keyboard text-3xl mb-2 opacity-50"></i><span class="text-sm">...</span></div>`; 
            return; 
        }
        try {
            const res = await fetch(`/api/search-materials?q=${q}`);
            const data = await res.json();
            renderResultados(data);
        } catch (err) { console.error(err); }
    });

    function renderResultados(data) {
        if (data.length === 0) {
            resultsContainer.innerHTML = `<div class="text-center py-4 text-slate-400 text-sm">No encontrado</div>`;
            return;
        }
        let html = '';
        data.forEach(m => {
            const costo = m.costo ? parseFloat(m.costo) : 0;
            const mData = encodeURIComponent(JSON.stringify(m));
            html += `
                <div class="bg-white p-3 rounded-xl border border-slate-100 shadow-sm mb-2 hover:border-blue-400 hover:shadow-md transition-all cursor-pointer group flex justify-between items-center"
                     onclick="agregar('${mData}')">
                    <div class="overflow-hidden mr-2">
                        <div class="flex items-center gap-2 mb-1">
                             <span class="text-[10px] font-bold text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded border border-blue-100">${m.codigo || 'S/C'}</span>
                             <span class="text-[10px] text-slate-400">${m.categoria || ''}</span>
                        </div>
                        <h4 class="text-sm font-medium text-slate-700 leading-tight truncate group-hover:text-blue-700" title="${m.descripcion}">${m.descripcion}</h4>
                    </div>
                    <div class="text-right whitespace-nowrap">
                        <div class="text-sm font-bold text-slate-800">$${costo.toFixed(2)}</div>
                        <i class="fa-solid fa-circle-plus text-blue-200 group-hover:text-blue-600 text-xl transition-colors mt-1"></i>
                    </div>
                </div>
            `;
        });
        resultsContainer.innerHTML = html;
    }

    // --- AGREGAR ---
    window.agregar = (encodedJson) => {
        const m = JSON.parse(decodeURIComponent(encodedJson));
        const codigo = m.codigo || m.cod_sap;
        const idx = seleccionados.findIndex(s => s.codigo === codigo);
        
        if (idx >= 0) {
            seleccionados[idx].cantidad++;
        } else {
            seleccionados.push({
                codigo: codigo,
                descripcion: m.descripcion,
                categoria: m.categoria || '',
                subcategoria: m.subcategoria || '',
                moneda: m.moneda || 'D',
                costo_unitario: m.costo ? parseFloat(m.costo) : 0,
                cantidad: 1
            });
        }
        renderTabla();
    };

    // --- TABLA ---
    function renderTabla() {
        if (seleccionados.length === 0) {
            selectedBody.innerHTML = `<tr><td colspan="5" class="p-12 text-center text-slate-400 text-sm">Carrito vacío.</td></tr>`;
            totalElem.innerText = '$ 0.00';
            itemCountElem.innerText = '0';
            return;
        }
        let html = '';
        let total = 0;
        let items = 0;

        seleccionados.forEach((s, i) => {
            const subtotal = s.cantidad * s.costo_unitario;
            total += subtotal;
            items += s.cantidad;
            html += `
                <tr class="group hover:bg-slate-50 border-b border-slate-50">
                    <td class="p-3">
                        <div class="font-bold text-slate-700 text-xs">${s.codigo}</div>
                        <div class="text-xs text-slate-500 truncate max-w-[180px]" title="${s.descripcion}">${s.descripcion}</div>
                    </td>
                    <td class="p-3 text-right text-xs text-slate-600">$${s.costo_unitario.toFixed(2)}</td>
                    <td class="p-3">
                        <div class="flex items-center justify-center bg-white border border-slate-200 rounded-lg h-8 w-20 mx-auto">
                            <button onclick="editCant(${i}, -1)" class="w-6 h-full text-slate-400 hover:text-blue-600">-</button>
                            <input type="text" readonly value="${s.cantidad}" class="w-8 text-center text-xs font-bold text-slate-700">
                            <button onclick="editCant(${i}, 1)" class="w-6 h-full text-slate-400 hover:text-blue-600">+</button>
                        </div>
                    </td>
                    <td class="p-3 text-right font-bold text-slate-800 text-xs">$${subtotal.toFixed(2)}</td>
                    <td class="p-3 text-center">
                        <button onclick="del(${i})" class="text-slate-300 hover:text-red-500"><i class="fa-solid fa-times"></i></button>
                    </td>
                </tr>
            `;
        });
        selectedBody.innerHTML = html;
        totalElem.innerText = `$ ${total.toFixed(2)}`;
        itemCountElem.innerText = items;
    }

    window.editCant = (i, d) => {
        if (seleccionados[i].cantidad + d > 0) { seleccionados[i].cantidad += d; renderTabla(); }
    };
    window.del = (i) => { seleccionados.splice(i, 1); renderTabla(); };

    // --- GUARDAR ---
    btnGuardar.addEventListener('click', async () => {
        if(seleccionados.length === 0) return alert('No hay materiales seleccionados.');
        
        // VALIDACIÓN: ¿Hay brigada seleccionada?
        const brigadaVal = brigadaSelector ? brigadaSelector.value : null;
        if (!brigadaVal) return alert('⚠️ ATENCIÓN: Debes seleccionar la BRIGADA responsable antes de guardar.');

        if (!confirm(`¿Registrar estos materiales a la brigada ${brigadaVal}?`)) return;

        const original = btnGuardar.innerHTML;
        btnGuardar.disabled = true;
        btnGuardar.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Guardando...';
        
        try {
            const res = await fetch('/api/guardar-acumulados', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                // ENVIAMOS LA BRIGADA SELECCIONADA
                body: JSON.stringify({ 
                    bitacora_id: bitacoraId, 
                    brigada_seleccionada: brigadaVal,
                    materiales: seleccionados 
                })
            });
            
            if(res.ok) {
                btnGuardar.classList.replace('bg-blue-600', 'bg-green-500');
                btnGuardar.innerHTML = '<i class="fa-solid fa-check"></i> Registro Exitoso';
                setTimeout(() => {
                    seleccionados = []; // Limpiar carrito para poder agregar a OTRA brigada si se desea
                    renderTabla();
                    btnGuardar.classList.replace('bg-green-500', 'bg-blue-600');
                    btnGuardar.disabled = false;
                    btnGuardar.innerHTML = original;
                }, 1500);
            } else {
                const err = await res.json();
                alert('Error: ' + err.error);
                btnGuardar.disabled = false;
                btnGuardar.innerHTML = original;
            }
        } catch(e) {
            console.error(e);
            alert('Error de conexión');
            btnGuardar.disabled = false;
            btnGuardar.innerHTML = original;
        }
    });
});