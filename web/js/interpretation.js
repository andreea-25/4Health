// Randarea interpretării AI a unei rețete (folosită de pacient și de medic).
const MEAL_LABELS = {
  inainte_de_masa: "înainte de masă", dupa_masa: "după masă", in_timpul_mesei: "în timpul mesei",
  pe_stomacul_gol: "pe stomacul gol", nespecificat: "",
};

function dosageSummary(d) {
  if (!d) return "";
  const parts = [];
  if (d.dose_amount) parts.push(`${d.dose_amount} ${d.dose_unit || ""}`.trim());
  if (d.times_per_day) parts.push(`de ${d.times_per_day} ori/zi`);
  if (d.interval_hours) parts.push(`la ${d.interval_hours} ore`);
  if (MEAL_LABELS[d.meal_relation]) parts.push(MEAL_LABELS[d.meal_relation]);
  if (d.duration_days) parts.push(`${d.duration_days} zile`);
  if (d.as_needed) parts.push("la nevoie");
  return parts.join(" · ");
}

function renderInterpretation(out) {
  const meds = out.medications.map(m => `
    <div class="med-card">
      <h4>${esc(m.name)} ${m.doctor_instructions.concentration_form ? `<span class="muted">${esc(m.doctor_instructions.concentration_form)}</span>` : ""}</h4>
      ${m.allergy_alert ? `<div class="alert">${esc(m.allergy_alert)}</div>` : ""}
      <p><strong>Pentru ce este:</strong> ${esc(m.what_it_is_for)}</p>
      <p><strong>Cum îl iei:</strong> ${esc(m.how_to_take)}</p>
      ${m.schedule.length ? `<div class="schedule" title="Orar orientativ - îl poți ajusta după programul tău">${m.schedule.map(h => `<span>🕒 ${h}</span>`).join("")}</div>` : ""}
      ${dosageSummary(m.dosage) ? `<div class="muted">Înțeles de sistem: ${esc(dosageSummary(m.dosage))} <span class="pill">${m.dosage_method === "llm" ? "extras de AI" : "extras cu reguli"}</span></div>` : ""}
      ${m.warnings.length ? `<ul>${m.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <div class="original"><strong>Indicația medicului (original):</strong> ${esc(m.doctor_instructions.administration || "-")}
        ${m.doctor_instructions.duration ? " · durată: " + esc(m.doctor_instructions.duration) : ""}</div>
    </div>`).join("");

  return `
    <div class="ai-box">
      <h3>✨ Explicația asistentului Iris</h3>
      <p>${esc(out.summary)}</p>
      ${meds}
      ${out.questions_for_doctor.length ? `<p><strong>Întrebări pe care le poți pune medicului:</strong></p>
        <ul>${out.questions_for_doctor.map(q => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
      ${out.grounding && out.grounding.unmatched_drugs.length ? `<p class="muted">ℹ️ ${esc(out.grounding.note)} (${out.grounding.unmatched_drugs.map(esc).join(", ")})</p>` : ""}
      ${out.sources.length ? `<div class="sources">Surse: ${[...new Set(out.sources.map(s => s.title.split(" - ")[0]))].map(esc).join(", ")} (baza de cunoștințe 4Health)</div>` : ""}
      <div class="disclaimer">${esc(out.disclaimer)} · model: ${esc(out.model)}${out.cached ? " · din cache" : ""} · ${Math.round(out.latency_ms / 100) / 10}s</div>
    </div>`;
}

async function loadInterpretation(rxId, container, refresh = false) {
  container.innerHTML = `<div class="ai-box"><span class="spinner"></span>Iris citește rețeta... (pe un model local poate dura până la un minut)</div>`;
  try {
    const out = await api(`/api/ai/prescriptions/${rxId}/interpret${refresh ? "?refresh=true" : ""}`, { method: "POST" });
    container.innerHTML = renderInterpretation(out);
  } catch (e) {
    container.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}
