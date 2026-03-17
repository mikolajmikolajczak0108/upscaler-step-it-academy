# Upscaler

Desktop AI video and image upscaler project built together with students at Step IT Academy.

Desktopowa aplikacja do upscalingu video i obrazow z prostym GUI, kolejka zadan i w pelni lokalnym pipeline AI.

- `FFmpeg only`: dziala od razu na tej maszynie bez dodatkowych modeli.
- `Local AI VSR`: uzywa lokalnych binarek `Real-ESRGAN-ncnn-vulkan` i `rife-ncnn-vulkan` z oficjalnych release'ow, bez chmury i bez fallbackowego braku modeli.
- `Image Lift / Batch`: robi pojedynczy lub hurtowy upscale obrazow, plus korekcje koloru i podstawowy lifting.

## Co potrafi teraz

- analiza wejscia przez `ffprobe`
- pasek postepu aktywnego joba + przewidywany czas do konca
- profile renderu `720p/1080p/1440p/2160p`
- target fps `24/30/48/60`
- filtry video: `denoise`, `deband`, `temporal VSR`, `sharpen`, `grain`, `brightness`, `contrast`, `saturation`, `gamma`
- skala klasyczna `ffmpeg`
- lokalny AI upscale przez `Real-ESRGAN`
- AI frame interpolation przez `RIFE`
- batch i single-image upscale
- korekty obrazu: `autocontrast`, `brightness`, `contrast`, `saturation`, `gamma`, `denoise`, `sharpen`
- output obrazow do `png`, `jpg`, `webp`
- queue, cancel, logi, detekcja narzedzi, downloader narzedzi
- encode `h264_nvenc`, `hevc_nvenc`, `libx264`

## Uruchomienie

W PowerShell:

```powershell
.\run.ps1
```

Skrypt:

1. tworzy lokalne `.venv`
2. instaluje `PySide6`
3. uruchamia aplikacje

## Uzycie

1. Wybierz input video.
2. Wybierz output file.
3. Ustaw preset albo zmien parametry recznie.
4. Jesli chcesz lokalny AI VSR dla video, kliknij `Download` przy `Real-ESRGAN` i `RIFE`.
5. Wybierz preset `Local AI VSR 4K60` albo `AI Restore 4K60`.
6. Kliknij `Add To Queue`, potem `Start Queue`.

## Uzycie obrazow

1. W sekcji `Image Upscale / Batch` wybierz plik albo folder.
2. Wybierz output folder.
3. Wybierz preset, np. `Photo Lift 4x` albo `Gentle Batch 2x`.
4. Dla AI wybierz `Real-ESRGAN`; dla klasycznego upscale mozna zostawic `Pillow Lanczos`.
5. Kliknij `Add Image Job`, potem `Start Queue`.

## Uwagi

- Pipeline AI jest batchowy i zapisuje klatki do katalogu tymczasowego w `%TEMP%\Upscaler`.
- Dla materialow `4:3` target `2160p` zachowuje proporcje, czyli wynik nie musi miec `3840x2160`; moze wyjsc np. `2880x2160`.
- `RIFE` po pobraniu dziala od razu, bo model jest w paczce release.
- Aplikacja pobiera sprawdzony oficjalny release `Real-ESRGAN-ncnn-vulkan` z kompletnym `models/`, wiec lokalny AI upscale dziala od razu po instalacji narzedzia.
- Najbezpieczniejszy preset startowy to `Fast FFmpeg 1080p60`, `Fast FFmpeg 720p30` albo `AI Lift 1080p30` zalezne od materialu.
