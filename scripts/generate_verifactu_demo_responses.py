#!/usr/bin/env python3
"""Generate demo Verifactu data from scratch in a fresh database.

This script is designed to run right after installing l10n_es_verifactu_oca
on a demo database with no prior verifactu data.  It will:

  1. Configure the main company for VERI*FACTU (developer, chaining, etc.)
  2. Set up fiscal positions with verifactu registration keys
  3. Create demo partners and invoices (out_invoice + out_refund)
  4. Post the invoices  (this generates verifactu.invoice.entry records)
  5. Simulate AEAT responses: Correcto, Incorrecto, AceptadoConErrores,
     Duplicado and cancel variants

Usage:
    odoo shell -d <database> < generate_verifactu_demo_responses.py
"""

import json
import random
from datetime import datetime, timedelta

from odoo import Command

# ---------------------------------------------------------------------------
# AEAT error catalogue  (source: VERIFACTU - Listado de códigos de error.pdf)
# ---------------------------------------------------------------------------

# 4xxx — Reject the ENTIRE submission
REJECTION_ERRORS = {
    4102: "El XML no cumple el esquema. Falta informar campo obligatorio.",
    4103: "Se ha producido un error inesperado al parsear el XML.",
    4104: (
        "Error en la cabecera: el valor del campo NIF del bloque "
        "ObligadoEmision no está identificado."
    ),
    4106: "El formato de fecha es incorrecto.",
    4107: "El NIF no está identificado en el censo de la AEAT.",
    4109: "El formato del NIF es incorrecto.",
    4112: (
        "El titular del certificado debe ser Obligado Emisión, "
        "Colaborador Social, Apoderado o Sucesor."
    ),
    4115: "El valor del campo NIF del bloque ObligadoEmision es incorrecto.",
    4116: (
        "Error en la cabecera: el campo NIF del bloque ObligadoEmision "
        "tiene un formato incorrecto."
    ),
    4119: "Error al informar caracteres cuya codificación no es UTF-8.",
    4134: "Servicio no activo.",
}

# 1xxx — Reject a SINGLE invoice record
INVOICE_ERRORS = {
    1104: "El valor del campo NumSerieFactura es incorrecto.",
    1105: "El valor del campo FechaExpedicionFactura es incorrecto.",
    1106: (
        "El valor del campo TipoFactura no está incluido en la lista "
        "de valores permitidos."
    ),
    1108: (
        "El NIF del IDEmisorFactura debe ser el mismo que el NIF "
        "del ObligadoEmision."
    ),
    1109: "El NIF no está identificado en el censo de la AEAT.",
    1112: "El campo FechaExpedicionFactura es superior a la fecha actual.",
    1114: (
        "Si la factura es de tipo rectificativa, el campo "
        "TipoRectificativa debe tener valor."
    ),
    1124: (
        "El valor del campo TipoImpositivo no está incluido en la "
        "lista de valores permitidos."
    ),
    1150: (
        "Cuando TipoFactura sea F2 el sumatorio de "
        "BaseImponibleOimporteNoSujeto y CuotaRepercutida de "
        "todas las líneas de detalle no podrá ser superior a 3.000."
    ),
    1181: "El valor del campo CalificacionOperacion es incorrecto.",
    1189: (
        "Si TipoFactura es F1 o F3 o R1 o R2 o R3 o R4 el bloque "
        "Destinatarios tiene que estar cumplimentado."
    ),
    1195: (
        "Al menos uno de los dos campos OperacionExenta o "
        "CalificacionOperacion deben estar informados."
    ),
    1196: (
        "OperacionExenta o CalificacionOperacion no pueden ser ambos "
        "informados ya que son excluyentes entre sí."
    ),
    1210: (
        "El campo ImporteTotal tiene un valor incorrecto para el valor "
        "de los campos BaseImponibleOimporteNoSujeto, CuotaRepercutida "
        "y CuotaRecargoEquivalencia suministrados."
    ),
    1216: (
        "El campo CuotaTotal tiene un valor incorrecto para el valor "
        "de los campos CuotaRepercutida y CuotaRecargoEquivalencia "
        "suministrados."
    ),
    1244: "El campo FechaHoraHusoGenRegistro tiene un formato incorrecto.",
    1246: "El valor del campo ClaveRegimen es incorrecto.",
    1247: "El valor del campo TipoHuella es incorrecto.",
    1262: "La longitud de huella no cumple con las especificaciones.",
    1286: (
        "Si el impuesto es IVA(01), IGIC(03) o vacio, si ClaveRegimen "
        "es 02 solo se podrá informar OperacionExenta."
    ),
}

# 3xxx — Record-level errors (duplicated, not found …)
RECORD_ERRORS = {
    3000: "Registro de facturación duplicado.",
    3001: "El registro de facturación ya ha sido dado de baja.",
    3002: "No existe el registro de facturación.",
    3003: (
        "El presentador no tiene los permisos necesarios para "
        "actualizar este registro de facturación."
    ),
}

# 2xxx — Accepted-with-errors (must be fixed later)
ACCEPTED_WITH_ERRORS = {
    2000: "El cálculo de la huella suministrada es incorrecta.",
    2001: (
        "El NIF del bloque Destinatarios no está identificado en el "
        "censo de la AEAT."
    ),
    2002: (
        "La longitud de huella del registro anterior no cumple con "
        "las especificaciones."
    ),
    2003: (
        "El contenido de la huella del registro anterior no cumple "
        "con las especificaciones."
    ),
    2004: (
        "El valor del campo FechaHoraHusoGenRegistro debe ser la fecha "
        "actual del sistema de la AEAT, admitiéndose un margen de error."
    ),
    2005: (
        "El campo ImporteTotal tiene un valor incorrecto para el valor "
        "de los campos BaseImponibleOimporteNoSujeto, CuotaRepercutida "
        "y CuotaRecargoEquivalencia suministrados."
    ),
    2006: (
        "El campo CuotaTotal tiene un valor incorrecto para el valor "
        "de los campos CuotaRepercutida y CuotaRecargoEquivalencia "
        "suministrados."
    ),
}

# ---------------------------------------------------------------------------
# State mappings  (mirrors verifactu_invoice_entry.py constants)
# ---------------------------------------------------------------------------

VERIFACTU_STATE_MAPPING = {
    "Correcto": "sent",
    "Incorrecto": "incorrect",
    "AceptadoConErrores": "sent_w_errors",
}
VERIFACTU_CANCEL_STATE_MAPPING = {
    "Correcto": "cancel",
    "Incorrecto": "cancel_incorrect",
    "AceptadoConErrores": "cancel_w_errors",
}

# ---------------------------------------------------------------------------
# How many demo invoices to create per type
# ---------------------------------------------------------------------------
NUM_OUT_INVOICES = 10
NUM_OUT_REFUNDS = 3
# How many already-sent invoices will additionally get a cancel entry
NUM_CANCEL = 2


# ===================================================================
#  STEP 1 — Ensure VERI*FACTU configuration on the company
# ===================================================================


def ensure_verifactu_config(env):
    """Return the main company with all verifactu prerequisites set up."""
    company = env.company
    print(f"[1/5] Configuring company '{company.name}' for VERI*FACTU …")

    # Country must be Spain
    es = env.ref("base.es")
    if company.country_id != es:
        company.country_id = es
        print("  - Set country to Spain")

    # Valid Spanish VAT
    if not company.vat:
        company.vat = "A28017895"
        print(f"  - Set demo VAT: {company.vat}")

    # Tax agency
    tax_agency = env.ref("l10n_es_aeat.aeat_tax_agency_spain", False)
    if tax_agency and company.tax_agency_id != tax_agency:
        company.tax_agency_id = tax_agency
        print("  - Set tax agency to AEAT Spain")

    # Developer
    developer = env["verifactu.developer"].search([], limit=1)
    if not developer:
        developer = env["verifactu.developer"].create(
            {
                "name": "Demo Developer S.L.",
                "vat": "B12345678",
                "sif_name": "demo_sif",
                "version": "1.0",
            }
        )
        print(f"  - Created verifactu developer: {developer.name}")
    company.verifactu_developer_id = developer

    # Chaining
    chaining = company.verifactu_chaining_id
    if not chaining:
        chaining = env["verifactu.chaining"].search([], limit=1)
    if not chaining:
        chaining = env["verifactu.chaining"].create(
            {"name": "DEMO Chaining", "sif_id": "01", "installation_number": 1}
        )
        print(f"  - Created verifactu chaining: {chaining.name}")
    company.verifactu_chaining_id = chaining

    # Enable verifactu flags
    company.verifactu_enabled = True
    company.verifactu_test = True
    if not company.verifactu_description:
        company.verifactu_description = "/"
    # The write on company auto-enables sale journals (see res_company.py)

    print("  - VERI*FACTU enabled on company")
    return company


# ===================================================================
#  STEP 2 — Ensure fiscal positions have verifactu keys
# ===================================================================


def ensure_fiscal_positions(env, company):
    """Assign verifactu_registration_key to the main fiscal positions."""
    print("[2/5] Setting verifactu registration keys on fiscal positions …")
    reg_key_01 = env.ref("l10n_es_verifactu_oca.verifactu_registration_keys_01", False)
    if not reg_key_01:
        print("  ! Registration key 01 not found — skipping")
        return
    fps = env["account.fiscal.position"].search([("company_id", "=", company.id)])
    updated = 0
    for fp in fps:
        if not fp.verifactu_registration_key:
            fp.verifactu_registration_key = reg_key_01
            updated += 1
    print(f"  - Updated {updated} fiscal positions")


# ===================================================================
#  STEP 3 — Create demo invoices
# ===================================================================

DEMO_PARTNERS = [
    {"name": "Empresa Demo Alpha S.L.", "vat": "B65410011", "is_company": True},
    {"name": "María García López", "vat": "50064081G", "is_company": False},
    {"name": "Servicios Beta S.A.", "vat": "A58818501", "is_company": True},
    {"name": "Carlos Fernández Ruiz", "vat": "17702795V", "is_company": False},
    {"name": "Innovación Gamma S.L.", "vat": "A80192727", "is_company": True},
]

DEMO_PRODUCTS = [
    "Consultoría técnica",
    "Servicio de mantenimiento",
    "Licencia software anual",
    "Formación profesional",
    "Desarrollo a medida",
]


def _get_or_create_partners(env):
    """Return a list of demo partner records."""
    partners = []
    es = env.ref("base.es")
    for data in DEMO_PARTNERS:
        partner = env["res.partner"].search([("vat", "=", data["vat"])], limit=1)
        if not partner:
            partner = env["res.partner"].create(
                {
                    "name": data["name"],
                    "vat": data["vat"],
                    "is_company": data["is_company"],
                    "country_id": es.id,
                }
            )
        partners.append(partner)
    return partners


def _get_or_create_products(env):
    """Return a list of demo product records."""
    products = []
    for name in DEMO_PRODUCTS:
        product = env["product.product"].search([("name", "=", name)], limit=1)
        if not product:
            product = env["product.product"].create({"name": name, "type": "service"})
        products.append(product)
    return products


def _get_sale_account(env, company):
    """Find a suitable income account for invoice lines."""
    account = env["account.account"].search(
        [
            ("company_id", "=", company.id),
            ("account_type", "=", "income"),
        ],
        limit=1,
    )
    if not account:
        account = env["account.account"].search(
            [
                ("company_id", "=", company.id),
                ("account_type", "=", "income_other"),
            ],
            limit=1,
        )
    return account


def _get_fiscal_position(env, company):
    """Find the 'Régimen Nacional' fiscal position or the first available."""
    fp = env["account.fiscal.position"].search(
        [
            ("company_id", "=", company.id),
            ("name", "ilike", "nacional"),
        ],
        limit=1,
    )
    if not fp:
        fp = env["account.fiscal.position"].search(
            [
                ("company_id", "=", company.id),
                ("verifactu_registration_key", "!=", False),
            ],
            limit=1,
        )
    return fp


def create_demo_invoices(env, company):
    """Create and post demo invoices; return all posted invoices."""
    print("[3/5] Creating demo invoices …")
    partners = _get_or_create_partners(env)
    products = _get_or_create_products(env)
    account = _get_sale_account(env, company)
    fp = _get_fiscal_position(env, company)

    if not account:
        print("  ! Could not find an income account — aborting")
        return env["account.move"]

    base_date = datetime.now().date() - timedelta(days=30)
    invoices = env["account.move"]

    # --- out_invoices ---
    for i in range(NUM_OUT_INVOICES):
        partner = random.choice(partners)
        product = random.choice(products)
        amount = round(random.uniform(50, 5000), 2)
        inv_date = base_date + timedelta(days=i)
        vals = {
            "company_id": company.id,
            "partner_id": partner.id,
            "move_type": "out_invoice",
            "invoice_date": inv_date.isoformat(),
            "invoice_line_ids": [
                Command.create(
                    {
                        "product_id": product.id,
                        "account_id": account.id,
                        "name": product.name,
                        "price_unit": amount,
                        "quantity": 1,
                    }
                )
            ],
        }
        if fp:
            vals["fiscal_position_id"] = fp.id
        invoices |= env["account.move"].create(vals)

    # --- out_refunds ---
    refund_origins = invoices[:NUM_OUT_REFUNDS] if invoices else env["account.move"]
    for i, origin in enumerate(refund_origins):
        partner = origin.partner_id
        product = random.choice(products)
        amount = round(random.uniform(20, 500), 2)
        inv_date = base_date + timedelta(days=NUM_OUT_INVOICES + i)
        vals = {
            "company_id": company.id,
            "partner_id": partner.id,
            "move_type": "out_refund",
            "invoice_date": inv_date.isoformat(),
            "reversed_entry_id": origin.id,
            "invoice_line_ids": [
                Command.create(
                    {
                        "product_id": product.id,
                        "account_id": account.id,
                        "name": f"Rectificativa: {product.name}",
                        "price_unit": amount,
                        "quantity": 1,
                    }
                )
            ],
        }
        if fp:
            vals["fiscal_position_id"] = fp.id
        invoices |= env["account.move"].create(vals)

    print(f"  - Created {len(invoices)} invoices")

    # Post them — this triggers _generate_verifactu_chaining automatically
    print("[4/5] Posting invoices (generates verifactu entries) …")
    posted = env["account.move"]
    for inv in invoices.sorted("name"):
        try:
            inv.action_post()
            posted |= inv
        except Exception as exc:
            print(f"  ! Could not post {inv.name or inv.id}: {exc}")
    print(f"  - Posted {len(posted)} invoices")
    return posted


# ===================================================================
#  STEP 5 — Create simulated AEAT responses
# ===================================================================


def _random_csv():
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = "0123456789"
    return "A-{}{}{}-{}".format(
        random.choice(letters),
        "".join(random.choices(digits, k=2)),
        "".join(random.choices(letters, k=2)),
        "".join(random.choices(digits, k=7)),
    )


def _base_response(company):
    nif = company.partner_id._parse_aeat_vat_info()[2]
    now = datetime.now().astimezone()
    return {
        "CSV": _random_csv(),
        "DatosPresentacion": {
            "NIFPresentador": nif,
            "TimestampPresentacion": now.isoformat(timespec="seconds"),
        },
        "Cabecera": {
            "ObligadoEmision": {
                "NombreRazon": company.name[:120],
                "NIF": nif,
            },
            "Representante": "",
            "RemisionVoluntaria": "",
            "RemisionRequerimiento": "",
        },
        "TiempoEsperaEnvio": "60",
        "EstadoEnvio": "",
        "RespuestaLinea": [],
    }


def _response_line_base(entry):
    doc = entry.document
    issuer = doc._get_verifactu_issuer()
    serial = doc._get_document_serial_number()
    date_str = doc._get_verifactu_date(doc._get_document_date())
    is_cancel = entry.entry_type == "cancel"
    return {
        "IDFactura": {
            "IDEmisorFactura": issuer,
            "NumSerieFactura": serial,
            "FechaExpedicionFactura": date_str,
        },
        "Operacion": {
            "TipoOperacion": "Anulacion" if is_cancel else "Alta",
            "Subsanacion": "",
            "RechazoPrevio": "",
            "SinRegistroPrevio": "",
        },
        "RefExterna": "",
    }


# --- response builders ---


def build_correct(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Correcto"
    for entry in entries:
        line = _response_line_base(entry)
        line["EstadoRegistro"] = "Correcto"
        resp["RespuestaLinea"].append(line)
    return resp


def build_incorrect(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Incorrecto"
    for entry in entries:
        line = _response_line_base(entry)
        code = random.choice(list(INVOICE_ERRORS.keys()))
        line["EstadoRegistro"] = "Incorrecto"
        line["CodigoErrorRegistro"] = code
        line["DescripcionErrorRegistro"] = INVOICE_ERRORS[code]
        resp["RespuestaLinea"].append(line)
    return resp


def build_accepted_with_errors(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Correcto"
    for entry in entries:
        line = _response_line_base(entry)
        code = random.choice(list(ACCEPTED_WITH_ERRORS.keys()))
        line["EstadoRegistro"] = "AceptadoConErrores"
        line["CodigoErrorRegistro"] = code
        line["DescripcionErrorRegistro"] = ACCEPTED_WITH_ERRORS[code]
        resp["RespuestaLinea"].append(line)
    return resp


def build_duplicated(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Incorrecto"
    for entry in entries:
        line = _response_line_base(entry)
        line["EstadoRegistro"] = "Incorrecto"
        line["CodigoErrorRegistro"] = "3000"
        line["DescripcionErrorRegistro"] = RECORD_ERRORS[3000]
        line["RegistroDuplicado"] = {
            "IdPeticionRegistroDuplicado": "",
            "EstadoRegistroDuplicado": "Correcta",
            "CodigoErrorRegistro": "",
            "DescripcionErrorRegistro": "",
        }
        resp["RespuestaLinea"].append(line)
    return resp


# Cancel builders


def build_cancel_correct(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Correcto"
    for entry in entries:
        line = _response_line_base(entry)
        line["EstadoRegistro"] = "Correcto"
        resp["RespuestaLinea"].append(line)
    return resp


def build_cancel_incorrect(entries, company):
    resp = _base_response(company)
    resp["EstadoEnvio"] = "Incorrecto"
    for entry in entries:
        line = _response_line_base(entry)
        code = random.choice(list(INVOICE_ERRORS.keys()))
        line["EstadoRegistro"] = "Incorrecto"
        line["CodigoErrorRegistro"] = code
        line["DescripcionErrorRegistro"] = INVOICE_ERRORS[code]
        resp["RespuestaLinea"].append(line)
    return resp


# --- scenario definitions ---

SCENARIOS_REGISTER = [
    ("Correcto", build_correct, "Envío VERI*FACTU"),
    ("Incorrecto", build_incorrect, "Facturas incorrectas en VERI*FACTU"),
    (
        "AceptadoConErrores",
        build_accepted_with_errors,
        "Facturas incorrectas en VERI*FACTU",
    ),
    ("Duplicado", build_duplicated, "Facturas incorrectas en VERI*FACTU"),
]

SCENARIOS_CANCEL = [
    ("Anulación correcta", build_cancel_correct, "Envío VERI*FACTU"),
    (
        "Anulación incorrecta",
        build_cancel_incorrect,
        "Facturas incorrectas en VERI*FACTU",
    ),
]


# --- apply one response scenario to a list of entries ---


def _get_send_state(estado, is_cancel):
    m = VERIFACTU_CANCEL_STATE_MAPPING if is_cancel else VERIFACTU_STATE_MAPPING
    return m.get(estado, "not_sent")


def apply_scenario(env, entries, scenario_name, builder, response_name):
    """Create response + response-lines for *entries* using *builder*."""
    if not entries:
        return
    company = entries[0].company_id
    verifactu_response = builder(entries, company)
    nif = company.partner_id._parse_aeat_vat_info()[2]
    header = {"ObligadoEmision": {"NombreRazon": company.name[:120], "NIF": nif}}

    response = (
        env["verifactu.invoice.entry.response"]
        .sudo()
        .create(
            {
                "header": json.dumps(header),
                "name": response_name,
                "invoice_data": json.dumps(
                    verifactu_response.get("RespuestaLinea", [])
                ),
                "response": json.dumps(verifactu_response, indent=2),
                "verifactu_csv": verifactu_response.get("CSV", "-"),
                "date_response": datetime.now(),
            }
        )
    )

    for resp_line in verifactu_response.get("RespuestaLinea", []):
        invoice_num = resp_line["IDFactura"]["NumSerieFactura"]
        estado = resp_line.get("EstadoRegistro", "Correcto")

        matching = entries.filtered(
            lambda e, num=invoice_num: e.document and e.document.name == num
        )
        if not matching:
            continue
        entry = matching[0]
        is_cancel = entry.entry_type == "cancel"
        send_state = _get_send_state(estado, is_cancel)

        # Handle duplicate special case
        if resp_line.get("CodigoErrorRegistro") in (3000, "3000"):
            dup = resp_line.get("RegistroDuplicado", {})
            dup_estado = dup.get("EstadoRegistroDuplicado", "")
            if dup_estado == "Correcta":
                dup_estado = "Correcto"
            elif dup_estado == "AceptadaConErrores":
                dup_estado = "AceptadoConErrores"
            send_state = _get_send_state(dup_estado, is_cancel)

        error_code = str(resp_line.get("CodigoErrorRegistro", ""))

        rl = (
            env["verifactu.invoice.entry.response.line"]
            .sudo()
            .create(
                {
                    "entry_id": entry.id,
                    "model": entry.model,
                    "document_id": entry.document_id,
                    "response": json.dumps(resp_line, indent=2),
                    "entry_response_id": response.id,
                    "send_state": send_state,
                    "error_code": error_code,
                }
            )
        )
        entry.last_response_line_id = rl
        doc = entry.document
        if doc:
            doc.last_verifactu_response_line_id = rl
            doc_vals = {"verifactu_return": json.dumps(resp_line, indent=2)}
            if send_state in ("sent", "cancel"):
                doc_vals["verifactu_csv"] = verifactu_response["CSV"]
                doc_vals["aeat_send_failed"] = False
            elif send_state in ("sent_w_errors", "cancel_w_errors"):
                doc_vals["verifactu_csv"] = verifactu_response["CSV"]
                doc_vals["aeat_send_failed"] = True
            else:
                doc_vals["aeat_send_failed"] = True
            if error_code:
                desc = resp_line.get("DescripcionErrorRegistro", "")
                doc_vals["aeat_send_error"] = f"{error_code} | {desc}"
            doc.write(doc_vals)

    print(
        f"  -> '{scenario_name}' — {len(entries)} entries "
        f"(response id={response.id})"
    )
    return response


# ===================================================================
#  STEP 5b — Generate cancel entries for some already-sent invoices
# ===================================================================


def create_cancel_entries(env, sent_invoices, count):
    """Pick *count* already-sent invoices, create cancel verifactu entries."""
    candidates = sent_invoices.filtered(
        lambda inv: inv.aeat_state == "sent" and inv.move_type == "out_invoice"
    )
    to_cancel = candidates[: min(count, len(candidates))]
    cancel_entries = env["verifactu.invoice.entry"]
    for inv in to_cancel:
        try:
            # Cancel the invoice in Odoo first
            inv.with_context(verifactu_cancel=True).button_cancel()
            inv.verifactu_registration_date = datetime.now()
            inv._generate_verifactu_chaining(entry_type="cancel")
            cancel_entries |= inv.last_verifactu_invoice_entry_id
        except Exception as exc:
            print(f"  ! Could not create cancel entry for {inv.name}: {exc}")
    return cancel_entries


# ===================================================================
#  Main
# ===================================================================


def main(env):
    print("=" * 60)
    print(" VERI*FACTU demo data generator")
    print("=" * 60)

    # 1 — Company config
    company = ensure_verifactu_config(env)

    # 2 — Fiscal positions
    ensure_fiscal_positions(env, company)

    # 3 & 4 — Create + post invoices
    posted = create_demo_invoices(env, company)
    if not posted:
        print("\nNo invoices could be posted.  Aborting.")
        return

    # Collect all pending register entries
    Entry = env["verifactu.invoice.entry"]
    register_entries = Entry.search(
        [("send_state", "=", "not_sent"), ("entry_type", "!=", "cancel")],
        order="id asc",
    )

    if not register_entries:
        print("\nNo pending verifactu entries found after posting.  Aborting.")
        return

    # 5 — Distribute register entries across scenarios
    print(
        f"[5/5] Creating simulated AEAT responses for "
        f"{len(register_entries)} entries …"
    )
    ids = list(register_entries.ids)
    random.shuffle(ids)
    total = len(ids)

    # 40 % correct, 25 % incorrect, 20 % accepted-with-errors, 15 % duplicated
    n_correct = max(1, int(total * 0.40))
    n_incorrect = max(1, int(total * 0.25))
    n_accepted = max(1, int(total * 0.20))
    # rest goes to duplicated

    slices = [
        ids[:n_correct],
        ids[n_correct : n_correct + n_incorrect],
        ids[n_correct + n_incorrect : n_correct + n_incorrect + n_accepted],
        ids[n_correct + n_incorrect + n_accepted :],
    ]

    for (sc_name, builder, resp_name), id_list in zip(SCENARIOS_REGISTER, slices):  # noqa: B905
        if not id_list:
            continue
        apply_scenario(env, Entry.browse(id_list), sc_name, builder, resp_name)

    # 5b — Cancel entries for some already-sent invoices
    sent_invoices = posted.filtered(lambda i: i.aeat_state == "sent")
    if sent_invoices and NUM_CANCEL > 0:
        print(f"\n  Creating {NUM_CANCEL} cancel entries …")
        cancel_entries = create_cancel_entries(env, sent_invoices, NUM_CANCEL)
        if cancel_entries:
            # Half correct, half incorrect
            mid = max(1, len(cancel_entries) // 2)
            cancel_ids = cancel_entries.ids
            for (sc_name, builder, resp_name), id_list in zip(  # noqa: B905
                SCENARIOS_CANCEL,
                [cancel_ids[:mid], cancel_ids[mid:]],
            ):
                if id_list:
                    apply_scenario(
                        env, Entry.browse(id_list), sc_name, builder, resp_name
                    )
    env.cr.execute(
        "UPDATE account_move SET pms_property_id=1 "
        "WHERE move_type='out_invoice' AND pms_property_id IS NULL"
    )

    env.cr.commit()

    # Summary
    print("\n" + "=" * 60)
    total_responses = env["verifactu.invoice.entry.response"].search_count([])
    total_lines = env["verifactu.invoice.entry.response.line"].search_count([])
    print(
        f" Done!  {total_responses} response(s), "
        f"{total_lines} response line(s) created."
    )
    print("=" * 60)


# Auto-run when loaded in Odoo shell
try:
    main(env)  # noqa: F821 — `env` is available in Odoo shell context
except NameError:
    print(
        "This script must be run inside the Odoo shell:\n"
        "  odoo shell -d <database> < generate_verifactu_demo_responses.py"
    )
