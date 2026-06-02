# El Hareketiyle Bilgisayar Kontrolu

Kameradan el hareketlerini algilayarak Windows masaustunde fare hareketi, tiklama ve kaydirma yapmayi saglayan Python projesi.

## Proje Ozeti

Bu uygulama OpenCV ile kameradan goruntu alir, MediaPipe ile el noktalarini algilar ve PyAutoGUI ile sistem faresini kontrol eder. Kamera okuma islemi ayri bir thread uzerinde calistigi icin gecikme ve kare birikmesi azaltilmistir.

## Ozellikler

- Isaret parmagi ile imlec kontrolu
- Bas parmak + isaret parmagi ile sol tik
- Bas parmak + orta parmak ile sag tik
- Bas parmak + yuzuk parmagi ile cift tik
- Isaret + orta parmak hareketiyle yukari/asagi kaydirma
- Yumruk hareketiyle duraklatma/devam ettirme
- Dusuk cozunurluk ve hafif MediaPipe modeliyle performans odakli calisma
- Temiz kapanma icin kamera ve OpenCV kaynak yonetimi

## Kullanilan Teknolojiler

- Python
- OpenCV
- MediaPipe
- PyAutoGUI
- NumPy

## Kurulum

Python 3.10 veya 3.11 kurulu olmalidir.

```bat
install.bat
```

Notlar:
- `app.py` dosyasini tek basina calistirmayin. Her zaman `run.bat` kullanin (sanal ortami dogru sekilde kullanir).
- Proje yolu OneDrive altindaysa veya yolda Turkce/ozel karakter varsa, `install.bat` sanal ortami otomatik olarak
  `%LOCALAPPDATA%\\ElKontrolu\\.venv` altina kurar ve konumu `.venv_path` dosyasina yazar. Bu, MediaPipe'in bazi
  sistemlerde gordugu `.binarypb` dosya yolu hatalarini engellemeye yardimci olur.

## Calistirma

```bat
run.bat
```

Hata detaylarini dosyaya yazdirmak icin:

```bat
run_debug.bat
```

## EXE Olusturma

```bat
build_exe.bat
```

Olusan uygulama `dist\ElKontrolu\ElKontrolu.exe` konumunda yer alir.

## Kullanim

- Kameraya elini goster.
- Isaret parmagini hareket ettirerek imleci kontrol et.
- Bas parmak + isaret parmagi: sol tik.
- Bas parmak + orta parmak: sag tik.
- Bas parmak + yuzuk parmagi: cift tik.
- Isaret ve orta parmak acikken elini yukari/asagi hareket ettir: kaydirma.
- Yumruk yap: duraklat/devam ettir.
- Cikis icin kamera penceresindeyken `Q` tusuna bas.

## Kod Yapisi

- `ControlConfig`: Uygulama ayarlarini tek yerde toplar.
- `CameraStream`: Kamerayi ayri thread uzerinde okur.
- `GestureMouse`: El hareketlerini fare komutlarina cevirir.
- `draw_hud`: Kamera penceresine durum ve FPS bilgisini yazar.
- `main`: Kamera, MediaPipe ve kontrol dongusunu yonetir.

## Kod Mantigi (Adim Adim)

1. `CameraStream` kamera karelerini arka planda okur ve her zaman en yeni kareyi saklar.
2. `main` dongusu bu en yeni kareyi alir, aynalar (flip) ve MediaPipe icin RGB'ye cevirir.
3. MediaPipe `Hands`, kare uzerinde 21 adet el noktasi (landmark) uretir.
4. `GestureMouse`, isaret parmaginin uc noktasini ekrana map eder ve `pyautogui.moveTo()` ile imleci hareket ettirir.
5. Basparmak + belirli parmak ucu yaklasinca tiklama (sol/sag/cift) tetiklenir.
6. Isaret + orta parmak acikken elin yukari/asagi hareketi kaydirma olarak yorumlanir.
7. Yumruk (tum parmaklar kapali) hareketi kontrolu duraklatir/devam ettirir.
8. Ekrana HUD (durum + FPS) basilir; `Q` ile cikis yapilir ve kamera/MediaPipe kaynaklari temiz kapatilir.

## Performans Notlari

Varsayilan ayarlar hizli ve akici kontrol icin ayarlanmistir. Imlec kucuk hareketlerde titremeyi azaltir, buyuk hareketlerde ise ele daha cabuk yetisir. Imlec fazla hassas gelirse `cursor_deadzone_px` veya `movement_smoothing` biraz artirilabilir; hala agir gelirse `fast_movement_smoothing` veya `max_cursor_step_px` artirilabilir. Daha dusuk sistemlerde `camera_width`, `camera_height` veya `camera_fps` degerleri azaltilabilir.

## Sik Hatalar (Cozum)

`ModuleNotFoundError: No module named 'cv2'`
- Sebep: `app.py` sistem Python'u ile calistirilmistir (venv degil).
- Cozum: Once `install.bat`, sonra `run.bat` calistirin.

`FileNotFoundError: ... mediapipe/... .binarypb`
- Sebep: MediaPipe'in model dosyasi yolu OneDrive/ozel karakter iceren dizinlerde sorun cikarabiliyor.
- Cozum: `install.bat` dosyasini tekrar calistirin (venv'i `%LOCALAPPDATA%` altina tasir) veya projeyi ASCII bir yola
  tasiyin (ornek: `C:\\ElKontrolu`).

## Guvenlik ve Gizlilik

- Tum islemler tamamen yerel olarak yapilir.
- Kamera goruntusu hicbir sekilde kaydedilmez veya saklanmaz.
- Hicbir veri internete gonderilmez.
- Kamera yalnizca uygulama acikken ve kullanici izniyle calisir.
- Uygulama kapatildiginda kamera otomatik olarak kapanir.
