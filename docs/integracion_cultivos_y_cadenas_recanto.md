# Integración de Cultivos Adicionales y Cadenas Productivas (Recanto da Alvorada $\rightarrow$ Granja Bonita V4)

Este documento detalla todos los cambios técnicos, archivos copiados, ajustes de rutas (`maps/` $\rightarrow$ `map/`), dependencias de texturas externas y configuraciones XML/I3D implementadas en `FS25_Granja_Bonita_V4` para integrar los cultivos personalizados y las cadenas productivas de `FS25_Recanto_da_Alvorada` (excluyendo el cultivo y la cadena productiva del café propio de ese mapa).

---

## 1. Resumen de Elementos Integrados

### 1.1. Cultivos Adicionales (`fruitTypes` y `fillTypes`)
| Cultivo (`fruitType`) | `fillType` Asociado | Nombre en Español | Tipo de Cosecha / Hilerado |
| :--- | :--- | :--- | :--- |
| `BLACKBEAN` | `BLACKBEAN` | Frijol Negro | Cabezal de grano (`GRAINHEADER`), segadora (`MOWER` $\rightarrow$ `STRAW`, `COMBINE_MOWER` $\rightarrow$ `SOYBEAN_CUT`) y picadora (`FORAGEHARVESTER` $\rightarrow$ `CHAFF`). |
| `NAPIER` | `NAPIER` | Pasto Elefante | Cabezal de maíz/forraje (`MAIZEHEADER`, `MAIZECUTTER`, `DIRECTCUTTER`) y picadora de forraje (`FORAGEHARVESTER` $\rightarrow$ `CHAFF`, factor `7.8`). |
| `PEANUT` | `PEANUT` / `PEANUT_CUT` | Maní | Segadora/arrancadora (`COMBINE_MOWER` $\rightarrow$ `PEANUT_CUT`), recolección por pickup (`PEANUT_CUT`), cabezal de cosechadora y picadora (`FORAGEHARVESTER` $\rightarrow$ `CHAFF`). |
| `WHEAT` *(actualizado)* | `WHEAT` | Trigo | Incluye configuración de hilerado de trigo (`WHEAT_CUT`) compatible con `COMBINE_MOWER`. |

### 1.2. Cadenas Productivas (Fábricas Colocables) y Palets
| Identificador | Archivo XML | Nombre en Tienda | Entradas (`inputs`) | Salidas (`outputs`) |
| :--- | :--- | :--- | :--- | :--- |
| `peanutbag` | `map/placeables/factory/peanutbag/dairy.xml` | Fábrica de Maní Procesado (`$l10n_shopItem_peanutbag`) | `PEANUT` (`22`) | `PEANUT_BAG` (`20`) |
| `dairy` | `map/placeables/factory/dairy/dairy.xml` | Lácteos (`$l10n_shopItem_dairy`) | `MILK`, `GOATMILK`, `BUFFALOMILK`, `SUGAR` | `BUTTER`, `CHEESE`, `GOATCHEESE`, `BUFFALOMOZZARELLA`, `CHOCOLATE`, `MILK_BOTTLED`, `GOATMILK_BOTTLED`, `BUFFALOMILK_BOTTLED` |
| `grainFlourMill` | `map/placeables/factory/grainFlourMill/grainFlourMill.xml` | Molino de Granos (`$l10n_shopItem_grainMill`) | `WHEAT`, `BARLEY`, `OAT`, `SORGHUM`, `RICELONGGRAIN`, `RICE` | `FLOUR`, `RICEFLOUR` |
| `peanutBagPallet` | `map/pallets/peanutBagPallet/peanutBagPallet.xml` | Palet de Bolsas de Maní (`$l10n_storeItem_peanutBagsPallet`) | — | Capacidad: `960 L` de `PEANUT_BAG` |

---

## 2. Registro de Follajes en `FS25_Granja_Bonita_V4/map/map.i3d`

### 2.1. Carpetas de Follaje Copiadas (`map/foliage/`)
Se copiaron los directorios completos con sus archivos `.xml`, `.i3d`, `.i3d.shapes`, texturas `.dds` por etapa (`Stage01` a `Stage04`) y mapas de distancia (`distance/`):
- `FS25_Granja_Bonita_V4/map/foliage/blackbean/`
- `FS25_Granja_Bonita_V4/map/foliage/napier/`
- `FS25_Granja_Bonita_V4/map/foliage/peanut/`
- `FS25_Granja_Bonita_V4/map/foliage/wheat/`

### 2.2. Modificaciones en `<Files>` y `<FoliageMultiLayer>` de `map/map.i3d`
1. En la sección `<Files>` de `FS25_Granja_Bonita_V4/map/map.i3d`:
   - Se actualizó `fileId="588"` para apuntar a `foliage/wheat/wheat.xml`.
   - Se añadieron los nuevos identificadores de archivo (`9001`, `9002`, `9003`) para evitar colisiones con el ID máximo previo (`826`):
     ```xml
     <File fileId="588" filename="foliage/wheat/wheat.xml"/>
     <File fileId="9001" filename="foliage/blackbean/blackbean.xml"/>
     <File fileId="9002" filename="foliage/napier/napier.xml"/>
     <File fileId="9003" filename="foliage/peanut/peanut.xml"/>
     ```
2. En el `<FoliageMultiLayer densityMapId="640" numChannels="11" numTypeIndexChannels="6" compressionChannels="6">` (que soporta hasta 63 tipos de follaje gracias a la expansión de 6 bits documentada en `actualizacion_dlc_beans_y_parche_1_24.md`), se agregaron los 3 nuevos `<FoliageType>` después de `spinach`:
   ```xml
   <FoliageType name="blackbean" foliageXmlId="9001"/>
   <FoliageType name="napier" foliageXmlId="9002"/>
   <FoliageType name="peanut" foliageXmlId="9003"/>
   ```

---

## 3. Texturas de Volquete (`fillPlanes`), Iconos HUD y Efectos de Cosecha

### 3.1. Archivos en `FS25_Granja_Bonita_V4/map/fillPlanes/`
- **Texturas de volumen y cordón (`map/fillPlanes/`):**
  - `blackbean_diffuse.dds`, `blackbean_normal.dds`
  - `peanut_diffuse.dds`, `peanut_normal.dds`
  - `peanutCut_diffuse.dds`, `peanutCut_normal.dds` *(Requeridas también por `map/effects/pickup/pickupIN.i3d` y `pickupOUT.i3d` mediante la ruta relativa `../../fillPlanes/peanutCut_diffuse.dds`)*.
- **Iconos HUD (`map/fillPlanes/huds/`):**
  - `hud_fill_blackbean.dds`
  - `hud_fill_napier.dds`
  - `hud_fill_peanut.dds`
  - `hud_fill_PeanutCut.dds`
  - `hud_fill_peanut_bag.dds`

### 3.2. Efectos de Cosecha (`FS25_Granja_Bonita_V4/map/effects/`)
Se copiaron los modelos 3D de partículas/plantas en movimiento y sus archivos de configuración, corrigiendo todas las rutas de `maps/effects/...` a `map/effects/...` y eliminando la referencia a `coffeeEffects.xml`:
- `map/effects/motionPathEffects.xml`: referencia a `map/effects/cutter/cutterEffects.xml`, `map/effects/chopperEffects.xml` y `map/effects/pickupEffects_Peanut.xml`, manteniendo los demás efectos con rutas base `$data/effects/...`.
- `map/effects/cutter/cutterEffects.xml`: define los efectos de corte para `NAPIER`, `BLACKBEAN` y `PEANUT` apuntando a:
  - `map/effects/cutter/napier/napier_harvestReadyGreen.i3d`
  - `map/effects/cutter/blackbean/blackbean_greenBig.i3d` y `blackbean_harvestReady.i3d` *(usan ruta relativa `../../../foliage/blackbean/` hacia las texturas del follaje)*.
  - `map/effects/cutter/peanut/peanut_greenBig.i3d` y `peanut_harvestReady.i3d`
- `map/effects/chopperEffects.xml`: incluye `BLACKBEAN` y `PEANUT` en los efectos `STRAW_CHOPPER` (`CHOPPER`, `CHAFFER`, `DROP`).
- `map/effects/pickupEffects_Peanut.xml`: define los efectos `PICKUPIN` y `PICKUPOUT` para `PEANUT_CUT` usando `map/effects/pickup/pickupIN.i3d` y `pickupOUT.i3d`.

---

## 4. Fábricas, Palets y Dependencias de Texturas Externas

### 4.1. Fábricas (`FS25_Granja_Bonita_V4/map/placeables/factory/`)
En el mapa origen (`Recanto da Alvorada`), las fábricas `peanutbag` y `grainFlourMill` tenían `<showInStore>false</showInStore>` y `<canBeSold>false</canBeSold>` porque estaban precolocadas en el mapa. En `FS25_Granja_Bonita_V4` se habilitaron en el menú de construcción (`true`) y se corrigieron las rutas de `maps/placeables/...` a `map/placeables/...`:
1. **Fábrica de Maní Procesado (`map/placeables/factory/peanutbag/`):**
   - Archivos locales: `dairy.xml`, `dairy.i3d`, `dairy.i3d.shapes`, `store_dairy3.dds`.
2. **Lácteos (`map/placeables/factory/dairy/`):**
   - Archivos locales: `dairy.xml`, `dairy.i3d`, `dairy.i3d.shapes`, `store_milk.dds`.
3. **Molino de Harina (`map/placeables/factory/grainFlourMill/`):**
   - Archivos locales: `grainFlourMill.xml`, `grainFlourMill.i3d`, `grainFlourMill.i3d.shapes`, `store_grainFlourMill.dds`.

### 4.2. Texturas Externas de las Fábricas (`FS25_Granja_Bonita_V4/map/textures/`)
Los archivos `.i3d` de las 3 fábricas referencian sus texturas arquitectónicas mediante la ruta relativa `../../../textures/`. Se copiaron las **13 texturas necesarias** en `FS25_Granja_Bonita_V4/map/textures/`:
- **Para `peanutbag/dairy.i3d`:**
  - `amendoim_fabrica_diffuse.dds`
  - `amendoim_fabrica_normal.dds`
  - `amendoim_fabrica_specular_vis.dds`
  - `amendoim_fabrica_Height.dds`
- **Para `dairy/dairy.i3d`:**
  - `Laticinio_diffuse.dds`
  - `Laticinio_normal.dds`
  - `Laticinio_specular.dds`
  - `LaticinioUV2_specular.dds`
- **Para `grainFlourMill/grainFlourMill.i3d`:**
  - `Moinho_Base_color.dds`
  - `Moinho_Base_colorspecular.dds`
  - `Moinho_Normal_OpenGL.dds`
  - `Moinho_Height.dds`
  - `moinho_mask.dds`

### 4.3. Palet de Sacos de Maní (`FS25_Granja_Bonita_V4/map/pallets/peanutBagPallet/`)
- Archivos copiados: `peanutBagPallet.xml`, `peanutBagPallet.i3d`, `peanutBagPallet.i3d.shapes`, `peanutDiffusePallets.dds`, `Peanut_normal.dds`, `PeanutSpecular.dds`, `store_peanutBagsPallet.dds`.
- En `peanutBagPallet.xml` se actualizaron las rutas `<image>` y `<filename>` de `maps/pallets/...` a `map/pallets/...`.

---

## 5. Archivos de Configuración (`map/config/`, `map/map.xml`, `modDesc.xml`)

### 5.1. `FS25_Granja_Bonita_V4/map/map.xml`
Se enlazaron los archivos de configuración personalizados del mapa:
```xml
<animals filename="$dataS/character/animals.xml">
    <names filename="$dataS/character/animalNames.xml" />
    <food filename="map/config/animalFood.xml" />
</animals>
...
<motionPathEffects filename="map/effects/motionPathEffects.xml" />
<fillTypes filename="map/config/maps_fillTypes.xml" />
<fruitTypes filename="map/config/maps_fruitTypes.xml" />
<densityMapHeightTypes filename="map/config/maps_densityMapHeightTypes.xml" />
```

### 5.2. `FS25_Granja_Bonita_V4/map/config/maps_fruitTypes.xml`
- Registra `map/foliage/wheat/wheat.xml`, `map/foliage/blackbean/blackbean.xml`, `map/foliage/napier/napier.xml` y `map/foliage/peanut/peanut.xml` (junto con `$data/foliage/meadow/meadowUS/meadowUS.xml` y los cultivos base).
- Se eliminó la entrada `maps/foliage/coffee/coffee.xml`.
- Añade `BLACKBEAN`, `NAPIER` y `PEANUT` a las categorías de maquinaria (`GRAINHEADER`, `MAIZEHEADER`, `MAIZECUTTER`, `DIRECTCUTTER`, `SOWINGMACHINE`, `PLANTER`, `MOWER`, `COMBINE_MOWER`) y a los convertidores (`FORAGEHARVESTER`, `MOWER`, `COMBINE_MOWER`).

### 5.3. `FS25_Granja_Bonita_V4/map/config/maps_fillTypes.xml`
- Define los `fillTypes`: `BLACKBEAN`, `PEANUT`, `NAPIER`, `PEANUT_CUT` y `PEANUT_BAG` con rutas `map/fillPlanes/...` y `map/pallets/peanutBagPallet/peanutBagPallet.xml`.
- **Corrección de clave `l10n` en `BLACKBEAN`:** En el archivo original de Recanto da Alvorada había un error tipográfico (`title="$l10n_fillType_backbean"` sin la letra `l`), lo que causaba el mensaje `MISSING 'FILLTYPE_BACKBEAN'` en el menú del juego. Se corrigió a `title="$l10n_fillType_blackbean"`.
- Se eliminaron las definiciones y referencias a `COFFEE`, `COFFEE_BAG`, `ROASTED_COFFEE` y `PREMIUM_ROASTED_COFFEE` de Recanto da Alvorada para no interferir con el café oficial del DLC (`pdlc_beansAndAlpacasPack`).
- Se incluyeron `PEANUT` y `BLACKBEAN` en `BULK`, `COMBINE`, `AUGERWAGON`, `TRAINWAGON`, `SHOVEL`, `FARMSILO`, `LOADINGVEHICLE`, `SELLINGSTATION_FIELDFRUITS`, y `PEANUT_BAG` en `PRODUCT` y `SELLINGSTATION_PRODUCTSFOOD`, permitiendo que los silos y puntos de venta existentes en Granja Bonita los acepten automáticamente.

### 5.4. `FS25_Granja_Bonita_V4/map/config/maps_densityMapHeightTypes.xml`
- Añade soporte de volcado en el suelo (`densityMapHeightType`) para `BLACKBEAN`, `PEANUT`, `PEANUT_CUT` y los cordones de cereal (`WHEAT_CUT`, `BARLEY_CUT`, `OAT_CUT`, `CANOLA_CUT`, `SOYBEAN_CUT`).
- Se eliminó la entrada `COFFEE`.

### 5.5. `FS25_Granja_Bonita_V4/map/config/animalFood.xml` y `storeItems.xml`
- En `animalFood.xml`: se añadió `PEANUT` dentro del grupo de proteína (`$l10n_fillTypeGroup_protein`) para la alimentación de cerdos (`PIG`).
- En `storeItems.xml`: se registraron las 3 fábricas y el palet de bolsas de maní:
  ```xml
  <storeItem xmlFilename="map/placeables/factory/peanutbag/dairy.xml"/>
  <storeItem xmlFilename="map/placeables/factory/dairy/dairy.xml"/>
  <storeItem xmlFilename="map/placeables/factory/grainFlourMill/grainFlourMill.xml"/>
  <storeItem xmlFilename="map/pallets/peanutBagPallet/peanutBagPallet.xml"/>
  ```

### 5.6. Traducciones en `FS25_Granja_Bonita_V4/modDesc.xml`
Se agregaron las entradas `<l10n>` en inglés (`en`), español (`es`), alemán (`de`) y portugués brasileño (`br`) para:
- `fillType_blackbean` y `fillType_backbean` (*Frijol Negro*)
- `fillType_peanut` (*Maní*)
- `fillType_peanutbag` (*Bolsa de Maní*)
- `fillType_napier` (*Pasto Elefante*)
- `storeItem_peanutBagsPallet` (*Palet de Bolsas de Maní*)
- `shopItem_peanutbag` (*Fábrica de Maní Procesado*)
