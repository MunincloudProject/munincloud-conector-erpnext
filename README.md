# munincloud-conector-erpnext

**MuninCloud · conector ERPNext (Data Import de facturas de compra y de venta).** Convierte las respuestas de la
**API de auditoría de facturas de MuninCloud** (formato `v1`) en los dos CSV que importa el **Data Import** de
ERPNext: uno de `Purchase Invoice` (facturas recibidas) y otro de `Sales Invoice` (facturas emitidas).

Es un único fichero de Python sin dependencias (`munincloud_erpnext.py`, más la pieza común `munincloud_v1.py`).
Cógelo, léelo y adáptalo a tu ERPNext: para eso está. Licencia MIT (`LICENSE`).

**Solo genera el fichero.** No se conecta a tu ERPNext, no usa su API ni toca tu sitio: el CSV lo importas tú.

## Requisitos

Python 3.8 o posterior (solo biblioteca estándar). Probado con Python 3.12; con 3.8 se ha comprobado la sintaxis.

## En cinco minutos

```bash
python3 munincloud_erpnext.py --mi-nif B00000018 --compania "Empresa de Pruebas" --cuenta-acreedores "Acreedores - EP" --cuenta-gasto "Compras - EP" --cuenta-deudores "Deudores - EP" --cuenta-ingreso "Ventas - EP" --centro-coste "Principal - EP" --plantillas ejemplos/plantillas_erpnext.csv --cuentas-iva ejemplos/cuentas_iva_ejemplo.csv --salida salida ejemplos/entrada
head -3 salida/erpnext_purchase_invoice.csv
python3 -m unittest discover -s pruebas
```

`B00000018` es un NIF reservado por la AEAT para pruebas: pon el tuyo. La compañía, las cuentas, el centro de coste y
las plantillas y las cuentas de IVA de ejemplo son inventados: pon **los nombres exactos de tu ERPNext** (con la abreviatura de tu compañía
detrás, como los crea ERPNext: «Acreedores - EP»). La salida tiene que ser la de `ejemplos/salida/`.

Lo que deja en `--salida`:

| fichero | qué es |
|---|---|
| `erpnext_purchase_invoice.csv` | las facturas **recibidas** que se pueden importar: *Data Import* → DocType **Purchase Invoice** → *Insert New Records* |
| `erpnext_sales_invoice.csv` | las **emitidas**, igual, con DocType **Sales Invoice** |
| `revisar.csv` | qué factura se apartó (`nivel` REVISAR: **no está** en ningún CSV de ERPNext) o lleva un aviso (`nivel` AVISO: sí está), y por qué |
| `omitidos.csv` | lo que no era una factura terminada (trabajo en curso, rechazado por la API, no es una factura) |

**Antes de importar:** los proveedores y clientes **tienen que existir ya** en tu ERPNext, con el mismo nombre que la
columna `supplier` / `customer` (ERPNext no los crea al importar la factura: la rechaza con «Value missing for
Supplier» y no deja nada a medias). Si son nuevos, impórtalos antes con su propia plantilla de *Supplier* / *Customer*.

**Cómo importar:** con la herramienta web *Data Import* (DocType, *Insert New Records*, adjuntas el CSV, *Start
Import*). Las facturas quedan en **borrador** para que las revises y las valides tú. No uses la orden de terminal
`bench data-import` para importar más de un fichero: en Frappe 16.36 crea la importación sin nombre y, en la segunda,
salta filas y dice «Successfully imported» sin crear nada (medido el 2026-10-04).

Cada CSV va en UTF-8 sin marca, separado por comas, todo entre comillas y con salto de línea `\n`. **Una fila por
línea de factura**: la cabecera de la factura va solo en la primera fila y, en las siguientes, **todas** las columnas
de cabecera van vacías. No lo cambies a mano: si una fila de líneas lleva algo en una columna de cabecera, Frappe la
toma por una factura nueva y parte la anterior en dos.

Las columnas, con los nombres de campo de ERPNext:

| `Purchase Invoice` | `Sales Invoice` | de dónde sale |
|---|---|---|
| `supplier` | `customer` | el nombre de la otra parte (`proveedor.nombre` / `cliente.nombre`) |
| `bill_no`, `bill_date` | `remarks` («Nº original: …») | el número y la fecha de la factura; en venta, ERPNext numera con su serie y el número de tu papel va en las observaciones |
| `posting_date`, `set_posting_time`=1 | igual | la fecha de la factura, en ISO (`AAAA-MM-DD`) |
| `company`, `currency`=EUR | igual | `--compania`; solo pasan facturas en euros |
| `credit_to` | `debit_to` | `--cuenta-acreedores` / `--cuenta-deudores` |
| `is_return` | igual | 0 (las rectificativas se apartan, ver abajo) |
| `items.item_name`, `items.description` | igual | el concepto de la línea (`item_name` recortado a 140 caracteres; entero en `description`) |
| `items.qty`, `items.rate` | igual | cantidad y precio; si no dan el importe de la línea, cantidad 1 y precio = importe |
| `items.uom` | igual | `--uom`, por omisión `Nos` |
| `items.expense_account` | `items.income_account`, `items.cost_center` | `--cuenta-gasto` / `--cuenta-ingreso` y `--centro-coste` |
| `items.item_tax_template` | igual | el tipo de IVA de la línea, traducido por tu fichero `--plantillas` (opcional) |
| `taxes.charge_type`=Actual, `taxes.account_head`, `taxes.description`, `taxes.tax_amount` | igual | **el IVA**: una fila de la tabla de impuestos, solo en la primera fila de la factura, con la cuenta de tu `--cuentas-iva` para ese uso y tipo, «IVA 21 %» y la **cuota** de la respuesta |
| `taxes.category`=Total, `taxes.add_deduct_tax`=Add | — | lo que pide la tabla de impuestos de compra (en venta no existen) |

No se emiten `amount` (es de solo lectura: ERPNext lo calcula como `qty × rate`) ni el NIF (`tax_id` también es de solo
lectura: sale del proveedor o cliente).

**El IVA va en la tabla `taxes`, y por eso solo pasan las facturas de UN tipo de IVA sin suplidos.** Medido importando
en ERPNext 16.37: la plantilla de la línea (`items.item_tax_template`) sola deja el IVA a 0, porque ERPNext lo calcula
desde el artículo, que el CSV no trae; con una fila `Actual` con la cuota, la factura de un tipo entra con base, IVA y
total exactos. Con dos tipos o con un suplido, ERPNext reparte esa fila entre **todas** las líneas y rechaza la
factura («Item Wise Tax Details do not match with Taxes and Charges»): esas se apartan y se meten a mano. Una factura
de un solo tipo con cuota 0 (exenta) va sin fila de impuestos.

## Compra o venta: cómo lo decide

Manda la `direccion` de la respuesta (RECIBIDA → `Purchase Invoice`, EMITIDA → `Sales Invoice`), que es la que declaró
quien subió la factura. Si das `--mi-nif`, además se comprueba contra el papel: si tu NIF sale solo en el lado contrario,
la factura se aparta (`DIRECCION_CONTRADICE_NIF`). Si la respuesta no trae dirección, la decide tu NIF; si tampoco así,
se aparta (`DIRECCION_DESCONOCIDA`). `--direccion RECIBIDA|EMITIDA` la fuerza para todo el lote.

## Cuándo una factura sale para revisar

Una factura apartada **no va** en ningún CSV de ERPNext: está solo en `revisar.csv`, con sus motivos.

| motivo | qué pasa |
|---|---|
| los de la pieza común (`DESCUADRE`, `DESGLOSE_NO_CUADRA`, `NUMERO_NO_LEIDO`, `FECHA_NO_VALIDA`, `NIF_CONTRAPARTE_NO_LEIDO`, `IMPORTES_NO_LEIDOS`, `NIF_AEAT_BAJA_O_REVOCADO`, `NIF_AEAT_NO_IDENTIFICADO`, `DIVISA_NO_EUR`, `AVISO_…`, `DICTAMEN_…`…) | lo que ningún programa contable puede recibir tal cual: ver `munincloud_v1.py` |
| `FALTA_PARAMETRO` | ERPNext exige la compañía, la cuenta a pagar o a cobrar, la cuenta de gasto o de ingreso y, en venta, el centro de coste, y la respuesta no los trae: sin el de una dirección, se apartan las facturas de esa dirección |
| `TERCERO_SIN_NOMBRE` | la otra parte no trae nombre: ERPNext busca el proveedor o el cliente por su nombre, y no se inventa uno |
| `RETENCION_ERPNEXT` | lleva retención (IRPF): en ERPNext va por *Tax Withholding Category*, que este conector no pone |
| `RECARGO_ERPNEXT` | lleva recargo de equivalencia: en ERPNext va en la plantilla de impuestos de la cabecera, que este conector no pone |
| `OPERACION_NO_INTERIOR` | la otra parte no es española: intracomunitaria, importación o inversión del sujeto pasivo van en ERPNext por la plantilla de impuestos de la cabecera, que `v1` no trae |
| `RECTIFICATIVA_ERPNEXT` | rectificativa: el enlace con la factura rectificada (`return_against`) y el signo de las cantidades no están medidos en ERPNext |
| `SIN_LINEAS_ERPNEXT` | no hay ninguna línea que poner |
| `ERPNEXT_VARIOS_TIPOS_IVA` | lleva más de un tipo de IVA: ERPNext 16 reparte la fila `Actual` entre todas las líneas y la rechaza (medido) |
| `ERPNEXT_SUPLIDOS` | lleva suplidos o una parte no sujeta: lo mismo, ERPNext reparte el IVA también sobre esa línea y la rechaza (medido) |
| `ERPNEXT_SIN_CUENTA_IVA` | tu fichero `--cuentas-iva` no tiene cuenta para ese uso (compra o venta) y ese tipo de IVA |
| `BASE_NO_CUADRA_ERPNEXT` | `qty × rate` de las líneas no da la base de la respuesta (con los suplidos): **no se añade una línea de ajuste** |

Una línea sin plantilla de IVA (`items.item_tax_template` vacía, porque no diste `--plantillas` o su tipo no está en
tu fichero) **no** aparta la factura ni lleva aviso: el IVA va igual, y exacto, en la tabla `taxes`.

**Duplicadas y redondeo** (pieza común): si el lote trae dos veces la misma factura (dirección, NIF de la otra parte, número y fecha), la primera sigue su camino y la segunda y siguientes van a revisar con `POSIBLE_DUPLICADA` (nunca se funden); y si las líneas difieren de la cabecera en 1 o 2 céntimos (base, cuota o las dos), la diferencia se funde en la línea de mayor base, la factura entra cuadrando exacto con la cabecera y `revisar.csv` lo dice con un aviso `AJUSTE_REDONDEO`; con 3 céntimos o más, a revisar como siempre.

El conector **no arregla** nada de esto: lo enseña. Una factura que no cuadra sale con su descuadre a la vista.

## Lo que la respuesta `v1` no trae, y este conector no inventa

- **La compañía, las cuentas y el centro de coste** (`--compania`, `--cuenta-acreedores`, `--cuenta-gasto`,
  `--cuenta-deudores`, `--cuenta-ingreso`, `--centro-coste`): los nombres exactos de tu plan de cuentas.
- **La unidad de medida** (`--uom`): v1 no la trae; por omisión `Nos`, la que crea ERPNext al instalarse. Es una
  suposición: si en tu sitio no existe, pon la tuya.
- **Las cuentas de IVA** (`--cuentas-iva`): un fichero `uso;tipo;cuenta`, una fila por uso y tipo, con el nombre exacto
  de tu cuenta: `compra;21;4720 - HP IVA Soportado 21% - ME`, `venta;21;4770 - HP IVA Repercutido 21% - ME`… Ejemplo
  (con nombres inventados) en `ejemplos/cuentas_iva_ejemplo.csv`. Sin la cuenta de una factura con IVA, esa factura se
  aparta (`ERPNEXT_SIN_CUENTA_IVA`).
- **Las plantillas de IVA** (`--plantillas`, opcional): un fichero `uso;tipo;plantilla`, una fila por tipo y uso (`compra` o
  `venta`; el tipo en %, o `NS` para suplidos y no sujeto), con el nombre exacto de tu *Item Tax Template*. Ejemplo en
  `ejemplos/plantillas_erpnext.csv`. Sin él, la columna va vacía y no pasa nada: la plantilla de la línea **no pone el
  IVA**, lo pone la fila de `taxes` (ver arriba).
- **El proveedor o cliente**: va por su nombre y tiene que existir en tu ERPNext con ese nombre (es un enlace).
- **La retención y el recargo**: no van en el CSV (esas facturas se apartan). La cuota de IVA sí va: la de la respuesta.

## Contra qué se ha verificado

- **2026-10-04** · contra el código de ERPNext 16.37 / Frappe 16.36 (campos obligatorios y de solo lectura del Data
  Import: `purchase_invoice.json`, `sales_invoice.json` y sus tablas de líneas; la agrupación de filas hijas de
  `importer.py`) y el banco común de los conectores de MuninCloud.
- **2026-10-04** · **importado en ERPNext 16.37** (Frappe 16.36), en borrador, por el importador *Data Import*:
  facturas de compra de un tipo de IVA con base, IVA y total exactos; las de varios tipos o con suplidos, rechazadas
  por ERPNext, y por eso se apartan; un proveedor que no existe hace fallar la factura sin crear nada; con la unidad
  `Nos` y sin artículo (`item_code`) en la línea.

Lo que no figura aquí **no está verificado** en el programa: la salida cumple el formato documentado, nada más. No
verificado todavía en una instancia: la tabla de impuestos de las facturas de **venta** (su forma sale de los campos
de `Sales Taxes and Charges` de ERPNext 16.37, no de una importación), una factura exenta sin fila de impuestos, y la
serie (`naming_series`) y la lista de precios que pone ERPNext por defecto en venta.

## Pruebas

```bash
python3 -m unittest discover -s pruebas -v
python3 pruebas/via_b.py ejemplos/salida ejemplos/entrada
```

Comprueban, sobre los ejemplos y sobre las respuestas desafinadas de `pruebas/datos/`, que cada factura cuadra o sale
para revisar y que la salida es la de `ejemplos/salida/`. `pruebas/via_b.py` es **otro lector** de los CSV, que no
comparte código con el conector: comprueba la cabecera solo en la 1.ª fila, ningún obligatorio vacío, una cabecera
por factura, `qty × rate` = base de la respuesta, compras y ventas cada una en su fichero, y que cada factura lleva
su fila de IVA con la cuota de la respuesta, ninguna de varios tipos o con suplidos está en lo importable y una
factura importada solo lleva en `revisar.csv` el aviso `AJUSTE_REDONDEO`.

`munincloud_v1.py` es la misma pieza en todos los conectores de MuninCloud: si la cambias, cámbiala en todos.

## Marcas

ERPNext® es una marca de Frappe Technologies Pvt. Ltd. MuninCloud no está asociado a, ni certificado por, Frappe
Technologies Pvt. Ltd. Frappe® también es una marca de Frappe Technologies Pvt. Ltd.

## Licencia

MIT. Copyright (c) 2026 Bogarra Integrated SL. Ver `LICENSE`. ERPNext y Frappe son GPL-3.0 y no se distribuyen aquí:
este conector solo escribe un fichero que ERPNext sabe leer.
