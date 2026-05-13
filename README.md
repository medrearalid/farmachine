# FARMACHINE

**FARMACHINE**, Metin2 için geliştirilmiş Python tabanlı bir görüntü işleme ve yapay zeka destekli farming otomasyon yazılımıdır. YOLO tabanlı nesne tespiti, Google Gemini ile CAPTCHA çözümü ve çift istemci (dual-client) desteği sunar.

> **Uyarı:** Bu yazılım yalnızca eğitim ve araştırma amacıyla geliştirilmiştir. Oyun kurallarına ve kullanım şartlarına uygun şekilde kullanmak kullanıcının sorumluluğundadır.

---

## İçindekiler

- [Özellikler](#özellikler)
- [Sistem Gereksinimleri](#sistem-gereksinimleri)
- [Kurulum](#kurulum)
- [Konfigürasyon](#konfigürasyon)
- [Çalıştırma](#çalıştırma)
- [Kullanım Kılavuzu](#kullanım-kılavuzu)
- [Proje Yapısı](#proje-yapısı)
- [Sık Karşılaşılan Sorunlar](#sık-karşılaşılan-sorunlar)

---

## Özellikler

- **YOLO Tabanlı Nesne Tespiti** — Metin taşlarını gerçek zamanlı olarak tespit eder (`metin2_yolo26.onnx` / `best.pt`)
- **Çift İstemci Desteği** — İki Metin2 penceresini aynı anda yönetir, round-robin zamanlayıcı ile akıllı geçiş yapar
- **CAPTCHA Çözümü** — Google Gemini API ile otomatik CAPTCHA tespiti ve çözümü
- **Multi-Target Queue** — Birden fazla Metin taşını sıraya alarak art arda tıklama
- **Smart Pathing** — Euclidean mesafeye göre en yakın Metin taşına önce saldırır
- **Otomatik Beceri Kullanımı** — Cooldown tabanlı beceri/buff otomasyonu (Saman/Savaşçı profilleri)
- **ROI Maskeleme** — Oyun UI bölgelerini inferans dışında tutarak yanlış tespitleri önler
- **Session Manager** — Circadian macro-break: 60-120 dk çalışma, 5-10 dk otomatik mola
- **Frameless Modern GUI** — PySide6 + QML tabanlı, sürüklenebilir başlık çubuğu, Light/Dark tema
- **Overlay** — Oyun üzerine YOLO tespit kutularını gösterir
- **Anti-Takılma** — WASD burst ile karakter sıkışma durumundan çıkarma

---

## Sistem Gereksinimleri

| Gereksinim | Minimum |
|---|---|
| İşletim Sistemi | Windows 10/11 (64-bit) |
| Python | 3.11+ |
| GPU (opsiyonel) | CUDA destekli NVIDIA GPU (ONNX runtime ile CPU da çalışır) |
| RAM | 8 GB |
| Ekstra Sürücü | [Interception Driver](https://github.com/oblitum/Interception) (düşük seviye mouse kontrolü için) |

---

## Kurulum

### 1. Repoyu Klonlayın

```bash
git clone https://github.com/medrearalid/farmachine.git
cd farmachine
```

### 2. Python Sanal Ortam Oluşturun

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Bağımlılıkları Yükleyin

```bash
pip install -r requirements.txt
```

> Eğer `torch` kurulumunda sorun yaşarsanız önce PyTorch'u manuel kurun:
> ```bash
> pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
> pip install -r requirements.txt
> ```

### 4. Interception Driver Kurulumu (Zorunlu)

Düşük seviye mouse/klavye kontrolü için Interception driver gereklidir.

1. [Interception releases](https://github.com/oblitum/Interception/releases) sayfasından `install-interception.exe` indirin.
2. Yönetici (Administrator) olarak çalıştırın ve kurun.
3. Bilgisayarı yeniden başlatın.

### 5. Konfigürasyon Dosyasını Oluşturun

```bash
copy config.example.json config.json
```

`config.json` dosyasını açın ve `global.captcha.api_key` alanına kendi **Google Gemini API** anahtarınızı girin:

```json
"captcha": {
    "enabled": true,
    "api_key": "AIzaSy...",
    "selected_model": "gemini-2.5-flash-lite"
}
```

> **Gemini API Anahtarı:** [Google AI Studio](https://aistudio.google.com/app/apikey) üzerinden ücretsiz oluşturabilirsiniz.

### 6. Mouse ID Dosyasını Oluşturun

`data/mouse_id.txt` dosyasını oluşturun ve içine Interception'ın size atadığı mouse device ID'yi yazın (genellikle 11):

```
11
```

Mouse ID'nizi bulmak için Interception'ın test aracını kullanabilirsiniz.

---

## Konfigürasyon

`config.json` iki istemci (`client_1`, `client_2`) ve global ayarlar içerir.

### Temel Ayarlar

#### `general` Bloğu

| Ayar | Açıklama | Varsayılan |
|---|---|---|
| `auto_attack` | Otomatik saldırı aktif/pasif | `true` |
| `auto_loot` | Otomatik item toplama | `false` |
| `auto_revive` | Ölünce otomatik yeniden doğma | `true` |
| `captcha_solver` | CAPTCHA çözümünü aktif et | `true` |
| `combat_timeout` | Savaş timeout süresi (saniye) | `120` |
| `anti_stuck` | Anti-takılma WASD burst | `true` |
| `miss_timeout` | Vuruş timeout (saniye) | `2` |

#### `vision` Bloğu

| Ayar | Açıklama |
|---|---|
| `model_path` | Kullanılacak YOLO model dosyası |
| `confidence_threshold` | Template matching eşiği |
| `yolo_confidence` | YOLO tespit güven eşiği |
| `mask_regions` | UI bölgelerini maskele (YOLO inferansından hariç tutar) |

#### `combat` Bloğu

| Ayar | Açıklama | Varsayılan |
|---|---|---|
| `multi_target_queue_size` | Aynı anda sıraya alınacak maksimum hedef sayısı | `5` |
| `reachable_distance_px` | Maksimum erişilebilir hedef mesafesi (piksel) | `420` |
| `deferred_queue_click_delay_sec` | İlk tıklamadan sonra queue tıklamaları için bekleme | `2` |

#### `skills` Bloğu

Beceri kullanımı için cooldown ve tuş ataması yapılır:

```json
"skill_1": {
    "key": "3",
    "cooldown": 63,
    "enabled": true
}
```

#### `global.captcha` Bloğu

| Ayar | Açıklama |
|---|---|
| `enabled` | CAPTCHA çözümünü aktif et |
| `api_key` | Google Gemini API anahtarı |
| `selected_model` | Kullanılacak Gemini modeli |

Desteklenen modeller:
- `gemini-2.5-flash-lite` (önerilen, hızlı)
- `gemini-2.5-flash`
- `gemini-2.0-flash`
- `gemini-1.5-flash`

---

## Çalıştırma

### Yöntem 1 — Batch Dosyası (Kolay)

```bash
run_bot.bat
```

### Yöntem 2 — Python ile Manuel

```bash
venv\Scripts\activate
python main_gui_qml.py
```

---

## Kullanım Kılavuzu

### Pencere Bağlama (Attach)

1. Metin2 istemcilerini başlatın.
2. Uygulamayı açın.
3. **Settings** sekmesine gidin.
4. **Client 1** için `Attach Window` butonuna tıklayın; açık Metin2 pencerelerinden birini seçin.
5. Çift istemci kullanıyorsanız **Client 2** için aynı işlemi yapın.

### Botu Başlatma

1. Sol paneldeki **START** butonuna tıklayın.
2. Bot otomatik olarak farming döngüsüne girer.
3. Durdurmak için **STOP** butonuna tıklayın.

### Beceri Profili Seçme

**Skills** sekmesinden karakter sınıfına uygun profil seçin:
- `saman_ejderha` — Ejderha Şaman
- `saman_iyilestirme` — İyileştirme Şaman
- `savasci_bedensel` — Bedensel Savaşçı

Her beceri için tuş bağlaması ve cooldown süresini ayarlayın.

### ROI Maskesi Ekleme (Opsiyonel)

Oyun içi UI elementleri (minimap, item bar vb.) yanlış tespit tetikleyebilir. Bunları maskelemek için:

1. **Settings → Mask UI Regions** butonuna tıklayın.
2. Oyun penceresi üzerinde maskelemek istediğiniz bölgeleri sürükle-bırak ile seçin.
3. **Enter** ile kaydedin, **Escape** ile iptal edin.

### Session Manager (Otomatik Mola)

Bot, insansı davranış için otomatik mola sistemi içerir:
- **Çalışma süresi:** 60-120 dakika (rastgele)
- **Mola süresi:** 5-10 dakika (rastgele)
- Mola sırasında sol panelde `Status: ON BREAK (X mins left)` görüntülenir.

---

## Proje Yapısı

```
farmachine/
├── main_gui_qml.py          # Ana giriş noktası
├── config.example.json      # Konfigürasyon şablonu
├── requirements.txt         # Python bağımlılıkları
├── run_bot.bat              # Windows başlatıcı
│
├── core/
│   ├── bot_engine.py        # Tek istemci state machine
│   ├── dual_client_engine.py # Çift istemci orkestrasyon motoru
│   ├── vision_ai.py         # YOLO nesne tespiti
│   ├── window_capture.py    # MSS ekran yakalama
│   ├── process_manager.py   # Pencere bulma ve context switch
│   ├── skill_manager.py     # Beceri/buff otomasyonu
│   ├── config_manager.py    # JSON okuma/yazma
│   ├── ai/
│   │   └── gemini_client.py # Google Gemini API istemcisi
│   ├── drivers/
│   │   └── interception_handler.py # Düşük seviye input
│   └── vision/
│       └── vision.py        # Template matching (HP bar, CAPTCHA vb.)
│
├── ui/
│   ├── backend_bridge.py    # PySide6 ↔ QML köprüsü
│   ├── frameless_window.py  # Özel başlık çubuğu + QSS
│   ├── overlay_pyside6.py   # Oyun üstü YOLO overlay
│   ├── signals_pyside6.py   # Thread-safe sinyal katmanı
│   └── qml/                 # Qt Quick arayüz dosyaları
│
├── models/
│   ├── metin2_yolo26.onnx   # Birincil tespit modeli
│   ├── best.pt              # İkincil model (PyTorch)
│   └── yolo26.pt            # Yedek model
│
└── assets/
    └── skills/              # Beceri ikonları (template matching)
```

---

## State Machine (Bot Akışı)

```
SEARCHING → BATCH_QUEUEING → MOVING_TO_TARGET → EXECUTING_QUEUE/COMBAT → QUEUE_WAIT → SEARCHING

CAPTCHA dalı:
SEARCHING → SOLVING_CAPTCHA → SEARCHING
```

| Durum | Açıklama |
|---|---|
| `SEARCHING` | Ekranda Metin taşı arıyor |
| `BATCH_QUEUEING` | Birden fazla hedefi sıraya alıyor |
| `MOVING_TO_TARGET` | Hedefe doğru hareket ediyor |
| `EXECUTING_QUEUE` | Sıradaki hedeflere tıklanıyor |
| `COMBAT` | Savaş süresince bekliyor |
| `QUEUE_WAIT` | Queue handoff grace bekleniyor |
| `SOLVING_CAPTCHA` | CAPTCHA çözülüyor |
| `LOOT` | Item toplanıyor |

---

## Sık Karşılaşılan Sorunlar

### Bot başlamıyor / pencere bulunamıyor
- Metin2'nin yönetici (Administrator) haklarıyla çalıştığından emin olun.
- Uygulamayı da yönetici haklarıyla çalıştırın.

### Mouse hareket etmiyor
- Interception driver'ın kurulu ve aktif olduğunu kontrol edin.
- `data/mouse_id.txt` dosyasındaki device ID'yi kontrol edin.
- Bilgisayarı yeniden başlatın.

### CAPTCHA çözülemiyor
- `config.json` içindeki `api_key` değerinin doğru olduğunu kontrol edin.
- [Google AI Studio](https://aistudio.google.com/app/apikey) üzerinden anahtarınızın aktif olduğunu doğrulayın.
- `selected_model` değerini `gemini-2.0-flash` olarak değiştirip deneyin.

### YOLO hiç tespit yapmıyor
- `models/` klasöründe `metin2_yolo26.onnx` veya `best.pt` dosyasının bulunduğundan emin olun.
- Settings → Client 1 → `yolo_confidence` değerini düşürün (örn. `0.5`).

### `ImportError: No module named ...`
- Sanal ortamın aktif olduğundan emin olun: `venv\Scripts\activate`
- Bağımlılıkları yeniden yükleyin: `pip install -r requirements.txt`

---

## Lisans

Telif Hakkı (c) 2026 [medrearalid]
Tüm hakları saklıdır.

Kaynak kodu, belgeler ve ilişkili dosyalar (bundan böyle "Yazılım" olarak anılacaktır) dahil olmak üzere bu yazılım, telif hakkı sahibinin (geliştiricinin) özel mülkiyetindedir. 

Bu Yazılıma erişerek, kodları derleyerek veya kullanarak aşağıdaki hüküm ve koşulları peşinen ve açıkça kabul etmiş sayılırsınız:

1. SADECE KİŞİSEL KULLANIM
Bu Yazılımı yalnızca kişisel, özel ve eğitim/araştırma amaçlı kullanmak üzere sınırlı, münhasır olmayan, devredilemez ve iptal edilebilir bir lisans hakkına sahipsiniz.

2. KESİNLİKLE TİCARİ KULLANIM YASAĞI
Bu Yazılımı, tamamen veya kısmen HİÇBİR ticari amaçla kullanamazsınız. Bu kısıtlama, bunlarla sınırlı olmamak üzere aşağıdakileri içerir:
- Yazılımı veya derlenmiş halini satmak, kiralamak veya üzerinden abonelik ücreti talep etmek.
- Yazılımı başka bir ticari ürünün veya hizmetin parçası haline getirmek.

3. YENİDEN DAĞITIM VEYA PAYLAŞIM YASAĞI
Telif hakkı sahibinin önceden yazılı ve açık izni olmaksızın; Yazılımı (kaynak kodunu veya derlenmiş .exe/.dll dosyalarını) değiştirilmiş veya orijinal haliyle herhangi bir genel veya özel platformda (forumlar, Discord sunucuları, diğer GitHub repoları vb.) KOPYALAYAMAZ, DAĞITAMAZ, PAYLAŞAMAZ VEYA BARINDIRAMAZSINIZ.

4. DAĞITIM AMACIYLA TÜRETİLMİŞ ESER YASAĞI
Yazılım kodlarını yalnızca kendi bilgisayarınızdaki kişisel kullanımınız için değiştirebilir ve geliştirebilirsiniz. Ancak, bu Yazılımın kodlarına dayanarak oluşturduğunuz veya modifiye ettiğiniz yeni çalışmaları (sürümleri/çatallamaları) başkalarıyla PAYLAŞAMAZ, SATAMAZ veya DAĞITAMAZSINIZ.

SORUMLULUK REDDİ BEYANI (DISCLAIMER)
YAZILIM, TİCARİ ELVERİŞLİLİK VEYA BELİRLİ BİR AMACA UYGUNLUK GARANTİLERİ DAHİL ANCAK BUNLARLA SINIRLI OLMAMAK ÜZERE, AÇIK VEYA ZIMNİ HİÇBİR GARANTİ OLMAKSIZIN "OLDUĞU GİBİ" SAĞLANMAKTADIR. 

HİÇBİR DURUMDA YAZAR VEYA TELİF HAKKI SAHİBİ; YAZILIMIN KULLANIMINDAN VEYA YAZILIMLA BAĞLANTILI OLARAK ORTAYA ÇIKAN HİÇBİR ZARARDAN, BİLGİSAYAR ARIZALARINDAN, OYUN HESABI YASAKLAMALARINDAN (BAN) VEYA DİĞER YÜKÜMLÜLÜKLERDEN SORUMLU TUTULAMAZ. TÜM RİSK KULLANICIYA AİTTİR.
