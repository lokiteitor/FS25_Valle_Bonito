# Actualización de Soporte para DLC (Beans & Alpacas / Highlands Fishing) y Parche 1.24/1.25

Este documento detalla el diagnóstico y los cambios implementados en `FS25_Granja_Bonita_V4` para:
1. Habilitar todos los cultivos inyectados dinámicamente por los DLCs de Farming Simulator 25 (**Beans & Alpacas Pack** y **Highlands Fishing Pack**), específicamente el café (`coffeeCherries`), plátano (`banana`) y cebolla (`onion`).
2. Incorporar las mejoras de lluvia a alta velocidad e iluminación atmosférica introducidas en el parche **1.24 / 1.25** de Farming Simulator 25.

---

## 1. Soporte de Cultivos DLC (`FoliageMultiLayer` y `densityMap_fruits`)

### Diagnóstico de la Causa Raíz
En Farming Simulator 25, los DLCs oficiales inyectan dinámicamente sus nuevos tipos de follaje (`<FoliageType>`) dentro de la capa `FoliageMultiLayer` de frutas del mapa (`densityMap_fruits.gdm`) al iniciar la partida:
- **`pdlc_beansAndAlpacasPack`**: inyecta `banana` y `coffeeCherries` (los cultivos de campo como `blackBean`, `pintoBean` y `peanut` se registran por XML mediante `fruitTypes.xml`).
- **`pdlc_highlandsFishingPack`**: inyecta `onion`.

El archivo `FS25_Granja_Bonita_V4/map/map.i3d` tenía configurado el `FoliageMultiLayer` de frutas con **`numTypeIndexChannels="5"`**, lo que permite un máximo matemático de $2^5 - 1 = 31$ tipos de follaje (`typeIndex` del `1` al `31`).

Dado que el mapa ya definía **30 `<FoliageType>`** propios (`decoFoliage` hasta `spinach`):
1. `banana` ocupaba el último índice libre (**slot 31**).
2. `coffeeCherries` (**slot 32**) y `onion` (**slot 33**) fallaban al registrarse con el error en `log.txt`:
   ```text
   Error: FoliageSystemDesc: no more type indexes can be allocated for MultiLayer.
   ```
3. Al fallar la asignación de `coffeeCherries` en el sistema de follaje, el DLC desactivaba en cascada los huertos colocables de café, cosechadoras de café y cadenas de producción asociadas.

---

### Cambios Implementados

#### 1.1. Expansión de canales en `FS25_Granja_Bonita_V4/map/map.i3d`
Se amplió el número de bits para el índice de tipo (`numTypeIndexChannels` y `compressionChannels`) de **5 a 6 bits** (aumentando la capacidad de **31 a 63 tipos de follaje**), manteniendo **5 bits** para los estados de crecimiento (`11 - 6 = 5` bits):

```xml
<!-- Antes -->
<FoliageMultiLayer densityMapId="640" numChannels="10" numTypeIndexChannels="5" compressionChannels="5">

<!-- Después -->
<FoliageMultiLayer densityMapId="640" numChannels="11" numTypeIndexChannels="6" compressionChannels="6">
```

#### 1.2. Actualización de `ProceduralPlacement` en `FS25_Granja_Bonita_V4/map/map.i3d`
En la sección `<ProceduralPlacement>` de `map.i3d`, los objetos `<Object type="terrainFoliage" .../>` codifican en su atributo `channels` tanto el `typeIndex` como el `state` mediante desplazamiento de bits:
- Con 5 bits de índice: $\text{channels} = \text{typeIndex} \mid (\text{state} \ll 5)$
- Con 6 bits de índice: $\text{channels} = \text{typeIndex} \mid (\text{state} \ll 6)$

Se actualizaron los 22 objetos de `ProceduralPlacement` asociados a follajes de `densityMap_fruits` (`decoFoliage`, `meadow`, `forestPlants`, `waterPlants`), mientras que `decoBush` (`densityMap_decoBush`) y `forestGrass` (`densityMap_groundFoliage`) conservaron sus valores originales porque pertenecen a capas independientes:

| Objeto (`name`) | `foliageTypeName` | `typeIndex` | `state` | `channels` (5 bits) | `channels` (6 bits) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| `F_Meadow` | `meadow` | 3 | 2 | `67` | **`131`** |
| `F_ForestDryBranch` | `forestPlants` | 4 | 1 | `36` | **`68`** |
| `F_ForestClover` | `forestPlants` | 4 | 2 | `68` | **`132`** |
| `F_ForestStarFlower` | `forestPlants` | 4 | 3 | `100` | **`196`** |
| `F_ForestBunchBerry` | `forestPlants` | 4 | 4 | `132` | **`260`** |
| `F_ForestSalmonBerry` | `forestPlants` | 4 | 5 | `164` | **`324`** |
| `F_ForestStarryFalse` | `forestPlants` | 4 | 6 | `196` | **`388`** |
| `F_ForestSwordFern` | `forestPlants` | 4 | 7 | `228` | **`452`** |
| `F_ForestDeerFern` | `forestPlants` | 4 | 8 | `260` | **`516`** |
| `F_ForestGrass` | `forestPlants` | 4 | 9 | `292` | **`580`** |
| `F_typhaSmall` | `waterPlants` | 5 | 1 | `37` | **`69`** |
| `F_typhaLatifoliaMedium` | `waterPlants` | 5 | 2 | `69` | **`133`** |
| `F_typhaLarge` | `waterPlants` | 5 | 3 | `101` | **`197`** |
| `F_miscanthusSmall` | `waterPlants` | 5 | 4 | `133` | **`261`** |
| `F_miscanthusMedium` | `waterPlants` | 5 | 5 | `165` | **`325`** |
| `F_miscanthusLarge` | `waterPlants` | 5 | 6 | `197` | **`389`** |
| `F_stipaSmall` | `waterPlants` | 5 | 7 | `229` | **`453`** |
| `F_stipaLarge` | `waterPlants` | 5 | 8 | `261` | **`517`** |
| `F_coneFlower` | `decoFoliage` | 1 | 12 | `385` | **`769`** |
| `F_slenderMountainMint` | `decoFoliage` | 1 | 13 | `417` | **`833`** |
| `F_wildBergamot` | `decoFoliage` | 1 | 14 | `449` | **`897`** |
| `F_prairyBlazingStar` | `decoFoliage` | 1 | 15 | `481` | **`961`** |

#### 1.3. Parche binario de cabecera en `FS25_Granja_Bonita_V4/map/data/densityMap_fruits.gdm`
El formato binario GIANTS Density Map (`"MDF` v2) divide cada celda de $32 \times 32$ píxeles en dos *chunks* independientes:
- **Chunk 0 (`typeIndex`)**: bits `[0 .. compressionChannels - 1]` almacenados en bytes (`uint8`).
- **Chunk 1 (`state`)**: bits `[compressionChannels .. numChannels - 1]` almacenados por separado en bytes (`uint8`).

Gracias a esta separación de *chunks*, fue posible actualizar la cabecera de 17 bytes del archivo `.gdm` directamente sin necesidad de regenerar ni perder el follaje ya pintado en el mapa:

| Offset (Byte) | Campo en Cabecera `"MDF` | Valor Anterior | Valor Nuevo |
| :---: | :--- | :---: | :---: |
| `11` (`0x0B`) | `numChannels` | `0x0A` (10) | **`0x0B` (11)** |
| `13` (`0x0D`) | `numTypeIndexChannels` | `0x05` (5) | **`0x06` (6)** |
| `16` (`0x10`) | `compressionChannels` | `0x05` (5) | **`0x06` (6)** |

---

## 2. Mejoras de Lluvia e Iluminación del Parche 1.24 / 1.25 (`environment.xml`)

En la actualización 1.24 de Farming Simulator 25 (*"Improved rain effects while traveling at high speeds"*), GIANTS modificó los parámetros de generación de partículas climáticas y ajustó las curvas de transición solar en `environment.xml`.

### Cambios Implementados en `FS25_Granja_Bonita_V4/map/config/environment.xml`

#### 2.1. Caja de generación de lluvia para alta velocidad (`<weather><rain><types>`)
Con la caja anterior (`24 x 24 x 30` m y `behindCameraBuffer="0.1"`), al conducir vehículos rápidos la cámara salía de la zona de caída antes de que las gotas tocaran el suelo. Se amplió el volumen de generación en `RAIN`, `HAIL`, `SNOW` y `DUST` a **`50 x 50 x 60` m** y se eliminó `behindCameraBuffer="0.1"` para centrar el volumen de lluvia alrededor de la cámara:

```xml
<!-- Antes -->
<type name="RAIN" filename="$data/sky/rain.i3d" numDropsScale="2.0" spawnBoxWidth="24" spawnBoxDepth="24" spawnBoxHeight="30" behindCameraBuffer="0.1"/>
<type name="HAIL" filename="$data/sky/hail.i3d" numDropsScale="2.0" spawnBoxWidth="24" spawnBoxDepth="24" spawnBoxHeight="30" behindCameraBuffer="0.1"/>
<type name="SNOW" filename="$data/sky/snow.i3d" numDropsScale="2.0" spawnBoxWidth="24" spawnBoxDepth="24" spawnBoxHeight="30" behindCameraBuffer="0.1"/>
<type name="DUST" filename="$data/sky/dust.i3d" numDropsScale="1.0" spawnBoxWidth="24" spawnBoxDepth="24" spawnBoxHeight="24" behindCameraBuffer="0.1" .../>

<!-- Después -->
<type name="RAIN" filename="$data/sky/rain.i3d" numDropsScale="2.0" spawnBoxWidth="50" spawnBoxDepth="50" spawnBoxHeight="60"/>
<type name="HAIL" filename="$data/sky/hail.i3d" numDropsScale="2.0" spawnBoxWidth="50" spawnBoxDepth="50" spawnBoxHeight="60"/>
<type name="SNOW" filename="$data/sky/snow.i3d" numDropsScale="2.0" spawnBoxWidth="50" spawnBoxDepth="50" spawnBoxHeight="60"/>
<type name="DUST" filename="$data/sky/dust.i3d" numDropsScale="1.0" spawnBoxWidth="50" spawnBoxDepth="50" spawnBoxHeight="60" .../>
```

#### 2.2. Evento climático de granizo (`WeatherObjectHail`)
Se añadió la definición de granizo (`HAIL`) dentro de las variaciones climáticas de temporada en `<weather>` para equipararlo con el `environment.xml` oficial de `mapUS`:

```xml
<object typeName="HAIL" class="WeatherObjectHail" weight="5">
    <variation weight="1" minHours="1" maxHours="2" minTemperature="15" maxTemperature="22">
        <clouds presetId="RAIN_1" />
        <wind angle="242" speed="20" cirrusSpeedFactor="1.00"/>
        <rain dropScale="1.0" />
    </variation>
</object>
```

#### 2.3. Curvas de iluminación de amanecer y atardecer (`<lighting>`)
Se sincronizaron los tiempos y claves de interpolación de `sunRotation`, `lightScatteringRotation`, `primaryDynamicLightingScale`, `primaryExtraterrestrialColor`, `secondaryExtraterrestrialColor` y `sunIsPrimary` (cambiando el cruce matutino a `6.1` y el vespertino a `19.6` con pasos intermedios de color extraterrestre) para evitar saltos bruscos de sombras e iluminación durante el cambio entre el sol y la luna.

> **Nota sobre *"Improved raindrop effects on vehicle shader"*:** Esta mejora pertenece directamente al shader nativo del juego base (`$data/shaders/vehicleShader.xml`), por lo que todos los vehículos la reciben automáticamente sin requerir cambios adicionales dentro del mapa.
