// Worker leads-intake (Cloudflare). Recibe el formulario de tecnocr.net y
// lo entrega a Frappe con cortec_helpdesk.api.web_lead_intake, que crea el
// CRM Lead y registra los consentimientos en una sola transacción.
//
// El formulario debe enviar:
//   privacidad  — true si marcó la Política de Privacidad (obligatorio)
//   promociones — true si aceptó recibir información promocional
//   page_url    — location.href (si falta se usa el Referer)
// Durante la transición se acepta el campo antiguo `consentimiento` como
// equivalente de `privacidad`.

const FRAPPE_URL = "https://soporte.tecnocr.net";

export default {
  async fetch(req, env) {
    const ORIGIN = "https://tecnocr.net";
    const cors = {
      "Access-Control-Allow-Origin": ORIGIN,
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Content-Type": "application/json"
    };
    const reply = (status, data) =>
      new Response(JSON.stringify(data), { status, headers: cors });

    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });

    const ip = req.headers.get("CF-Connecting-IP") || "sin-ip";
    const { success } = await env.LEAD_LIMITER.limit({ key: ip });
    if (!success) return reply(429, { ok: false, error: "rate_limited" });

    let body;
    try { body = await req.json(); }
    catch { return reply(400, { ok: false }); }

    const ts = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        secret: env.TURNSTILE_SECRET,
        response: body.token,
        remoteip: ip
      })
    }).then(r => r.json());
    if (!ts.success) {
      console.log("turnstile fail", JSON.stringify(ts["error-codes"]));
      return reply(403, { ok: false });
    }

    if (!body.nombre || !body.email) return reply(400, { ok: false });

    // Igual que el acuerdo obligatorio de los formularios de Bitrix24:
    // sin la Política de Privacidad aceptada no se recibe el envío.
    const privacidad = Boolean(body.privacidad || body.consentimiento);
    if (!privacidad) return reply(400, { ok: false, error: "privacy_required" });

    const lead = {
      first_name: String(body.nombre).slice(0, 100),
      last_name: String(body.apellido || "").slice(0, 100),
      email: String(body.email).slice(0, 140),
      mobile_no: String(body.telefono || "").replace(/\D/g, "").slice(0, 30),
      organization: String(body.empresa || "").slice(0, 140),
      custom_comentarios: String(body.comentarios || "").slice(0, 2000),
      source: "Shopify Web Form",
      status: "New"
    };

    const consents = [
      { agreement: "politica-privacidad", accepted: true },
      { agreement: "promociones", accepted: Boolean(body.promociones) }
    ];

    const r = await fetch(`${FRAPPE_URL}/api/method/cortec_helpdesk.api.web_lead_intake`, {
      method: "POST",
      headers: {
        "Authorization": `token ${env.FRAPPE_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        lead,
        consents,
        ip: ip === "sin-ip" ? null : ip,
        page_url: String(body.page_url || req.headers.get("Referer") || "").slice(0, 1000),
        user_agent: String(req.headers.get("User-Agent") || "").slice(0, 500),
        // Frappe lo usa para no duplicar el Lead si este fetch se reintenta.
        submission_id: crypto.randomUUID()
      })
    });

    if (!r.ok) console.log("frappe error", r.status, await r.text());
    return reply(r.ok ? 200 : 502, { ok: r.ok });
  }
};
