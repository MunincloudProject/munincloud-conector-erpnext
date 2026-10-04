#!/usr/bin/env python3
"""Vía B del conector de ERPNext: OTRO lector de la salida, que no comparte código con el conector ni con la pieza común.

    python3 pruebas/via_b.py <directorio de salida> <entradas: ficheros .json o directorios>

Lee los CSV con su propio analizador (no el módulo csv) y las respuestas JSON por su cuenta, y comprueba:
  - formato: UTF-8 sin marca, fin de línea '\\n', todas las celdas entre comillas, las columnas que espera el
    Data Import de ERPNext para cada DocType (con la tabla `taxes.*`), y ni `amount` ni `tax_id`;
  - agrupación como la hace Frappe: la cabecera solo en la 1.ª fila de cada factura y, en las filas hijas, TODAS
    las columnas de cabecera vacías (una celda de cabecera en una hija parte la factura);
  - ningún obligatorio vacío (los `reqd` de ERPNext 16.37 que el conector emite);
  - n.º de facturas = n.º de cabeceras, y cada una es una factura de la entrada (por número y fecha), una sola vez;
  - la suma de qty x rate de cada factura = la base de la respuesta (base_total + líneas no sujetas);
  - el IVA (medido importando en ERPNext 16.37, 2026-10-04): cada factura importable trae en su 1.ª fila UNA fila
    de `taxes` Actual con la cuota que da la respuesta (calculada aquí de las líneas, sin la pieza), la cuenta y,
    en compras, Total/Add; ninguna otra fila lleva celdas `taxes.*`; una factura de cuota 0 no lleva fila; y en lo
    importable no hay ninguna de varios tipos de IVA ni con suplidos (ERPNext las rechaza);
  - compras solo en el de Purchase Invoice y ventas solo en el de Sales Invoice (por `documento.direccion`);
  - lo importable no lleva retención, recargo, divisa distinta de EUR, rectificativa ni otra parte extranjera ni
    sin nombre (la política, re-derivada aquí sin la pieza);
  - toda factura terminada de la entrada sale UNA vez: en un CSV de ERPNext o en revisar.csv; lo demás, en omitidos.csv;
  - una importada solo puede llevar en revisar.csv el aviso AJUSTE_REDONDEO (sin plantilla de IVA no se avisa).
Última línea: VIA_B=OK … (rc 0), VIA_B=FALLO:<n> (rc 1) o VIA_B=NO_MEDIBLE (rc 2). Solo biblioteca estándar.
"""
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PI = {"fichero": "erpnext_purchase_invoice.csv", "direccion": "RECIBIDA", "tercero": "supplier",
      "cabecera": ("supplier", "bill_no", "bill_date", "posting_date", "set_posting_time", "company", "currency",
                   "credit_to", "is_return"),
      "lineas": ("items.item_name", "items.description", "items.qty", "items.uom", "items.rate",
                 "items.expense_account", "items.item_tax_template"),
      "impuestos": ("taxes.charge_type", "taxes.account_head", "taxes.description", "taxes.tax_amount",
                    "taxes.category", "taxes.add_deduct_tax"),
      "fijos_tax": {"taxes.charge_type": "Actual", "taxes.category": "Total", "taxes.add_deduct_tax": "Add"},
      "obligatorios_cab": ("supplier", "posting_date", "credit_to"),
      "obligatorios_lin": ("items.item_name", "items.qty", "items.uom", "items.rate")}
SI = {"fichero": "erpnext_sales_invoice.csv", "direccion": "EMITIDA", "tercero": "customer",
      "cabecera": ("customer", "remarks", "posting_date", "set_posting_time", "company", "currency", "debit_to",
                   "is_return"),
      "lineas": ("items.item_name", "items.description", "items.qty", "items.uom", "items.rate",
                 "items.income_account", "items.cost_center", "items.item_tax_template"),
      "impuestos": ("taxes.charge_type", "taxes.account_head", "taxes.description", "taxes.tax_amount"),
      "fijos_tax": {"taxes.charge_type": "Actual"},
      "obligatorios_cab": ("customer", "company", "currency", "posting_date", "debit_to"),
      "obligatorios_lin": ("items.item_name", "items.uom", "items.rate", "items.income_account", "items.cost_center")}
VETADAS = ("items.amount", "amount", "tax_id", "taxes")
ESPANOL = re.compile(r"^(ES)?[A-Z0-9][0-9]{7}[A-Z0-9]$")
CENT = Decimal("0.01")


def celdas(linea, sep):
    """Una línea con TODAS las celdas entre comillas -> lista. None si alguna celda no va entre comillas."""
    out, i, n = [], 0, len(linea)
    while i < n:
        if linea[i] != '"':
            return None
        i, trozos = i + 1, []
        while True:
            j = linea.find('"', i)
            if j < 0:
                return None
            trozos.append(linea[i:j])
            if j + 1 < n and linea[j + 1] == '"':
                trozos.append('"')
                i = j + 2
                continue
            i = j + 1
            break
        out.append("".join(trozos))
        if i < n:
            if linea[i] != sep:
                return None
            i += 1
            if i == n:
                return None
    return out


def leer_tabla(ruta, sep, fin, problemas):
    with open(ruta, "rb") as fh:
        b = fh.read()
    if b.startswith(b"\xef\xbb\xbf"):
        problemas.append(f"{os.path.basename(ruta)}: lleva marca BOM")
        b = b[3:]
    try:
        texto = b.decode("utf-8")
    except UnicodeDecodeError:
        problemas.append(f"{os.path.basename(ruta)}: no es UTF-8")
        return []
    if not texto.endswith(fin):
        problemas.append(f"{os.path.basename(ruta)}: no acaba en {fin!r}")
    if fin == "\n" and "\r" in texto:
        problemas.append(f"{os.path.basename(ruta)}: lleva retornos de carro")
    filas = []
    for k, linea in enumerate(texto[:-len(fin)].split(fin) if texto.endswith(fin) else texto.split(fin), 1):
        c = celdas(linea, sep)
        if c is None:
            problemas.append(f"{os.path.basename(ruta)}:{k}: celda sin comillas o mal cerrada")
            continue
        filas.append(c)
    return filas


def dec(v):
    try:
        return Decimal(str(v)) if v is not None and v != "" else None
    except InvalidOperation:
        return None


def entradas(rutas):
    """[(nombre de fichero, sobre o documento)] de las rutas, por su cuenta."""
    out = []
    for r in rutas:
        ficheros = sorted(os.path.join(r, f) for f in os.listdir(r) if f.endswith(".json")) if os.path.isdir(r) else [r]
        for f in ficheros:
            with open(f, encoding="utf-8") as fh:
                o = json.load(fh)
            for x in (o if isinstance(o, list) else [o]):
                out.append((os.path.basename(f), x))
    return out


def factura_de(o):
    """El documento si es una factura terminada; None si debe estar en omitidos."""
    if not isinstance(o, dict):
        return None
    if "factura" in o and "documento" not in o:
        return o
    if o.get("estado") != "terminado" or o.get("error"):
        return None
    d = o.get("documento")
    return d if isinstance(d, dict) and isinstance(d.get("factura"), dict) else None


def iva_segun_b(d):
    """(tipos de IVA, cuota, hay suplidos) de la respuesta, por su cuenta: la cuota de cada línea es importe x tipo
    redondeado al céntimo (0,005 sube); sin líneas, el tipo y la cuota de la cabecera."""
    fac = d.get("factura") or {}
    lineas = [x or {} for x in d.get("lineas") or []]
    suplidos = any(x.get("es_no_sujeto") and (dec(x.get("amount")) or 0) != 0 for x in lineas)
    tipos, cuota = set(), Decimal("0")
    for x in lineas:
        if x.get("es_no_sujeto"):
            continue
        imp, pct = dec(x.get("amount")) or Decimal("0"), dec(x.get("tax_percent")) or Decimal("0")
        if imp != 0:
            tipos.add(format(pct.normalize(), "f"))
        cuota += (imp * pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    if not lineas:
        base, iva = dec(fac.get("base_total")) or Decimal("0"), dec(fac.get("iva_total")) or Decimal("0")
        cuota = iva.quantize(CENT, rounding=ROUND_HALF_UP)
        if base != 0:
            tipos.add(format((iva * 100 / base).quantize(Decimal("1"), rounding=ROUND_HALF_UP), "f"))
    return tipos, cuota, suplidos


def redondeo_segun_b(d):
    """(diferencia de base, diferencia de cuota) entre la cabecera y lo que dan las líneas, por su cuenta. Regla B del
    contrato: si las dos son de 2 céntimos o menos, la cabecera manda y la factura se importa; si alguna pasa, a revisar."""
    fac = d.get("factura") or {}
    lineas = [x or {} for x in d.get("lineas") or [] if not (x or {}).get("es_no_sujeto")]
    if not lineas:
        return Decimal("0"), Decimal("0")
    base_l = sum((dec(x.get("amount")) or Decimal("0")) for x in lineas)
    _, cuota_l, _ = iva_segun_b(d)
    return ((dec(fac.get("base_total")) or Decimal("0")) - base_l,
            (dec(fac.get("iva_total")) or Decimal("0")) - cuota_l)


def es_redondeo(d):
    db, dc = redondeo_segun_b(d)
    return (db != 0 or dc != 0) and abs(db) <= 2 * CENT and abs(dc) <= 2 * CENT


def clave_dup_b(d):
    """Clave de duplicada re-derivada del JSON (regla A): dirección, NIF de la otra parte y número solo A-Z0-9, fecha.
    None si falta algo: entonces no se compara con nadie."""
    fac = d.get("factura") or {}
    dirn = d.get("direccion")
    otra = d.get("proveedor") if dirn == "RECIBIDA" else d.get("cliente") if dirn == "EMITIDA" else None
    nif = re.sub(r"[^A-Z0-9]", "", str((otra or {}).get("cif_nif") or "").upper())
    num = re.sub(r"[^A-Z0-9]", "", str(fac.get("numero") or "").upper())
    fecha = str(fac.get("fecha") or "")
    return (dirn, nif, num, fecha) if otra is not None and nif and num and re.match(r"^\d{4}-\d{2}-\d{2}$", fecha) else None


def importable_segun_b(d):
    """Lo que la política dice que NO puede ir a un CSV de ERPNext, re-derivado del JSON."""
    fac = d.get("factura") or {}
    malos = []
    if any((dec(t.get("importe")) or 0) != 0 for t in d.get("impuestos_y_retenciones") or []
           if "IRPF" in str(t.get("tipo", "")).upper() or "RET" in str(t.get("tipo", "")).upper()):
        malos.append("retención")
    if fac.get("tiene_recargo_equivalencia") or (dec(fac.get("recargo_total")) or 0) != 0:
        malos.append("recargo")
    if str(fac.get("divisa") or "EUR").upper() != "EUR":
        malos.append("divisa")
    if fac.get("es_rectificativa"):
        malos.append("rectificativa")
    otra = d.get("proveedor") if d.get("direccion") == "RECIBIDA" else d.get("cliente")
    nif = str((otra or {}).get("cif_nif") or "").upper().replace(" ", "").replace("-", "")
    if nif and not ESPANOL.match(nif):
        malos.append("otra parte extranjera")
    if not str((otra or {}).get("nombre") or "").strip():
        malos.append("otra parte sin nombre")
    tipos, _, suplidos = iva_segun_b(d)
    if len(tipos) > 1:
        malos.append(f"varios tipos de IVA {sorted(tipos)}")
    if suplidos:
        malos.append("suplidos")
    return malos


def sin_apostrofo(t):
    return t[1:] if t.startswith("'") and t[1:2] in ("=", "+", "-", "@") else t


def comprobar(salida, rutas):
    problemas = []
    docs = entradas(rutas)
    facturas = {}            # (dirección, número, fecha) -> (fichero, documento): la PRIMERA de cada clave
    todas = []               # [(fichero, documento)] de todas las facturas, duplicadas incluidas
    duplicada_de = {}        # fichero de la 2.ª y siguientes -> fichero de la primera (regla A)
    primeras = {}
    omitibles = set()
    for nombre, o in docs:
        d = factura_de(o)
        if d is None:
            omitibles.add(nombre)
            continue
        todas.append((nombre, d))
        k = clave_dup_b(d)
        if k is not None and k in primeras:
            duplicada_de[nombre] = primeras[k]
            continue
        if k is not None:
            primeras[k] = nombre
        fac = d.get("factura") or {}
        facturas.setdefault((d.get("direccion"), str(fac.get("numero") or "").strip(), str(fac.get("fecha") or "")),
                            (nombre, d))
    vistas = {}              # nombre de fichero de entrada -> dónde salió
    n_cab = 0
    for esp in (PI, SI):
        ruta = os.path.join(salida, esp["fichero"])
        if not os.path.exists(ruta):
            continue
        filas = leer_tabla(ruta, ",", "\n", problemas)
        if not filas:
            problemas.append(f"{esp['fichero']}: vacío")
            continue
        cab, cuerpo = filas[0], filas[1:]
        esperadas = list(esp["cabecera"] + esp["lineas"] + esp["impuestos"])
        if cab != esperadas:
            problemas.append(f"{esp['fichero']}: columnas {cab} ≠ {esperadas}")
            continue
        if any(v in cab for v in VETADAS):
            problemas.append(f"{esp['fichero']}: lleva una columna vetada")
        ncab = len(esp["cabecera"])
        grupos = []
        for k, f in enumerate(cuerpo, 2):
            if len(f) != len(cab):
                problemas.append(f"{esp['fichero']}:{k}: {len(f)} celdas, se esperaban {len(cab)}")
                continue
            fila = dict(zip(cab, f))
            if any(f[:ncab]):
                # Frappe abre una factura nueva en cuanto UNA celda de cabecera trae algo (importer.py:556-570)
                if not fila[esp["tercero"]]:
                    problemas.append(f"{esp['fichero']}:{k}: celda de cabecera en una fila sin tercero: Frappe "
                                     "la toma por una factura nueva y parte la anterior")
                elif not all(fila[c] for c in esp["obligatorios_cab"]):
                    problemas.append(f"{esp['fichero']}:{k}: cabecera con un obligatorio vacío")
                grupos.append({"cab": fila, "lineas": [], "fila": k})
            elif not grupos:
                problemas.append(f"{esp['fichero']}:{k}: fila hija sin cabecera delante")
                continue
            if not all(fila[c] for c in esp["obligatorios_lin"]):
                problemas.append(f"{esp['fichero']}:{k}: línea con un obligatorio vacío")
            grupos[-1]["lineas"].append(fila)
        n_cab += len(grupos)
        for g in grupos:
            c = g["cab"]
            numero = sin_apostrofo(c["bill_no"] if esp is PI else re.sub(r"^Nº original: ", "", c["remarks"]))
            fecha = c["bill_date"] if esp is PI else c["posting_date"]
            clave = (esp["direccion"], numero, fecha)
            if clave not in facturas:
                otra = next((k for k in facturas if k[1:] == clave[1:]), None)
                problemas.append(f"{esp['fichero']}:{g['fila']}: la factura {numero} {fecha} "
                                 + ("es de la otra dirección" if otra else "no está en la entrada"))
                continue
            nombre, d = facturas[clave]
            if nombre in vistas:
                problemas.append(f"{nombre}: sale dos veces ({vistas[nombre]} y {esp['fichero']})")
            vistas[nombre] = esp["fichero"]
            suma = sum((dec(x["items.qty"]) or Decimal("0")) * (dec(x["items.rate"]) or Decimal("0"))
                       for x in g["lineas"])
            fac = d.get("factura") or {}
            base = (dec(fac.get("base_total")) or Decimal("0")) + sum(
                (dec(x.get("amount")) or Decimal("0")) for x in d.get("lineas") or [] if x.get("es_no_sujeto"))
            if abs(suma - base) >= CENT:
                problemas.append(f"{nombre}: qty x rate suma {suma.quantize(CENT)} y la base es {base.quantize(CENT)}")
            if c["is_return"] != ("1" if fac.get("es_rectificativa") else "0"):
                problemas.append(f"{nombre}: is_return={c['is_return']} no casa con la respuesta")
            # el IVA: UNA fila de taxes, en la 1.ª fila, con la cuota de la respuesta; ninguna celda taxes.* en las demás
            con_tax = [x for x in g["lineas"] if any(x[t] for t in esp["impuestos"])]
            if any(x is not g["lineas"][0] for x in con_tax):
                problemas.append(f"{nombre}: celdas taxes.* fuera de la 1.ª fila (ERPNext abriría otra fila de IVA)")
            tipos, cuota, _ = iva_segun_b(d)
            if es_redondeo(d):       # regla B: 1-2 céntimos, la cuota es la de la cabecera
                cuota = (dec(fac.get("iva_total")) or Decimal("0")).quantize(CENT, rounding=ROUND_HALF_UP)
            primera = g["lineas"][0]
            if cuota == 0:
                if con_tax:
                    problemas.append(f"{nombre}: cuota 0 en la respuesta y lleva fila de taxes")
            elif not con_tax:
                problemas.append(f"{nombre}: sin fila de taxes y la respuesta trae una cuota de {cuota}")
            else:
                for campo, valor in esp["fijos_tax"].items():
                    if primera[campo] != valor:
                        problemas.append(f"{nombre}: {campo}={primera[campo]!r}, se esperaba {valor!r}")
                if not primera["taxes.account_head"] or not primera["taxes.description"]:
                    problemas.append(f"{nombre}: fila de taxes sin cuenta o sin descripción (obligatorias)")
                if dec(primera["taxes.tax_amount"]) != cuota:
                    problemas.append(f"{nombre}: taxes.tax_amount={primera['taxes.tax_amount']} y la cuota de la "
                                     f"respuesta es {cuota}")
                if len(tipos) == 1 and not re.search(r"(?<![\d.])" + re.escape(next(iter(tipos))) + r"(?![\d.])",
                                                     primera["taxes.description"]):
                    problemas.append(f"{nombre}: la descripción del IVA «{primera['taxes.description']}» no dice el "
                                     f"tipo {next(iter(tipos))} %")
            malos = importable_segun_b(d)
            if malos:
                problemas.append(f"{nombre}: está en {esp['fichero']} y no debería ({', '.join(malos)})")
    # revisar.csv y omitidos.csv
    revisar, omitidos = {}, set()
    for nombre_f, dest in (("revisar.csv", "r"), ("omitidos.csv", "o")):
        ruta = os.path.join(salida, nombre_f)
        if not os.path.exists(ruta):
            problemas.append(f"falta {nombre_f}")
            continue
        filas = leer_tabla(ruta, ";", "\r\n", problemas)
        if not filas:
            continue
        cab = filas[0]
        for f in filas[1:]:
            fila = dict(zip(cab, f))
            if dest == "o":
                omitidos.add(fila.get("documento", ""))
            else:
                revisar.setdefault(fila.get("documento", ""), []).append(
                    (fila.get("nivel", ""), fila.get("motivos", "")))
    for nombre, d in todas:
        apartada = any(n == "REVISAR" for n, _ in revisar.get(nombre, []))
        motivos = " | ".join(m for _, m in revisar.get(nombre, []))
        if nombre in duplicada_de:
            if nombre in vistas:
                problemas.append(f"{nombre}: es la misma factura que {duplicada_de[nombre]} y sale en {vistas[nombre]}")
            if not apartada or "POSIBLE_DUPLICADA" not in motivos:
                problemas.append(f"{nombre}: es la misma factura que {duplicada_de[nombre]} y no está apartada con "
                                 "POSIBLE_DUPLICADA")
            continue
        if apartada and es_redondeo(d) and "NO_CUADRA" in motivos and not importable_segun_b(d):
            db, dc = redondeo_segun_b(d)
            problemas.append(f"{nombre}: apartada por un redondeo de {db} de base y {dc} de cuota (regla B: se importa)")
        if nombre in vistas and es_redondeo(d) and "AJUSTE_REDONDEO" not in motivos:
            problemas.append(f"{nombre}: importada con un redondeo de 1-2 céntimos y sin el aviso AJUSTE_REDONDEO")
        if nombre in vistas and apartada:
            problemas.append(f"{nombre}: está en {vistas[nombre]} y además apartada en revisar.csv")
        avisos_raros = sorted({c.strip() for n, m in revisar.get(nombre, []) if n == "AVISO" for c in m.split("|")}
                              - {"AJUSTE_REDONDEO"})
        if nombre in vistas and avisos_raros:
            problemas.append(f"{nombre}: importada con avisos {avisos_raros} en revisar.csv (el único aviso de una "
                             "importada es AJUSTE_REDONDEO; sin plantilla de IVA no se avisa)")
        elif nombre not in vistas and not apartada:
            problemas.append(f"{nombre}: no sale ni en un CSV de ERPNext ni apartada en revisar.csv")
    for nombre in omitibles:
        if nombre not in omitidos:
            problemas.append(f"{nombre}: no es una factura terminada y no está en omitidos.csv")
    return problemas, len(todas), n_cab, len(vistas)


def main(argv):
    if len(argv) < 2:
        print("uso: via_b.py <salida> <entradas…>", file=sys.stderr)
        print("VIA_B=NO_MEDIBLE motivo=argumentos")
        return 2
    try:
        problemas, n_fac, n_cab, n_imp = comprobar(argv[0], argv[1:])
    except (OSError, ValueError) as e:
        print(f"VIA_B=NO_MEDIBLE motivo={type(e).__name__}: {e}")
        return 2
    for p in problemas:
        print("  ✗", p)
    if problemas:
        print(f"VIA_B=FALLO:{len(problemas)} facturas={n_fac} cabeceras={n_cab}")
        return 1
    print(f"VIA_B=OK facturas={n_fac} cabeceras={n_cab} importadas={n_imp} apartadas={n_fac - n_imp}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
