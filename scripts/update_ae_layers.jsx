/**
 * update_ae_layers.jsx
 *
 * Growlabs 2026 Dünya Kupası Video Makinesi — ExtendScript Şablonu
 *
 * Bu dosya ae_bridge.py tarafından dinamik olarak üretilir ve doldurulur.
 * Doğrudan çalıştırılmaz; aşağıdaki parametreler runtime'da enjekte edilir:
 *   - PROJECT_PATH : .aep dosya yolu
 *   - COMP_NAME    : Hedef kompozisyon adı
 *   - TEXT_UPDATES : Katman adı → yeni metin çiftleri (JSON dizisi)
 *
 * Özellikler:
 *   - Motion Blur: Shutter Angle 360°, Phase -180° (Anayasa kuralı)
 *   - Tüm katmanlar için motion blur aktif
 *   - Belirtilen text katmanlarını günceller
 *   - Projeyi kaydedip AE'yi kapatır (headless mod)
 */

// ---- Parametreler (ae_bridge.py tarafından doldurulur) ----
var PROJECT_PATH = "{{PROJECT_PATH}}";
var COMP_NAME    = "{{COMP_NAME}}";
var TEXT_UPDATES = {{TEXT_UPDATES}};  // [{layer: "TEAM_HOME", text: "Türkiye"}, ...]

// ---- Projeyi aç ----
var projectFile = new File(PROJECT_PATH);
if (!projectFile.exists) {
    alert("ae_bridge: Proje dosyası bulunamadı: " + PROJECT_PATH);
    app.quit();
}

app.open(projectFile);
var proj = app.project;

// ---- Hedef kompozisyonu bul ----
var comp = null;
for (var i = 1; i <= proj.numItems; i++) {
    if (proj.item(i) instanceof CompItem && proj.item(i).name === COMP_NAME) {
        comp = proj.item(i);
        break;
    }
}

// Bulunamazsa ilk kompozisyonu kullan
if (comp === null) {
    for (var i = 1; i <= proj.numItems; i++) {
        if (proj.item(i) instanceof CompItem) {
            comp = proj.item(i);
            break;
        }
    }
}

if (comp === null) {
    alert("ae_bridge: Hiç kompozisyon bulunamadı. İşlem iptal.");
    app.quit();
}

// ---- Motion Blur: Shutter Angle 360° (Growlabs Anayasası) ----
comp.motionBlur = true;
comp.shutterAngle = 360;
comp.shutterPhase = -180;

// Tüm katmanlar için motion blur aktif et
for (var li = 1; li <= comp.numLayers; li++) {
    try {
        comp.layer(li).motionBlur = true;
    } catch (e) {}
}

// ---- Text Katmanı Güncellemeleri ----
for (var ui = 0; ui < TEXT_UPDATES.length; ui++) {
    var update = TEXT_UPDATES[ui];
    var updated = false;

    for (var li = 1; li <= comp.numLayers; li++) {
        var layer = comp.layer(li);
        if (layer.name === update.layer) {
            try {
                var srcText = layer.property("Text").property("Source Text");
                var doc = srcText.value;
                doc.text = update.text;
                srcText.setValue(doc);
                updated = true;
            } catch (e) {
                // Text katmanı değil, atla
            }
            break;
        }
    }

    if (!updated) {
        $.writeln("ae_bridge [UYARI]: Katman bulunamadı: " + update.layer);
    }
}

// ---- Kaydet ve Kapat ----
proj.save();
app.quit();
