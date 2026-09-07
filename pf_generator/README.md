# pf_generator

`generate_soil.py` escribe el mapa de suelos de Precision Farming para el mapa de FS25: un
ráster de 2048 x 2048 con cuatro clases de suelo, más una versión con leyenda para poder
mirarlo.

Es **independiente de todo lo demás del árbol**. No importa `map_layout`, no lee el DEM y
no sabe nada del río, los campos ni los pueblos: es ruido multiescala con semilla, cortado
por percentiles fijos. Ver "Out of scope" en `CLAUDE.md`.

    python3 generate_soil.py                      # un mapa en ./output
    python3 generate_soil.py -s 12345 -o output   # un mapa, reproducible
    python3 generate_soil.py -n 200 --size 512    # un lote de 200 previews para elegir
    python3 generate_soil.py -s 3861109994        # el elegido, ya a tamaño completo

Necesita numpy, scipy y Pillow (los tres están en `.venv`).

## Opciones

| Flag | Por defecto | Qué hace |
|---|---|---|
| `-s`, `--seed N` | aleatoria | Semilla del generador. Si se omite, se saca de la entropía del sistema (`random.SystemRandom`), no del reloj, y se imprime para poder reproducir un mapa que guste. En modo lote es la semilla *base* de la que se derivan las de cada mapa. |
| `-o`, `--output-dir DIR` | `output` | Dónde van los PNG. Se crea si no existe. Es relativo al directorio de trabajo, así que desde la raíz del repo es `./output`, no `pf_generator/output`. |
| `--scale-coarse SIGMA` | `120.0` | Sigma gaussiana, en píxeles, de la capa gruesa: los grandes cuerpos geológicos. Lleva el 65% del peso, así que es la que decide la forma del mapa. Más grande = menos zonas y más amplias. |
| `--scale-medium SIGMA` | `40.0` | Sigma de la capa media, 28% del peso: las subregiones dentro de cada cuerpo. |
| `--scale-fine SIGMA` | `12.0` | Sigma de la capa fina, 7% del peso. Solo rompe los bordes entre clases; subirle el peso llenaría el mapa de motas. |
| `--pixel-scale M` | `2.0` | Metros por píxel. **Solo afecta al informe**: escala las hectáreas de la tabla y de la leyenda, y no cambia nada del ráster. `2.0` sobre 2048 px es el mapa de 4096 x 4096 m, 1677.72 ha. Un `--size` menor cubre el mismo terreno con píxeles más grandes, así que las hectáreas no cambian. |
| `--size N` | `2048` | Tamaño del ráster en píxeles; tiene que ser divisor de 2048 (2048, 1024, 512, 256, 128). **Es el mismo mapa, solo rasterizado más grueso**, no otro mapa: ver "Lotes grandes" abajo. |
| `-n`, `--batch N` | `1` | Número de mapas a generar. Con `N > 1` los archivos se escriben planos en el directorio de salida como `NN_seed_<semilla>_soilMap[_vis].png`, para generar muchos candidatos de una vez y elegir uno a ojo. |
| `-j`, `--jobs N` | la mitad de los cores | Mapas generados en paralelo en modo lote, limitado al tamaño del lote. Cada mapa ya usa hasta 6 hilos por dentro para sus filtros gaussianos, y por eso el defecto es la mitad. Se ignora con `--batch 1`. |

Las tres flags `--scale-*` se aplican a los dos campos de ruido, pero el segundo campo (el
que talla los parches minoritarios) usa `0.7 x` las sigmas gruesa y media; solo
`--scale-fine` se comparte tal cual.

## Salida

Dos archivos por mapa, en `--output-dir`:

| Archivo | Qué es |
|---|---|
| `soilMap.png` | El archivo del juego. 2048 x 2048, paleta indexada (modo `P` de PIL), un valor de suelo de cuatro por píxel. |
| `soilMap_vis.png` | 2048 x 2248: el mismo ráster en color con una franja de leyenda oscura debajo — nombres de las clases, rendimiento, hectáreas, porcentaje, conteo de píxeles y la semilla. El juego no lo usa. |

La paleta que se escribe en `soilMap.png` es `[1,1,1, 2,2,2, 0,0,0, 3,3,3]`, así que el
índice del array **no** es el valor que lee el juego: array 0 -> 1, 1 -> 2, 2 -> 0, 3 -> 3.
Esa permutación es la paleta que espera Precision Farming. No tocarla.

## Lotes grandes: previsualizar y luego renderizar

Un mapa completo cuesta unos 21 s de CPU, casi todos en el filtro gaussiano de sigma 120
sobre 2048². A 512 el mismo filtro es 32 veces más barato, así que el flujo es:

    python3 generate_soil.py -n 200 --size 512 -j 12 -o output    # elegir sobre esto
    python3 generate_soil.py -s 3861109994 -o output              # renderizar el elegido

La semilla de cada preview está en su nombre de archivo, y las semillas de un lote se
derivan solo de la semilla base, así que el preview `07_seed_X_soilMap_vis.png` y el mapa
completo de `-s X` son el mismo mapa. Medido en esta máquina (12 núcleos):

| | tiempo |
|---|---|
| lote de 12 a `--size 2048`, `-j 12` | 36.6 s |
| lote de 12 a `--size 512`, `-j 12` | 3.0 s |
| un mapa suelto a 2048 / a 512 | 8.1 s / 0.9 s |

### Por qué el preview es el mismo mapa y no otro parecido

Porque el ruido blanco **siempre se dibuja a 2048²** y se promedia por bloques hasta
`--size`, con las sigmas reducidas en la misma proporción. Pedirle al RNG un campo de
512² directamente consumiría otro trozo de la secuencia y daría un mapa sin ninguna
relación con el de la misma semilla a tamaño completo — elegir sobre eso no serviría de
nada. Promediar ruido blanco por bloques vuelve a ser ruido blanco, así que la estructura
sobrevive intacta a la reducción; lo único que se pierde es el detalle más fino que un
bloque, y con sigma 12 no hay tal detalle.

Comprobado: la clase de suelo del preview coincide con la del mapa completo en el
**99.15 %** de los píxeles a 512, y en el **98.37 %** a 256. Lo que no coincide es el borde
entre clases moviéndose un píxel. El reparto por clase (5 / 46.45 / 43.55 / 5) sale exacto
en las dos resoluciones.

Con `--size 2048` la salida es byte a byte idéntica a la de antes de que existiera la
opción.

### El `_vis.png` de un preview

Por debajo de 2048 la leyenda no cabe (está maquetada en píxeles absolutos y necesita
2010 px de ancho), así que el preview lleva en su lugar una línea de pie con la semilla, el
tamaño y la escala. Tampoco se pierde nada: como el corte por percentil fija el reparto,
la leyenda es idéntica en todos los mapas de un lote salvo por la semilla.

## Las cuatro clases y su reparto

El reparto sale exacto con cualquier semilla, porque los dos cortes son por percentil y no
por umbral de valor. Los dos extremos están capados al 5% a propósito.

| Array | Suelo | Rendimiento | Reparto |
|---|---|---|---|
| 0 | Arena Limosa / Loamy Sand | 75% | 5.00% |
| 1 | Franco Arenoso / Sandy Loam | 100% | 46.45% |
| 2 | Franco / Loam | 125% | 43.55% |
| 3 | Arcilla Limosa / Silty Clay | 80% | 5.00% |

El corte es anidado: un campo de ruido parte el mapa en la mitad arenosa y la arcillosa por
el percentil 48.55, y un *segundo campo independiente* talla después la clase minoritaria
dentro de cada mitad (percentil 9.718 dentro de A, 89.701 dentro de B). Usar un segundo
campo es lo que hace que las clases del 5% sean parches orgánicos dentro de su zona, en vez
de un anillo en su borde.

## Reproducibilidad

Una semilla dada da siempre el mismo mapa, y una semilla base dada da siempre el mismo lote:
las semillas de cada mapa salen de `numpy.random.SeedSequence(semilla_base)`, así que
`-n 200 -s 42` dos veces son los mismos 200 mapas. La semilla de cada mapa está en su nombre
de archivo y escrita en su leyenda, así que un candidato suelto se puede regenerar por su
cuenta con `-s <esa semilla>`.

Los cuatro PNG que hay aquí versionados son dos supervivientes de un lote así, guardados con
su semilla en el nombre.
