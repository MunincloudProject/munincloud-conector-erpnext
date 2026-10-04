"""Pruebas del conector de ERPNext. Solo biblioteca estándar:

    python3 -m unittest discover -s pruebas -v

Plantilla del kit de conectores de MuninCloud. Copia como `pruebas/test_conector.py`, cambia `CONECTOR` y `SALIDAS`,
y añade las pruebas propias del programa al final. Las tres de arriba son las mínimas de todo conector.
"""
import contextlib
import csv
import hashlib
import io
import itertools
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import munincloud_v1 as v1  # noqa: E402

CONECTOR = "munincloud_erpnext"            # el módulo del conector, sin .py
SALIDAS = ("erpnext_purchase_invoice.csv", "erpnext_sales_invoice.csv", "revisar.csv", "omitidos.csv")
REVISAR = ("revisar.csv", "documento", ("nivel", "REVISAR"))  # fichero de revisar, columna del documento y filtro (None, o
                                            # ("revisar", "SI") si el fichero lleva todas y marca las de revisar)
ENTRADA = os.path.join(RAIZ, "ejemplos", "entrada")
SALIDA_REF = os.path.join(RAIZ, "ejemplos", "salida")
DATOS = os.path.join(RAIZ, "pruebas", "datos")
MI_NIF = "B00000018"   # NIF reservado por la AEAT para pruebas
# ERPNext: el ejemplo se genera con los parámetros y las plantillas de ejemplo (lo que ejecutan conector.json y el README)
PARAMS = ("--compania", "Empresa de Pruebas", "--cuenta-acreedores", "Acreedores - EP", "--cuenta-gasto", "Compras - EP",
          "--cuenta-deudores", "Deudores - EP", "--cuenta-ingreso", "Ventas - EP", "--centro-coste", "Principal - EP")
CUENTAS_IVA = ("--cuentas-iva", os.path.join(RAIZ, "ejemplos", "cuentas_iva_ejemplo.csv"))
EXTRA_EJEMPLO = PARAMS + ("--plantillas", os.path.join(RAIZ, "ejemplos", "plantillas_erpnext.csv")) + CUENTAS_IVA

conector = __import__(CONECTOR)
_TEMPORALES = []


def tearDownModule():
    for d in _TEMPORALES:
        shutil.rmtree(d, ignore_errors=True)


def generar(entradas, *extra, base=EXTRA_EJEMPLO):
    salida = tempfile.mkdtemp(prefix=CONECTOR + "_")
    _TEMPORALES.append(salida)
    with contextlib.redirect_stdout(io.StringIO()):
        conector.main(["--mi-nif", MI_NIF, "--salida", salida, *base, *extra, *entradas])
    return salida


def documentos(ruta, columna, solo_si=None):
    if not os.path.exists(ruta):
        return set()
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        return {f.get(columna, "") for f in csv.DictReader(fh, delimiter=";")
                if solo_si is None or f.get(solo_si[0]) == solo_si[1]}


def md5(ruta):
    with open(ruta, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


class Kit(unittest.TestCase):
    """Las mínimas del kit: la salida es la del ejemplo, lo desafinado se aparta, lo que la pieza aparta no se pierde."""

    def test_salida_igual_a_la_del_ejemplo(self):
        salida = generar([ENTRADA])
        for nombre in SALIDAS:
            self.assertEqual(md5(os.path.join(salida, nombre)), md5(os.path.join(SALIDA_REF, nombre)), nombre)

    def test_lo_desafinado_sale_a_revisar(self):
        for nombre in sorted(os.listdir(DATOS)):
            if not nombre.startswith("desafinada__"):
                continue
            salida = generar([os.path.join(DATOS, nombre)])
            self.assertIn(nombre, documentos(os.path.join(salida, REVISAR[0]), REVISAR[1], REVISAR[2]), nombre)

    def test_lo_que_la_pieza_aparta_sale_a_revisar(self):
        salida = generar([ENTRADA])
        apartados = documentos(os.path.join(salida, REVISAR[0]), REVISAR[1], REVISAR[2])
        for e in v1.leer_entradas([ENTRADA]):
            if e.get("documento") and v1.analizar(e["documento"], MI_NIF)["revisar"]:
                self.assertIn(e["origen"], apartados, e["origen"])


# ─── las propias de ERPNext ───────────────────────────────────────────────────

import json  # noqa: E402
import subprocess  # noqa: E402


def filas_csv(ruta):
    """[dict] de un CSV de ERPNext (coma), o [] si no existe."""
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def apartadas(salida):
    with open(os.path.join(salida, "revisar.csv"), encoding="utf-8", newline="") as fh:
        return {f["documento"]: f for f in csv.DictReader(fh, delimiter=";")}


def documento_de(nombre, **cambios):
    """Una copia de un ejemplo con cambios en `factura` o en el tercero, escrita en un temporal."""
    with open(os.path.join(ENTRADA, nombre), encoding="utf-8") as fh:
        o = json.load(fh)
    for clave, valor in cambios.items():
        sitio, campo = clave.split("__")
        o["documento"][sitio][campo] = valor
    d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
    _TEMPORALES.append(d)
    ruta = os.path.join(d, "x__" + nombre)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(o, fh)
    return ruta


PI, SI = "erpnext_purchase_invoice.csv", "erpnext_sales_invoice.csv"
CAB_PI, CAB_SI = conector.CAB_COMPRA, conector.CAB_VENTA


class Formato(unittest.TestCase):
    """El CSV como lo agrupa Frappe: cabecera solo en la 1.ª fila, hijas con TODA la cabecera vacía."""

    def test_cabecera_solo_en_la_primera_fila(self):
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json")])
        filas = filas_csv(os.path.join(salida, PI))
        self.assertEqual(len(filas), 2)
        self.assertTrue(all(filas[0][c] for c in ("supplier", "posting_date", "credit_to", "company")))
        for hija in filas[1:]:
            self.assertEqual([hija[c] for c in CAB_PI], [""] * len(CAB_PI))
            self.assertEqual([hija[c] for c in conector.TAX_COMPRA], [""] * len(conector.TAX_COMPRA))
            self.assertTrue(hija["items.item_name"] and hija["items.rate"])

    def test_con_taxes_y_sin_amount_ni_nif(self):
        for nombre, cab in ((PI, conector.CAB_COMPRA + conector.LIN_COMPRA + conector.TAX_COMPRA),
                            (SI, conector.CAB_VENTA + conector.LIN_VENTA + conector.TAX_VENTA)):
            with open(os.path.join(SALIDA_REF, nombre), encoding="utf-8") as fh:
                primera = next(csv.reader(fh))
            self.assertEqual(primera, cab)
            for vetada in ("items.amount", "amount", "tax_id", "taxes"):        # `taxes.*` sí; `taxes` a secas no
                self.assertNotIn(vetada, primera)

    def test_utf8_sin_marca_coma_y_salto_unix(self):
        with open(os.path.join(SALIDA_REF, PI), "rb") as fh:
            b = fh.read()
        self.assertFalse(b.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", b)
        self.assertTrue(b.startswith(b'"supplier","bill_no"'))

    def test_compras_y_ventas_cada_una_en_su_fichero(self):
        salida = generar([ENTRADA])
        compras = {f["bill_no"] for f in filas_csv(os.path.join(salida, PI)) if f["bill_no"]}
        ventas = {f["remarks"] for f in filas_csv(os.path.join(salida, SI)) if f["remarks"]}
        self.assertIn("P-2026-0925", compras)
        self.assertIn("Nº original: F7-V-0903", ventas)
        self.assertFalse(any(n.startswith("Nº original") for n in compras))

    def test_fechas_iso_y_is_return(self):
        f = filas_csv(os.path.join(SALIDA_REF, PI))[0]
        self.assertRegex(f["posting_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(f["bill_date"], f["posting_date"])
        self.assertEqual((f["is_return"], f["set_posting_time"]), ("0", "1"))

    def test_via_b_da_ok_sobre_lo_generado(self):
        salida = generar([ENTRADA, DATOS])
        r = subprocess.run([sys.executable, "-B", os.path.join(RAIZ, "pruebas", "via_b.py"), salida, ENTRADA, DATOS],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith("VIA_B=OK"))


class Lineas(unittest.TestCase):
    """qty x rate es la base que contabiliza ERPNext; nada de línea de ajuste."""

    def test_precio_que_no_cuadra_va_como_uno_por_importe(self):
        # la factura es de dos tipos (va a revisar), así que la regla de la línea se mide en la función
        with open(os.path.join(ENTRADA, "erpnext__venta_precio_no_cuadra.json"), encoding="utf-8") as fh:
            doc = json.load(fh)["documento"]
        f = conector.lineas_erpnext(doc, v1.analizar(doc, MI_NIF, ""))
        self.assertEqual(f[1][1:3], ("1", "80.00"))

    def test_sin_lineas_una_fila_por_tipo_con_la_base(self):
        salida = generar([os.path.join(ENTRADA, "erpnext__sin_lineas.json")])
        f = filas_csv(os.path.join(salida, PI))
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["items.qty"], f[0]["items.rate"]), ("1", "100.00"))

    def test_uom_por_omision_nos_y_parametro(self):
        self.assertTrue(all(f["items.uom"] == "Nos" for f in filas_csv(os.path.join(SALIDA_REF, PI))))
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json")], "--uom", "Unidad")
        self.assertTrue(all(f["items.uom"] == "Unidad" for f in filas_csv(os.path.join(salida, PI))))

    def test_item_name_cabe_en_140_y_description_entera(self):
        largo = "Concepto muy largo " * 12
        with open(os.path.join(ENTRADA, "compras__identidad.json"), encoding="utf-8") as fh:
            o = json.load(fh)
        o["documento"]["lineas"][0]["descripcion_original"] = largo.strip()
        d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
        _TEMPORALES.append(d)
        ruta = os.path.join(d, "largo.json")
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(o, fh)
        f = filas_csv(os.path.join(generar([ruta]), PI))[0]
        self.assertEqual(len(f["items.item_name"]), 140)
        self.assertEqual(f["items.description"], largo.strip())

    def test_sin_plantillas_columna_vacia_y_sin_aviso(self):
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json")], base=PARAMS + CUENTAS_IVA)
        filas = filas_csv(os.path.join(salida, PI))
        self.assertTrue(filas and all(f["items.item_tax_template"] == "" for f in filas))
        self.assertTrue(any(f["taxes.tax_amount"] for f in filas))      # el IVA va igual, en la tabla taxes
        self.assertNotIn("compras__identidad.json", apartadas(salida))   # sin plantilla no hay aviso

    def test_plantilla_de_compra_y_de_venta_distintas(self):
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json"),
                          os.path.join(ENTRADA, "erpnext__venta_un_tipo.json")])
        self.assertEqual(filas_csv(os.path.join(salida, PI))[0]["items.item_tax_template"], "IVA 21% Soportado - EP")
        self.assertEqual(filas_csv(os.path.join(salida, SI))[0]["items.item_tax_template"], "IVA 21% Repercutido - EP")

    def test_plantillas_mal_escritas_paran_con_rc_2(self):
        d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
        _TEMPORALES.append(d)
        ruta = os.path.join(d, "p.csv")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("uso;tipo;plantilla\ncompra;21;IVA 21%\ngasto;21;IVA 21%\n")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc = conector.main(["--salida", d, "--plantillas", ruta, ENTRADA])
        self.assertEqual(rc, 2)


class Revisar(unittest.TestCase):
    """Lo que ERPNext no recibe tal cual sale a revisar, con su motivo, y no entra en ningún CSV."""

    def motivos(self, salida, nombre):
        return apartadas(salida)[nombre]["motivos"].split(" | ")

    def test_sin_parametros_todo_a_revisar(self):
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json"), os.path.join(ENTRADA, "ventas__mixta.json")],
                         base=())
        self.assertFalse(os.path.exists(os.path.join(salida, PI)) or os.path.exists(os.path.join(salida, SI)))
        self.assertIn("FALTA_PARAMETRO", self.motivos(salida, "compras__identidad.json"))
        self.assertIn("FALTA_PARAMETRO", self.motivos(salida, "ventas__mixta.json"))

    def test_sin_cuenta_de_deudores_solo_se_apartan_las_ventas(self):
        i = PARAMS.index("--cuenta-deudores")
        params = PARAMS[:i] + PARAMS[i + 2:]
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json"),
                          os.path.join(ENTRADA, "erpnext__venta_un_tipo.json")], base=params + CUENTAS_IVA)
        self.assertTrue(filas_csv(os.path.join(salida, PI)))
        self.assertFalse(os.path.exists(os.path.join(salida, SI)))
        self.assertEqual(apartadas(salida)["erpnext__venta_un_tipo.json"]["motivos"], "FALTA_PARAMETRO")
        self.assertNotIn("compras__identidad.json", {d for d, f in apartadas(salida).items() if f["nivel"] == "REVISAR"})

    def test_contraparte_sin_nombre(self):
        salida = generar([os.path.join(ENTRADA, "erpnext__venta_sin_nombre.json")])
        self.assertIn("TERCERO_SIN_NOMBRE", self.motivos(salida, "erpnext__venta_sin_nombre.json"))
        self.assertFalse(os.path.exists(os.path.join(salida, SI)))

    def test_retencion_recargo_rectificativa_y_extranjera(self):
        casos = {"compras__mixta_irpf.json": "RETENCION_ERPNEXT", "ventas__identidad.json": "RETENCION_ERPNEXT",
                 "compras__rectificativa.json": "RECTIFICATIVA_ERPNEXT",
                 "compras__intracomunitaria.json": "OPERACION_NO_INTERIOR",
                 "ventas__intracomunitaria.json": "OPERACION_NO_INTERIOR"}
        salida = generar([os.path.join(ENTRADA, n) for n in casos] + [os.path.join(DATOS, "venta_con_recargo.json")])
        for nombre, motivo in casos.items():
            self.assertIn(motivo, self.motivos(salida, nombre), nombre)
        self.assertIn("RECARGO_ERPNEXT", self.motivos(salida, "venta_con_recargo.json"))
        self.assertFalse(os.path.exists(os.path.join(salida, PI)) or os.path.exists(os.path.join(salida, SI)))

    def test_lo_apartado_no_va_en_ningun_csv(self):
        rev = apartadas(SALIDA_REF)
        numeros = {f["bill_no"] for f in filas_csv(os.path.join(SALIDA_REF, PI))} | {
            f["remarks"].replace("Nº original: ", "") for f in filas_csv(os.path.join(SALIDA_REF, SI))}
        cabeceras = [f["bill_no"] for f in filas_csv(os.path.join(SALIDA_REF, PI)) if f["bill_no"]]
        for fila in rev.values():
            if fila["nivel"] == "REVISAR":
                self.assertEqual(fila["fichero"], "")
                if "POSIBLE_DUPLICADA" in fila["motivos"]:
                    # la 1.ª de la pareja sí entra, y una sola vez: lo apartado es esta, no el número
                    self.assertEqual(cabeceras.count(fila["numero"]), 1, fila["documento"])
                else:
                    self.assertNotIn(fila["numero"], numeros - {""}, fila["documento"])

    def test_la_segunda_duplicada_a_revisar_y_la_primera_una_vez(self):
        rev = apartadas(SALIDA_REF)
        self.assertNotIn("compras__duplicada_a.json", rev)
        self.assertEqual(rev["compras__duplicada_b.json"]["nivel"], "REVISAR")
        self.assertIn("POSIBLE_DUPLICADA", rev["compras__duplicada_b.json"]["motivos"])
        cabeceras = [f["bill_no"] for f in filas_csv(os.path.join(SALIDA_REF, PI)) if f["bill_no"]]
        self.assertEqual(cabeceras.count("P-2026-3001"), 1)

    def test_base_que_no_cuadra_sin_linea_de_ajuste(self):
        ruta = documento_de("compras__identidad.json", factura__base_total=160.0, factura__iva_total=33.6,
                            factura__total_factura=193.6)
        salida = generar([ruta])
        self.assertFalse(os.path.exists(os.path.join(salida, PI)))
        self.assertEqual(apartadas(salida)["x__compras__identidad.json"]["nivel"], "REVISAR")

    def test_el_freno_de_la_base_salta_y_calla(self):
        """El freno propio (BASE_NO_CUADRA_ERPNEXT), visto saltar con unas líneas que no dan la base, y callar con las
        buenas: la pieza suele apartar antes (DESGLOSE_NO_CUADRA), así que se mide aquí solo."""
        with open(os.path.join(ENTRADA, "compras__identidad.json"), encoding="utf-8") as fh:
            doc = json.load(fh)["documento"]
        a = v1.analizar(doc, MI_NIF)
        params = {"compania": "C", "cuenta_acreedores": "A", "cuenta_gasto": "G"}
        buenas = conector.lineas_erpnext(doc, a)
        cuentas = {(v1.RECIBIDA, "21"): "IVA 21"}
        codigos = lambda ls: [c for c, _ in conector.motivos_erpnext(a, doc, ls, params, cuentas)]  # noqa: E731
        self.assertEqual(codigos(buenas), [])
        self.assertEqual(codigos(buenas[:1]), ["BASE_NO_CUADRA_ERPNEXT"])
        self.assertEqual(codigos([]), ["SIN_LINEAS_ERPNEXT"])



def documento_con_lineas(nombre, tipos, iva_total=None):
    """Una copia de un ejemplo con el tipo de IVA de cada línea cambiado (lista, uno por línea) y la cabecera recalculada."""
    with open(os.path.join(ENTRADA, nombre), encoding="utf-8") as fh:
        o = json.load(fh)
    doc = o["documento"]
    for linea, t in zip(doc["lineas"], tipos):
        linea["tax_percent"] = t
    iva = sum(v1.redondear(v1.decimal(l["amount"]) * v1.decimal(l["tax_percent"]) / 100) for l in doc["lineas"])
    doc["factura"]["iva_total"] = float(iva if iva_total is None else iva_total)
    doc["factura"]["total_factura"] = float(v1.decimal(doc["factura"]["base_total"]) + iva)
    d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
    _TEMPORALES.append(d)
    ruta = os.path.join(d, "t__" + nombre)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(o, fh)
    return ruta


class Iva(unittest.TestCase):
    """El IVA en la tabla `taxes` (medido importando en ERPNext 16.37, 2026-10-04): una fila Actual con la cuota en las
    facturas de un solo tipo y sin suplidos; las de varios tipos, con suplidos o sin cuenta, a revisar."""

    def test_un_tipo_una_fila_actual_con_la_cuota_en_la_primera(self):
        filas = filas_csv(os.path.join(SALIDA_REF, PI))
        f = next(x for x in filas if x["bill_no"] == "P-2026-0925")
        self.assertEqual([f[c] for c in conector.TAX_COMPRA],
                         ["Actual", "4720 - HP IVA Soportado 21% - EP", "IVA 21 %", "31.50", "Total", "Add"])
        v = filas_csv(os.path.join(SALIDA_REF, SI))
        self.assertEqual([v[0][c] for c in conector.TAX_VENTA],
                         ["Actual", "4770 - HP IVA Repercutido 21% - EP", "IVA 21 %", "58.80"])
        self.assertEqual([v[1][c] for c in conector.TAX_VENTA], [""] * 4)

    def test_la_cuota_es_la_de_registros_iva(self):
        filas = filas_csv(os.path.join(SALIDA_REF, PI)) + filas_csv(os.path.join(SALIDA_REF, SI))
        cuotas = {f.get("bill_no") or f["remarks"].replace("Nº original: ", ""): f["taxes.tax_amount"]
                  for f in filas if f["taxes.charge_type"]}
        vistas = 0
        entradas = v1.leer_entradas([ENTRADA])
        for e, dup in zip(entradas, v1.duplicadas(entradas, MI_NIF, "")):
            if e["documento"] is None or dup:
                continue
            a = v1.analizar(e["documento"], MI_NIF, "")
            if a["numero"] in cuotas:
                regs = [r for r in a["registros"] if r["clase"] == "IVA"]
                self.assertEqual(len(regs), 1, e["origen"])
                self.assertEqual(cuotas[a["numero"]], v1.importe(regs[0]["cuota"]), e["origen"])
                vistas += 1
        self.assertEqual(vistas, len(cuotas))
        self.assertGreaterEqual(vistas, 5)

    def test_redondeo_de_1_2_centimos_entra_cuadrando_con_la_cabecera(self):
        filas = filas_csv(os.path.join(SALIDA_REF, PI))
        rev = apartadas(SALIDA_REF)
        for numero, base, cuota, nombre in (("P-2026-3002", "76.54", "16.08", "compras__redondeo_base.json"),
                                            ("P-2026-3003", "76.53", "16.07", "compras__redondeo_cuota.json")):
            self.assertEqual((rev[nombre]["nivel"], rev[nombre]["motivos"]), ("AVISO", "AJUSTE_REDONDEO"), nombre)
            k = next(i for i, f in enumerate(filas) if f["bill_no"] == numero)
            grupo = [filas[k]] + list(itertools.takewhile(lambda f: not f["bill_no"], filas[k + 1:]))
            suma = sum(Decimal(f["items.qty"]) * Decimal(f["items.rate"]) for f in grupo)
            self.assertEqual(suma.quantize(Decimal("0.01")), Decimal(base), numero)
            self.assertEqual(filas[k]["taxes.tax_amount"], cuota, numero)
        self.assertIn("DESGLOSE_NO_CUADRA", rev["compras__descuadre_3c.json"]["motivos"])

    def test_varios_tipos_y_suplidos_a_revisar(self):
        casos = {"erpnext__tres_tipos.json": "ERPNEXT_VARIOS_TIPOS_IVA", "ventas__mixta.json": "ERPNEXT_VARIOS_TIPOS_IVA",
                 "compras__suplidos.json": "ERPNEXT_SUPLIDOS"}
        salida = generar([os.path.join(ENTRADA, n) for n in casos])
        for nombre, motivo in casos.items():
            fila = apartadas(salida)[nombre]
            self.assertEqual(fila["nivel"], "REVISAR", nombre)
            self.assertIn(motivo, fila["motivos"].split(" | "), nombre)
        self.assertFalse(os.path.exists(os.path.join(salida, PI)) or os.path.exists(os.path.join(salida, SI)))

    def test_dos_tipos_hechos_uno_entran(self):
        """El contrario del anterior: la MISMA factura de tres tipos con todas sus líneas al 21 % sí entra."""
        ruta = documento_con_lineas("erpnext__tres_tipos.json", [21, 21, 21])
        salida = generar([ruta])
        f = filas_csv(os.path.join(salida, PI))
        self.assertEqual(len(f), 3)
        self.assertEqual(f[0]["taxes.charge_type"], "Actual")

    def test_sin_cuenta_de_iva_a_revisar(self):
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json")], base=PARAMS)
        self.assertEqual(apartadas(salida)["compras__identidad.json"]["motivos"], "ERPNEXT_SIN_CUENTA_IVA")
        self.assertFalse(os.path.exists(os.path.join(salida, PI)))
        d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
        _TEMPORALES.append(d)
        solo21 = os.path.join(d, "c.csv")
        with open(solo21, "w", encoding="utf-8") as fh:
            fh.write("uso;tipo;cuenta\ncompra;21;4720 - EP\n")
        salida = generar([os.path.join(ENTRADA, "compras__identidad.json"), os.path.join(ENTRADA, "erpnext__sin_lineas.json")],
                         base=PARAMS + ("--cuentas-iva", solo21))
        self.assertEqual(apartadas(salida)["erpnext__sin_lineas.json"]["motivos"], "ERPNEXT_SIN_CUENTA_IVA")
        self.assertEqual([x["taxes.account_head"] for x in filas_csv(os.path.join(salida, PI)) if x["taxes.account_head"]],
                         ["4720 - EP"])

    def test_cuota_cero_sin_fila_ni_cuenta(self):
        ruta = documento_con_lineas("compras__identidad.json", [0, 0])
        salida = generar([ruta], base=PARAMS)
        f = filas_csv(os.path.join(salida, PI))
        self.assertEqual(len(f), 2, apartadas(salida))
        self.assertTrue(all(x[c] == "" for x in f for c in conector.TAX_COMPRA))

    def test_cuentas_iva_mal_escritas_paran_con_rc_2(self):
        d = tempfile.mkdtemp(prefix="munincloud_erpnext_doc_")
        _TEMPORALES.append(d)
        ruta = os.path.join(d, "c.csv")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("uso;tipo;cuenta\ncompra;21\n")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            rc = conector.main(["--salida", d, "--cuentas-iva", ruta, ENTRADA])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
