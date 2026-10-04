#!/usr/bin/env python3
"""De la respuesta `v1` de la API de auditoría de facturas de MuninCloud a los CSV del Data Import de ERPNext.

    python3 munincloud_erpnext.py --mi-nif B00000018 --compania "Mi Empresa" \\
        --cuenta-acreedores "Acreedores - ME" --cuenta-gasto "Gastos - ME" \\
        --cuenta-deudores "Deudores - ME" --cuenta-ingreso "Ventas - ME" --centro-coste "Principal - ME" \\
        --salida salida/ trabajos/

Escribe en --salida (solo los que tengan alguna factura, salvo revisar.csv y omitidos.csv, que van siempre):
  erpnext_purchase_invoice.csv   las facturas RECIBIDAS, para Data Import > Purchase Invoice > Insert New Records:
                                 una fila por línea, la cabecera de la factura solo en la primera (en las demás,
                                 todas las columnas de cabecera vacías: una celda de cabecera en una fila hija PARTE
                                 la factura en dos)
  erpnext_sales_invoice.csv      las EMITIDAS, igual, para Sales Invoice
  revisar.csv                    qué factura se apartó (nivel REVISAR: no está en ningún CSV de ERPNext) o lleva un
                                 aviso (nivel AVISO: está en su CSV), y por qué
  omitidos.csv                   lo que no era una factura terminada

Solo genera el fichero: no se conecta a ERPNext ni toca tu sitio. ERPNext® y Frappe® son marcas de Frappe
Technologies Pvt. Ltd.; este conector no está asociado a Frappe Technologies ni certificado por ella. Detalle de
columnas, reglas y límites: README.md. Solo biblioteca estándar de Python (3.8 o posterior).
"""
import argparse
import csv
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import munincloud_v1 as v1  # noqa: E402

VERSION = "1.0.0"

# Los nombres de campo de ERPNext 16.37 (purchase_invoice.json, sales_invoice.json y sus tablas de líneas). Se
# emiten por su nombre de campo; las de la tabla de líneas con el prefijo `items.` (Frappe, importer.py).
# NO se emiten: `amount` (solo lectura: lo calcula ERPNext como qty x rate) ni el NIF (`tax_id`, solo lectura, sale
# del tercero). El IVA va en la tabla `taxes` (prefijo `taxes.`): UNA fila `Actual` con la cuota, en la misma fila
# del CSV que la primera línea. Medido importando en ERPNext 16.37 (2026-10-04): `items.item_tax_template` sola deja
# el IVA a 0 (ERPNext lo calcula desde `item_tax_rate`, que solo rellena al elegir un artículo), y con dos tipos o con
# suplidos ERPNext reparte el `Actual` entre todas las líneas y rechaza la factura («Item Wise Tax Details do not
# match with Taxes and Charges»): esas van a revisar.
CAB_COMPRA = ["supplier", "bill_no", "bill_date", "posting_date", "set_posting_time", "company", "currency",
              "credit_to", "is_return"]
LIN_COMPRA = ["items.item_name", "items.description", "items.qty", "items.uom", "items.rate",
              "items.expense_account", "items.item_tax_template"]
CAB_VENTA = ["customer", "remarks", "posting_date", "set_posting_time", "company", "currency", "debit_to", "is_return"]
LIN_VENTA = ["items.item_name", "items.description", "items.qty", "items.uom", "items.rate",
             "items.income_account", "items.cost_center", "items.item_tax_template"]
TAX_COMPRA = ["taxes.charge_type", "taxes.account_head", "taxes.description", "taxes.tax_amount", "taxes.category",
              "taxes.add_deduct_tax"]
TAX_VENTA = ["taxes.charge_type", "taxes.account_head", "taxes.description", "taxes.tax_amount"]
FICHERO = {v1.RECIBIDA: "erpnext_purchase_invoice.csv", v1.EMITIDA: "erpnext_sales_invoice.csv"}
COLUMNAS = {v1.RECIBIDA: (CAB_COMPRA, LIN_COMPRA + TAX_COMPRA), v1.EMITIDA: (CAB_VENTA, LIN_VENTA + TAX_VENTA)}
# Lo que ERPNext exige y v1 no trae: el parámetro que lo da, por dirección (sin él, esas facturas a revisar).
NECESARIOS = {v1.RECIBIDA: (("compania", "--compania"), ("cuenta_acreedores", "--cuenta-acreedores"),
                            ("cuenta_gasto", "--cuenta-gasto")),
              v1.EMITIDA: (("compania", "--compania"), ("cuenta_deudores", "--cuenta-deudores"),
                           ("cuenta_ingreso", "--cuenta-ingreso"), ("centro_coste", "--centro-coste"))}
USOS = {"compra": v1.RECIBIDA, "compras": v1.RECIBIDA, "recibida": v1.RECIBIDA,
        "venta": v1.EMITIDA, "ventas": v1.EMITIDA, "emitida": v1.EMITIDA}
NO_SUJETO = "NS"                     # la clave del fichero de plantillas para suplidos / no sujeto
UOM_POR_OMISION = "Nos"              # la unidad que trae ERPNext al instalarse; v1 no trae unidad
LARGO_ITEM_NAME = 140                # `item_name` es un Data de 140 caracteres; el concepto entero va en description
AVISO, REVISAR = "AVISO", "REVISAR"


# ─── lo que da el cliente ─────────────────────────────────────────────────────

def leer_plantillas(ruta, que="plantilla"):
    """{(RECIBIDA|EMITIDA, '21'|'10'|…|'NS'): nombre del Item Tax Template}, del fichero «uso;tipo;plantilla».
    Con que="cuenta" lee igual el fichero «uso;tipo;cuenta» de --cuentas-iva (ver leer_cuentas_iva).

    `uso` es compra o venta; `tipo`, el porcentaje de IVA (21, 10, 4, 0…) o NS para suplidos y no sujeto; la
    plantilla, el NOMBRE de la Item Tax Template de TU ERPNext tal cual (lleva la abreviatura de tu compañía, p. ej.
    «IVA 21% - ME»). Sin este fichero la columna `items.item_tax_template` va vacía.
    """
    tabla = {}
    if not ruta:
        return tabla
    primera = True
    with open(ruta, encoding="utf-8-sig") as fh:
        for n, linea in enumerate(fh, 1):
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            era_primera, primera = primera, False
            c = [p.strip().strip('"') for p in re.split(r"[;\t]", linea)]
            if len(c) < 3:
                raise ValueError(f"{ruta}:{n}: se esperaban tres columnas separadas por ';': uso;tipo;{que}")
            uso = USOS.get(c[0].lower())
            if uso is None:
                if era_primera:
                    continue                      # una cabecera
                raise ValueError(f"{ruta}:{n}: el uso «{c[0]}» tiene que ser compra o venta")
            if c[1].upper() == NO_SUJETO:
                tipo = NO_SUJETO
            else:
                d = v1.decimal(c[1].replace(",", ".").rstrip("%").strip())
                if d is None:
                    raise ValueError(f"{ruta}:{n}: el tipo «{c[1]}» no es un porcentaje ni {NO_SUJETO}")
                tipo = v1.texto_tipo(d)
            if not c[2]:
                raise ValueError(f"{ruta}:{n}: falta el nombre de la {que}")
            tabla[(uso, tipo)] = c[2]
    return tabla


def leer_cuentas_iva(ruta):
    """{(RECIBIDA|EMITIDA, '21'|'10'|'4'…): cuenta de IVA}, del fichero «uso;tipo;cuenta» de --cuentas-iva.

    La cuenta es el NOMBRE de la Account de TU ERPNext tal cual, con su abreviatura de compañía: en compras la del
    IVA soportado (p. ej. «4720 - HP IVA Soportado 21% - ME»), en ventas la del repercutido («4770 - HP IVA
    Repercutido 21% - ME»). Sin cuenta para el uso y el tipo de una factura con IVA, esa factura va a revisar
    (ERPNEXT_SIN_CUENTA_IVA). Sin el fichero, todas las que llevan IVA.
    """
    return leer_plantillas(ruta, "cuenta")


def tipo_unico(a):
    """El único registro de IVA de la factura (dict de la pieza) si tiene UN solo tipo y ningún suplido; si no, None."""
    regs = [r for r in a["registros"] if r["clase"] != "AJUSTE"]
    con_iva = [r for r in regs if r["clase"] == "IVA"]
    if len(con_iva) == 1 and len(regs) == 1:
        return con_iva[0]
    return None


def fila_iva(a, cuentas):
    """Las celdas `taxes.*` de la factura: [] si no lleva IVA que poner (un solo tipo con cuota 0, p. ej. exenta), o
    la fila Actual con la cuenta, la descripción y la cuota de registros_iva(). None si falta la cuenta."""
    r = tipo_unico(a)
    if r is None or r["cuota"] == 0:
        return []
    tipo = v1.texto_tipo(r["tipo_iva"])
    cuenta = cuentas.get((a["direccion"], tipo))
    if not cuenta:
        return None
    celdas = ["Actual", cuenta, f"IVA {tipo} %", v1.importe(r["cuota"])]
    return celdas + (["Total", "Add"] if a["direccion"] == v1.RECIBIDA else [])


# ─── la factura en ERPNext ────────────────────────────────────────────────────

def _numero(d):
    """Un Decimal como texto, sin notación científica ni ceros de más."""
    t = format(d.normalize(), "f")
    return t.rstrip("0").rstrip(".") if "." in t else t


def lineas_erpnext(doc, a):
    """[(concepto, cantidad, precio, clave de plantilla | None)] de una factura. ERPNext calcula el importe de la
    línea como qty x rate (`amount` es de solo lectura): si qty x rate no da el importe de la línea (o falta alguno),
    va cantidad 1 y precio = importe, para que la base que contabiliza ERPNext sea la que trae la respuesta."""
    filas = []
    for linea in v1.lineas(doc):              # con el redondeo de 1-2 céntimos ya fundido
        linea = linea or {}
        importe = v1.decimal(linea.get("amount")) or Decimal("0")
        qty, rate = v1.decimal(linea.get("qty")), v1.decimal(linea.get("rate"))
        concepto = v1.leido(linea.get("descripcion_original")) or v1.leido(linea.get("short_name")) or "Línea sin concepto"
        if linea.get("es_no_sujeto"):
            clave = NO_SUJETO
        else:
            clave = v1.texto_tipo(v1.decimal(linea.get("tax_percent")) or Decimal("0"))
        if qty is not None and rate is not None and qty != 0 and abs(v1.redondear(qty * rate) - v1.redondear(importe)) < v1.CENTIMO:
            filas.append((concepto, _numero(qty), _numero(rate), clave))
        else:
            filas.append((concepto, "1", v1.importe(importe), clave))
    if filas:
        return filas
    for r in a["registros"]:                      # factura sin líneas: una fila por tipo, con la base de la cabecera
        if r["clase"] == "AJUSTE":
            continue
        concepto = ("Suplidos / no sujeto" if r["clase"] == "NO_SUJETO"
                    else a["primer_concepto"] or (f"Factura {a['numero']}" if a["numero"] else "Factura"))
        clave = NO_SUJETO if r["clase"] == "NO_SUJETO" else (v1.texto_tipo(r["tipo_iva"]) if r["tipo_iva"] is not None else None)
        filas.append((concepto, "1", v1.importe(r["base"]), clave))
    return filas


def motivos_erpnext(a, doc, lineas, params, cuentas=None):
    """Lo que, además de los motivos de la pieza común, impide importar la factura en ERPNext a ciegas."""
    motivos = []
    fac = doc.get("factura") or {}
    faltan = [opcion for clave, opcion in NECESARIOS[a["direccion"]] if not params.get(clave)]
    if faltan:
        motivos.append(("FALTA_PARAMETRO", f"ERPNext lo exige y la respuesta no lo trae: da {', '.join(faltan)}"))
    if not a["contraparte"]["nombre"]:
        motivos.append(("TERCERO_SIN_NOMBRE", "la otra parte no trae nombre: el proveedor o cliente de ERPNext se "
                                              "busca por su nombre"))
    if (a["retencion"] or Decimal("0")) != 0:
        motivos.append(("RETENCION_ERPNEXT", f"retención de {a['retencion']}: en ERPNext va por Tax Withholding "
                                             "Category y este conector no la pone"))
    if (a["recargo"] or Decimal("0")) != 0 or fac.get("tiene_recargo_equivalencia"):
        motivos.append(("RECARGO_ERPNEXT", "recargo de equivalencia: en ERPNext va en la plantilla de impuestos de "
                                           "la cabecera y este conector no la pone"))
    pais = a["contraparte"]["pais"]
    if a["contraparte"]["nif"] and pais != "ES":
        motivos.append(("OPERACION_NO_INTERIOR", f"la otra parte no es española ({pais or 'país desconocido'}): "
                                                 "intracomunitaria, importación o inversión del sujeto pasivo van en "
                                                 "ERPNext por la plantilla de impuestos de la cabecera, que v1 no trae"))
    if a["es_rectificativa"]:
        motivos.append(("RECTIFICATIVA_ERPNEXT", "rectificativa: el enlace con la factura rectificada (return_against) "
                                                 "y el signo de las cantidades no están medidos en ERPNext "
                                                 f"(rectifica «{a['rectifica_numero']}»)"))
    con_iva = [r for r in a["registros"] if r["clase"] == "IVA"]
    if len(con_iva) > 1:
        motivos.append(("ERPNEXT_VARIOS_TIPOS_IVA", f"{len(con_iva)} tipos de IVA "
                        f"({', '.join(v1.texto_tipo(r['tipo_iva']) + ' %' for r in con_iva)}): ERPNext 16 reparte la "
                        "fila Actual entre todas las líneas y rechaza la factura (medido); va a mano"))
    if (a["suplidos"] or Decimal("0")) != 0 or any(r["clase"] == "NO_SUJETO" for r in a["registros"]):
        motivos.append(("ERPNEXT_SUPLIDOS", f"lleva suplidos o no sujeto ({a['suplidos']}): ERPNext 16 reparte el IVA "
                                            "también sobre esa línea y rechaza la factura (medido); va a mano"))
    if tipo_unico(a) is not None and fila_iva(a, cuentas or {}) is None:
        tipo = v1.texto_tipo(tipo_unico(a)["tipo_iva"])
        uso = "compra" if a["direccion"] == v1.RECIBIDA else "venta"
        motivos.append(("ERPNEXT_SIN_CUENTA_IVA", f"no hay cuenta de IVA para «{uso};{tipo}» en --cuentas-iva"))
    if not lineas:
        motivos.append(("SIN_LINEAS_ERPNEXT", "no hay ninguna línea ni base que poner: ERPNext exige al menos una"))
    elif a["base"] is not None:
        suma = v1.redondear(sum((Decimal(q) * Decimal(p) for _, q, p, _ in lineas), Decimal("0")))
        esperado = v1.redondear(a["base"] + a["suplidos"])
        if abs(suma - esperado) >= v1.CENTIMO:
            motivos.append(("BASE_NO_CUADRA_ERPNEXT", f"las líneas suman {suma} y la base (con suplidos) es {esperado}: "
                                                      "no se añade línea de ajuste"))
    return motivos


def convertir(entradas, mi_nif="", direccion="", params=None, plantillas=None, cuentas=None):
    """Todo el lote. Devuelve {"ficheros": {nombre: [filas]}, "revisar": [...], "omitidos": [...]}."""
    params, plantillas, cuentas = params or {}, plantillas or {}, cuentas or {}
    uom = params.get("uom") or UOM_POR_OMISION
    ficheros, revisar, omitidos = {}, [], []
    dup = v1.duplicadas(entradas, mi_nif, direccion)       # la misma factura dos veces en el lote: la 2.ª a revisar
    for e, duplicada_de in zip(entradas, dup):
        if e["documento"] is None:
            omitidos.append([e["origen"], e["trabajo"], e["omitido"]])
            continue
        doc = e["documento"]
        a = v1.analizar(doc, mi_nif, direccion, duplicada_de)
        fila_rev = lambda nivel, fichero, ms: [e["origen"], e["trabajo"], nivel, a["direccion"], a["numero"],  # noqa: E731
                                               a["contraparte"]["nif"], fichero, " | ".join(c for c, _ in ms)]
        if a["direccion"] == v1.DESCONOCIDA:
            revisar.append(fila_rev(REVISAR, "", a["motivos"]))
            continue
        lineas = lineas_erpnext(doc, a)
        motivos = a["motivos"] + motivos_erpnext(a, doc, lineas, params, cuentas)
        if motivos:
            revisar.append(fila_rev(REVISAR, "", motivos))
            continue
        plant = [plantillas.get((a["direccion"], clave), "") if clave is not None else "" for *_, clave in lineas]
        # Sin plantilla en la línea (items.item_tax_template vacío) no se avisa: el IVA va en la tabla taxes y es
        # exacto sin ella (medido importando en ERPNext 16.37). --plantillas sigue siendo opcional.
        avisos = list(a["notas"])    # AJUSTE_REDONDEO: 1-2 céntimos fundidos en la línea mayor; se dice, no se aparta
        nombre = FICHERO[a["direccion"]]
        if a["direccion"] == v1.RECIBIDA:
            cab = [a["contraparte"]["nombre"], a["numero"], a["fecha"], a["fecha"], "1", params["compania"], "EUR",
                   params["cuenta_acreedores"], "1" if a["es_rectificativa"] else "0"]
            hija = lambda c, q, p, t: [c[:LARGO_ITEM_NAME], c, q, uom, p, params["cuenta_gasto"], t]  # noqa: E731
        else:
            cab = [a["contraparte"]["nombre"], f"Nº original: {a['numero']}", a["fecha"], "1", params["compania"],
                   "EUR", params["cuenta_deudores"], "1" if a["es_rectificativa"] else "0"]
            hija = lambda c, q, p, t: [c[:LARGO_ITEM_NAME], c, q, uom, p, params["cuenta_ingreso"],  # noqa: E731
                                       params["centro_coste"], t]
        iva = fila_iva(a, cuentas)
        n_tax = len(TAX_COMPRA if a["direccion"] == v1.RECIBIDA else TAX_VENTA)
        for i, ((concepto, qty, precio, _), t) in enumerate(zip(lineas, plant)):
            tax = (iva or [""] * n_tax) if i == 0 else [""] * n_tax
            ficheros.setdefault(nombre, []).append((cab if i == 0 else [""] * len(cab)) + hija(concepto, qty, precio, t)
                                                   + tax)
        if avisos:
            revisar.append(fila_rev(AVISO, nombre, avisos))
    return {"ficheros": ficheros, "revisar": revisar, "omitidos": omitidos}


# ─── salida ───────────────────────────────────────────────────────────────────

def escribir_erpnext(ruta, direccion, filas):
    """UTF-8 sin marca, coma, todo entre comillas, '\\n' (Frappe lee utf-8-sig/utf-8 con el dialecto excel)."""
    cab, lin = COLUMNAS[direccion]
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=",", quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(cab + lin)
        for f in filas:
            w.writerow([v1.celda_segura(c) for c in f])


def escribir_informe(ruta, cabecera, filas):
    """revisar.csv y omitidos.csv: el formato común de los conectores (punto y coma, todo entre comillas)."""
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(cabecera)
        for f in filas:
            w.writerow([v1.celda_segura(c) for c in f])


def main(argv=None):
    p = argparse.ArgumentParser(description="Respuesta v1 de MuninCloud -> CSV del Data Import de ERPNext "
                                            "(Purchase Invoice y Sales Invoice)")
    p.add_argument("entradas", nargs="+", help="ficheros .json o directorios con las respuestas de la API")
    p.add_argument("--salida", required=True, help="directorio donde escribir los ficheros")
    p.add_argument("--mi-nif", default="", help="el NIF de tu empresa: comprueba la dirección que da la API y la decide "
                                                "si la respuesta no la trae")
    p.add_argument("--direccion", choices=["RECIBIDA", "EMITIDA"], default="",
                   help="fuerza la dirección de TODAS las facturas (manda sobre la que da la API)")
    p.add_argument("--compania", default="", help="la Company de tu ERPNext, tal cual (compras y ventas)")
    p.add_argument("--cuenta-acreedores", default="", help="cuenta a pagar de las compras (credit_to), p. ej. «Acreedores - ME»")
    p.add_argument("--cuenta-gasto", default="", help="cuenta de gasto de las líneas de compra (items.expense_account)")
    p.add_argument("--cuenta-deudores", default="", help="cuenta a cobrar de las ventas (debit_to), p. ej. «Deudores - ME»")
    p.add_argument("--cuenta-ingreso", default="", help="cuenta de ingreso de las líneas de venta (items.income_account)")
    p.add_argument("--centro-coste", default="", help="centro de coste de las líneas de venta (items.cost_center)")
    p.add_argument("--uom", default=UOM_POR_OMISION, help=f"unidad de medida de todas las líneas (por omisión {UOM_POR_OMISION})")
    p.add_argument("--plantillas", default="", help="fichero «uso;tipo;plantilla» con las Item Tax Template de TU ERPNext "
                                                    "(opcional: sin él, la columna items.item_tax_template va vacía)")
    p.add_argument("--cuentas-iva", default="", help="fichero «uso;tipo;cuenta» con las cuentas de IVA de TU ERPNext "
                                                     "(compra;21;4720 - HP IVA Soportado 21%% - ME …): sin la cuenta de "
                                                     "una factura con IVA, esa factura va a revisar")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION} (lectura v1 {v1.VERSION_V1})")
    args = p.parse_args(argv)

    try:
        plantillas = leer_plantillas(args.plantillas)
        cuentas = leer_cuentas_iva(args.cuentas_iva)
    except (OSError, ValueError, IndexError) as e:
        print(f"error de configuración: {e}", file=sys.stderr)
        return 2
    params = {k: (getattr(args, k) or "").strip() for k in ("compania", "cuenta_acreedores", "cuenta_gasto",
                                                            "cuenta_deudores", "cuenta_ingreso", "centro_coste", "uom")}
    r = convertir(v1.leer_entradas(args.entradas), args.mi_nif, args.direccion, params, plantillas, cuentas)
    os.makedirs(args.salida, exist_ok=True)
    escritos = []
    for dirn in (v1.RECIBIDA, v1.EMITIDA):
        nombre = FICHERO[dirn]
        if r["ficheros"].get(nombre):
            escribir_erpnext(os.path.join(args.salida, nombre), dirn, r["ficheros"][nombre])
            escritos.append(nombre)
    escribir_informe(os.path.join(args.salida, "revisar.csv"),
                     ["documento", "trabajo", "nivel", "direccion", "numero", "nif_contraparte", "fichero", "motivos"],
                     r["revisar"])
    escribir_informe(os.path.join(args.salida, "omitidos.csv"), ["documento", "trabajo", "motivo"], r["omitidos"])
    n_rev = sum(1 for f in r["revisar"] if f[2] == REVISAR)
    print(f"ficheros={','.join(escritos) or 'ninguno'} apartadas={n_rev} con_aviso={len(r['revisar']) - n_rev} "
          f"omitidos={len(r['omitidos'])} salida={args.salida}")
    for f in r["revisar"]:
        print(f"  {'apartada' if f[2] == REVISAR else 'aviso   '} {f[0]}: {f[7]}")
    for f in r["omitidos"]:
        print(f"  omitido  {f[0]}: {f[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
