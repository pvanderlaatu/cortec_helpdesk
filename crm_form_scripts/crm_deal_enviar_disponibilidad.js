// CRM Form Script — pegar en Desk: /app/crm-form-script/new
//   Name:     CORTEC Enviar disponibilidad WhatsApp
//   DocType:  CRM Deal
//   View:     Form
//   Enabled:  ✓
// No editar los CRM Form Scripts estándar: este es un registro aparte.
// Backend: cortec_helpdesk/api/whatsapp_disponibilidad.py
//
// Helpers de los Form Scripts de Frappe CRM usados aquí: call(), $dialog(),
// createToast(). this.doc es el Deal abierto.

class CRMDeal {
  onLoad() {
    this.actions = [
      {
        label: "Enviar disponibilidad (WhatsApp)",
        onClick: () => this.enviarDisponibilidad(),
      },
    ]
  }

  async enviarDisponibilidad() {
    const metodo = "cortec_helpdesk.api.whatsapp_disponibilidad"
    const deal = this.doc.name

    const error = (e) =>
      createToast({
        title: "No se pudo enviar",
        // call() rechaza con el error de Frappe: el texto de frappe.throw
        // viene en e.messages; si no, en e.message.
        text: (e && e.messages && e.messages[0]) || (e && e.message) || String(e),
        icon: "x",
        iconClasses: "text-ink-red-4",
      })

    let datos
    try {
      datos = await call(`${metodo}.vista_previa`, { deal })
    } catch (e) {
      error(e)
      return
    }

    let enviando = false
    $dialog({
      title: "Enviar disponibilidad por WhatsApp",
      message:
        `Destinatario: ${datos.nombre} (${datos.telefono})\n\n` +
        `Artículos: ${datos.articulos}\n\n` +
        `Total: ${datos.total} IVA incluido`,
      actions: [
        {
          label: "Enviar",
          variant: "solid",
          onClick: async ({ close }) => {
            if (enviando) return // evita doble envío por doble clic
            enviando = true
            try {
              await call(`${metodo}.enviar`, { deal })
              createToast({
                title: "Disponibilidad enviada",
                text: `Plantilla enviada a ${datos.nombre} (${datos.telefono})`,
                icon: "check",
                iconClasses: "text-ink-green-3",
              })
              close()
            } catch (e) {
              enviando = false
              error(e)
            }
          },
        },
        {
          label: "Cancelar",
          onClick: ({ close }) => close(),
        },
      ],
    })
  }
}
