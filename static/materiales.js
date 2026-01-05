(function () {
  const wrapper = document.querySelector(".page-wrapper");
  if (!wrapper) return;

  const bitacoraId = Number(wrapper.dataset.bitacoraId);
  const origenSelect = document.getElementById("origen");
  const selectBrigada = document.getElementById("select-brigada"); // Select de Brigada
  const searchInput = document.getElementById("search-material");
  const resultsBox = document.getElementById("search-results");

  // UI Elements
  const selectionCard = document.getElementById("selection-card");
  const emptyState = document.getElementById("empty-state");
  const selDesc = document.getElementById("sel-desc");
  const selCode = document.getElementById("sel-code");
  const selUnit = document.getElementById("sel-unit");
  const btnCancelSel = document.getElementById("btn-cancel-sel");

  const cantidadInput = document.getElementById("cantidad");
  const btnMinus = document.getElementById("btn-minus");
  const btnPlus = document.getElementById("btn-plus");
  const btnGuardar = document.getElementById("btn-guardar");

  // Lists
  const materialesListMobile = document.getElementById("materiales-list");
  const tablaBodyDesktop = document.getElementById("materiales-body");

  let selectedMaterial = null;

  // --- LÓGICA DE DECIMALES ---
  const DECIMAL_UNITS = ["M", "MTS", "MT", "MTR", "KM", "KG", "LITRO", "M3"];

  function configureQuantityInput(unitRaw) {
    const unit = (unitRaw || "").toUpperCase().trim();
    // Permitir decimales si es M, KG, etc. o contiene "METRO"
    const isDecimal = DECIMAL_UNITS.includes(unit) || unit.includes("METRO");

    if (isDecimal) {
      cantidadInput.step = "0.1";
      cantidadInput.value = "1.0";
    } else {
      cantidadInput.step = "1";
      cantidadInput.value = "1";
    }
  }

  // --- SELECCIÓN ---
  function selectItem(item) {
    selectedMaterial = item;
    selDesc.textContent = item.descripcion;
    selCode.textContent = item.codigo;
    selUnit.textContent = item.unidad || "-";

    configureQuantityInput(item.unidad);

    emptyState.classList.add("d-none");
    selectionCard.classList.remove("d-none");
    resultsBox.classList.add("d-none");
    searchInput.value = "";
    setTimeout(() => cantidadInput.focus(), 100);
  }

  function cancelSelection() {
    selectedMaterial = null;
    selectionCard.classList.add("d-none");
    emptyState.classList.remove("d-none");
    cantidadInput.value = "1";
  }
  btnCancelSel.addEventListener("click", cancelSelection);

  // --- CONTROLES + / - ---
  btnPlus.addEventListener("click", () => {
    let val = parseFloat(cantidadInput.value) || 0;
    const isDecimal = cantidadInput.step === "0.1";
    const step = isDecimal ? 0.1 : 1;
    let newVal = val + step;
    if(isDecimal) newVal = parseFloat(newVal.toFixed(2));
    cantidadInput.value = newVal;
  });

  btnMinus.addEventListener("click", () => {
    let val = parseFloat(cantidadInput.value) || 0;
    const isDecimal = cantidadInput.step === "0.1";
    const step = isDecimal ? 0.1 : 1;
    if (val > step) {
        let newVal = val - step;
        if(isDecimal) newVal = parseFloat(newVal.toFixed(2));
        cantidadInput.value = newVal;
    }
  });

  cantidadInput.addEventListener("change", () => {
     let val = parseFloat(cantidadInput.value);
     if (val < 0) cantidadInput.value = 1;
     if (cantidadInput.step === "1") cantidadInput.value = Math.floor(val) || 1;
  });

  // --- BUSCADOR ---
  async function buscarMateriales(term) {
    const origen = origenSelect.value;
    if (term.length < 3) {
      resultsBox.classList.add("d-none");
      return;
    }
    try {
      const resp = await axios.get("/api/materiales/buscar", { params: { origen, q: term } });
      const items = resp.data.items || [];
      resultsBox.innerHTML = "";

      if (!items.length) {
        resultsBox.innerHTML = `<div class="list-group-item text-muted small">No se encontraron resultados.</div>`;
        resultsBox.classList.remove("d-none");
        return;
      }

      items.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "list-group-item list-group-item-action py-2";
        const badgeColor = item.unidad ? 'bg-light text-dark border' : 'd-none';
        btn.innerHTML = `
          <div class="d-flex justify-content-between align-items-center">
            <div class="text-truncate me-2">
              <div class="fw-bold small text-dark">${item.codigo}</div>
              <div class="small text-secondary text-truncate">${item.descripcion}</div>
            </div>
            <span class="badge ${badgeColor} ms-auto">${item.unidad || ""}</span>
          </div>
        `;
        btn.addEventListener("click", () => selectItem(item));
        resultsBox.appendChild(btn);
      });
      resultsBox.classList.remove("d-none");
    } catch (err) { console.error(err); }
  }

  function debounce(fn, d) {
    let t; return function(...a){ clearTimeout(t); t = setTimeout(()=>fn.apply(this,a),d); };
  }
  const debouncedSearch = debounce((term) => buscarMateriales(term), 300);

  searchInput.addEventListener("input", (e) => {
      if(selectedMaterial && !selectionCard.classList.contains("d-none")) cancelSelection();
      debouncedSearch(e.target.value.trim());
  });

  document.addEventListener("click", (e) => {
    if (!resultsBox.contains(e.target) && e.target !== searchInput) resultsBox.classList.add("d-none");
  });

  // --- GUARDAR ---
  btnGuardar.addEventListener("click", async () => {
    if (!selectedMaterial) return;

    // Validar Brigada
    if (!selectBrigada.value || selectBrigada.value === "Sin Asignar") {
        alert("Por favor selecciona una Brigada Responsable antes de guardar.");
        selectBrigada.focus();
        return;
    }

    const originalText = btnGuardar.innerHTML;
    btnGuardar.disabled = true;
    btnGuardar.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Guardando...`;

    try {
      const payload = {
        bitacora_id: bitacoraId,
        origen: origenSelect.value,
        brigada: selectBrigada.value, // Envío de Brigada
        codigo: selectedMaterial.codigo,
        descripcion: selectedMaterial.descripcion,
        unidad: selectedMaterial.unidad,
        cantidad: Number(cantidadInput.value)
      };

      const resp = await axios.post("/api/materiales/guardar", payload);
      if (resp.data.ok) {
        cancelSelection();
        await cargarMateriales();
      } else {
        alert("Error al guardar: " + resp.data.error);
      }
    } catch (err) {
      alert("Error de conexión");
    } finally {
      btnGuardar.disabled = false;
      btnGuardar.innerHTML = originalText;
    }
  });

  // --- BORRAR ---
  window.borrarMaterial = async (id) => {
      if(!confirm("¿Estás seguro de eliminar este material?")) return;
      try {
          const resp = await axios.delete(`/api/materiales/borrar/${id}`);
          if(resp.data.ok) {
              await cargarMateriales();
          } else {
              alert("Error al borrar");
          }
      } catch(err) {
          console.error(err);
          alert("Error de conexión");
      }
  };

  // --- LISTAR ---
  async function cargarMateriales() {
    try {
      const resp = await axios.get(`/api/materiales/listar/${bitacoraId}`);
      const items = resp.data.items || [];

      materialesListMobile.innerHTML = "";
      tablaBodyDesktop.innerHTML = "";

      if (items.length === 0) {
        materialesListMobile.innerHTML = `<div class="text-center small text-muted py-3">Sin materiales registrados.</div>`;
        return;
      }

      items.forEach(item => {
        // MÓVIL (Cards)
        const cardDiv = document.createElement("div");
        cardDiv.className = "card card-body p-2 border shadow-sm d-flex flex-row align-items-center animate__animated animate__fadeInUp";
        const iconClass = item.origen.toLowerCase() === 'claro' ? 'bi-circle-fill text-primary' : 'bi-circle-fill text-danger';

        cardDiv.innerHTML = `
          <div class="me-3 fs-4"><i class="${iconClass}"></i></div>
          <div class="flex-grow-1 overflow-hidden">
             <div class="fw-bold text-dark small text-truncate">${item.descripcion}</div>
             <div class="d-flex align-items-center mt-1">
                <span class="badge bg-light text-secondary border small me-2">${item.brigada || "S/A"}</span>
                <span class="small text-muted">${item.created_at ? item.created_at.split(' ')[1] : ''}</span>
             </div>
          </div>
          <div class="ms-2 text-end d-flex flex-column align-items-end">
             <div>
                 <span class="fs-5 fw-bold text-dark">${item.cantidad}</span>
                 <span class="small text-muted" style="font-size:0.6rem">${item.unidad || ""}</span>
             </div>
             <button onclick="borrarMaterial(${item.id})" class="btn btn-sm btn-outline-danger border-0 p-1 mt-1" style="line-height:1;">
                <i class="bi bi-trash"></i>
             </button>
          </div>
        `;
        materialesListMobile.appendChild(cardDiv);

        // DESKTOP (Tabla)
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><span class="font-monospace small">${item.codigo}</span></td>
          <td class="text-truncate" style="max-width: 250px;">${item.descripcion}</td>
          <td><span class="badge bg-light text-dark border">${item.brigada || "-"}</span></td>
          <td class="fw-bold">${item.cantidad}</td>
          <td class="small text-muted">${item.unidad || ""}</td>
          <td class="small text-muted">${item.created_at || ""}</td>
          <td class="text-end">
            <button onclick="borrarMaterial(${item.id})" class="btn btn-sm btn-outline-danger" title="Borrar">
                <i class="bi bi-trash-fill"></i>
            </button>
          </td>
        `;
        tablaBodyDesktop.appendChild(tr);
      });

    } catch (err) { console.error(err); }
  }

  cargarMateriales();
})();