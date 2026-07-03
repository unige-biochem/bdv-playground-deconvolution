# Deconvolution Workflow

Created by: **Nicolas Chiaruttini** — Dudin Lab

GitLab: https://gitlab.unige.ch/unige-biochem/dudin-lab/deconvolution-workflow

> **Status:** early draft / proof of concept. The goal of this repo is to
> assess whether **deconvolution** improves downstream image-analysis
> workflows. This repo intentionally covers the **deconvolution step only** —
> the downstream analysis lives elsewhere.

---

## Goal

We were given raw **CZI** files (4-channel widefield fluorescence). The question
is simple:

> Does deconvolving the raw data measurably help the downstream workflow?

If it does, the next step is to **streamline** the process (see [Roadmap](#roadmap)).
The deconvolution workflow will soon be distributed via the
**UNIGE-Biochem update site** in [ImageJ / Fiji](https://fiji.sc/).

---

## Demo dataset

A single raw CZI acquisition is used as the test case. Acquisition metadata
(extracted from the CZI):

### Optics & detector

| Property           | Value                                              |
|--------------------|----------------------------------------------------|
| Objective          | LD C-Apochromat 40x/1.1 W Korr UV VIS IR           |
| Immersion          | Water                                              |
| NA                 | 1.1                                                |
| Nominal mag.       | 40×                                                |
| Working distance   | 600 µm                                             |
| Detector           | Hamamatsu camera (1× camera adapter)               |
| Acquisition mode   | Widefield / Epifluorescence                        |

### Voxel size

| Axis      | Size    |
|-----------|---------|
| XY pixel  | 162 nm  |
| Z step    | 500 nm  |

### Channels

| Channel  | Fluor           | Excitation | Emission |
|----------|-----------------|-----------:|---------:|
| 0:0      | Alexa Fluor 647 | 650 nm     | 671 nm   |
| 0:1      | Alexa Fluor 568 | 579 nm     | 603 nm   |
| 0:2      | Alexa Fluor 488 | 499 nm     | 520 nm   |
| 0:3      | DAPI            | 351 nm     | 464 nm   |

---

## Point Spread Function (PSF)

No **empirical PSF** (e.g. from sub-resolution beads) was available for this
dataset, so a **theoretical PSF** was generated with the
[PSF Generator](https://bigwww.epfl.ch/algorithms/psfgenerator/) Fiji plugin
using the **Born & Wolf 3D optical model**.

![Theoretical PSF generation parameters](PSF-Theoretical-Generated.png)

### Parameters used

| Parameter                     | Value                     |
|-------------------------------|---------------------------|
| Optical model                 | Born & Wolf 3D            |
| Refractive index (immersion)  | 1.33 (water)              |
| Accuracy                      | Good                      |
| Wavelength                    | 610 nm                    |
| Numerical aperture (NA)       | 1.1                       |
| Pixel size XY                 | 162 nm                    |
| Z step                        | 500 nm                    |
| Output size (X × Y × Z)       | 256 × 256 × 65            |
| Output type / display         | 32-bit, Linear, Fire LUT  |

Resulting theoretical resolution (reported by the plugin):

| Metric   | Value     |
|----------|-----------|
| FWHM XY  | 338.3 nm  |
| FWHM Z   | 1008.3 nm |

> **Note:** the immersion refractive index (1.33) matches the water-immersion
> objective, and the NA / voxel size match the acquisition. A single
> representative emission wavelength (**610 nm**) was used to generate **one**
> PSF applied to the data — see [open questions](#open-questions).

---

## Deconvolution

The deconvolution itself is the **not-yet-released** part of the pipeline:

- **Tool:** [BigDataViewer Playground](https://bigdataviewer-playground-documentation.readthedocs.io/en/latest/processing_images/deconvolution.html) **Tiled Multi-GPU deconvolution** in ImageJ / Fiji
- **Regularization:** none
- **Iterations:** 120 deconvolution steps
- **PSF:** the theoretical PSF above, stored beforehand
- **Output:** result resaved into the `deconvolution` folder of the dataset

---

## Results

Qualitative comparison of the raw vs. deconvolved demo dataset.

### Z projection (lateral view)

| Raw | Deconvolved |
|-----|-------------|
| ![Z projection — raw](ZProjection-Raw.png) | ![Z projection — deconvolved](ZProjection-Deconvolved.png) |

### Axial cross-section (Z view)

The axial view is where widefield blur is worst and where deconvolution is
expected to help most.

| Raw | Deconvolved |
|-----|-------------|
| ![Cross-section — raw](CrossSection-Raw.png) | ![Cross-section — deconvolved](CrossSection-Deconvolved.png) |

---

## What has been tested

- [x] Extracted acquisition metadata from a raw CZI
- [x] Generated a theoretical PSF (Born & Wolf) matching the acquisition optics
- [x] Ran GPU deconvolution (BDV Playground, 120 iterations, no regularization)
- [x] Produced qualitative before/after comparisons (lateral + axial)
- [ ] Quantitative evaluation of the improvement
- [ ] Confirmation that deconvolution helps the **downstream** workflow

---

## Roadmap

1. **Evaluate downstream impact** — feed deconvolved data into the downstream
   workflow and check whether results improve over raw data.
2. **If it helps → streamline** the process into (one-click-ish
   pipeline ? make cluster compatible ? controlled via Python ?) pre-crop the data (plenty of useless noise here)

### Open questions

- **Quantitative metric.** What is the right metric to declare the workflow
  "improved" (resolution, SNR, downstream segmentation accuracy)?

---

## Data location

Raw and deconvolved data are stored outside the repo:

```
F:\user-projects\data\dudin-lab\deconvolution-workflow
```

(Also linked via `Shared_Folder.lnk`.)
