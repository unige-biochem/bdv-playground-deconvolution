# Deconvolution Workflow

Created by: **Nicolas Chiaruttini** — Dudin Lab

GitLab: https://gitlab.unige.ch/unige-biochem/dudin-lab/deconvolution-workflow

> **Status:** early draft / proof of concept. The goal of this repo is to
> assess whether **deconvolution** improves downstream image-analysis
> workflows. This repo intentionally covers the **deconvolution step only** —
> the downstream analysis lives elsewhere.

---

## Goal

We were given raw **CZI** files (multi-channel widefield fluorescence). The
question is simple:

> Does deconvolving the raw data measurably help the downstream workflow?

If it does, the next step is to **streamline** the process (see [Roadmap](#roadmap)).
The deconvolution workflow will soon be distributed via the
**UNIGE-Biochem update site** in [ImageJ / Fiji](https://fiji.sc/).

---

## Datasets

The workflow is being tested on several datasets. Each has its own optics, voxel
size and PSF — the per-dataset pages below record all of that:

| Dataset | Sample | Z step | Details |
|---------|--------|-------:|---------|
| **Paula** | 4-channel widefield fluorescence | 500 nm | [datasets/paula.md](datasets/paula.md) |
| **Baukje** | *Dictyostelium* + ConA (widefield) | 330 nm | [datasets/baukje.md](datasets/baukje.md) |

The **optics and deconvolution settings are shared** across datasets; the main
difference is the **axial (Z) sampling**, which changes the Z step used to
generate the theoretical PSF. See each page for the exact acquisition metadata,
PSF parameters and before/after results.

---

## Why deconvolution — axial cross-section

The axial (Z) view is where widefield blur is worst and where deconvolution is
expected to help most. Below is a representative axial cross-section, raw vs.
deconvolved:

| Raw | Deconvolved |
|-----|-------------|
| ![Cross-section — raw](assets/CrossSection-Raw.png) | ![Cross-section — deconvolved](assets/CrossSection-Deconvolved.png) |

Per-dataset lateral (Z projection) comparisons are on each dataset page.

---

## Point Spread Function (PSF)

No **empirical PSF** (e.g. from sub-resolution beads) was available, so a
**theoretical PSF** is generated with the
[PSF Generator](https://bigwww.epfl.ch/algorithms/psfgenerator/) Fiji plugin
using the **Born & Wolf 3D optical model**.

One **single-channel PSF** is generated **per dataset** and reused for all
channels. Because the datasets share the same optics, the PSF parameters are
identical **except for the Z step**, which is matched to each acquisition's
axial sampling. The exact parameters and the resulting theoretical resolution
are listed on each dataset page.

> A single representative emission wavelength is used to generate **one** PSF
> applied to every channel — see [open questions](#open-questions).

---

## Deconvolution

The deconvolution itself is the **not-yet-released** part of the pipeline:

- **Tool:** [BigDataViewer Playground](https://bigdataviewer-playground-documentation.readthedocs.io/en/latest/processing_images/deconvolution.html) **Tiled Multi-GPU deconvolution** in ImageJ / Fiji
- **Algorithm:** Richardson–Lucy (CLIJ2, GPU)
- **Regularization:** none
- **Iterations:** 120 deconvolution steps
- **PSF:** the dataset's theoretical PSF, stored beforehand
- **Output:** result resaved into the `deconvolved` folder of the dataset

---

## Running it yourself — `Deconvolve.groovy`

[`Deconvolve.groovy`](Deconvolve.groovy) wraps the whole pipeline as a single
Fiji script: it opens a multi-channel image and a matching single-channel PSF
via Bio-Formats, runs **tiled, lazy Richardson–Lucy GPU deconvolution** (CLIJ2)
block by block, and exports the result as an **OME-TIFF** (channel order
preserved). The computation is lazy — writing the output file is what actually
triggers the block-by-block GPU work.

### Requirements

An up-to-date **Fiji** with the following update sites enabled
(`Help ▸ Update… ▸ Manage update sites`):

- **UNIGE-Biochem**
- **clij**
- **clij2**
- **clijx-deconvolution**

A CUDA-capable **GPU** is used through CLIJ2.

### How to run (single image)

1. Open the script in Fiji: drag `Deconvolve.groovy` onto the main window, or
   `File ▸ Open…` then `Run` in the Script Editor.
2. Fill in the dialog and run:
   - **Image to deconvolve** — the multi-channel raw image (e.g. CZI).
   - **PSF image** — a single-channel PSF (one PSF is used for all channels).
   - **Output folder** — result is written as `<imageName>.ome.tiff`.
3. The deconvolved OME-TIFF is written to the output folder.

### How to batch process

In the Fiji Script Editor, use the **`Batch`** button (next to `Run`). Because
the inputs are declared as script parameters, Fiji lets you point the `File`
inputs at a folder and runs the script over every file — reusing the same PSF
and settings for all of them.

### Key parameters

| Parameter                 | Default   | Notes                                                    |
|---------------------------|-----------|----------------------------------------------------------|
| Number of iterations      | 120       | Richardson–Lucy steps.                                   |
| Regularization factor     | 0.000     | 0 = none; increase to tame noise/ringing.                |
| Non-circulant             | true      | Reduces edge artefacts.                                  |
| Block size X / Y / Z      | 256/256/64 | Tiling — lower it if you run out of GPU memory.          |
| Block overlap             | 16 px     | Overlap between tiles to avoid seams.                    |
| GPU streams / threads     | 10        | Parallel blocks on the GPU.                              |
| Output pixel type         | keep original | Or force `Float`.                                    |
| OME-TIFF compression      | LZW       | Export compression.                                      |
| Show sources in BigDataViewer | true  | Displays raw + deconvolved for a quick visual check.     |
| Overwrite                 | false     | Refuses to clobber an existing output unless ticked.     |

> The script defaults (**120 iterations**, **no regularization**) match the
> datasets described here — tune them for your own data.

---

## What has been tested

- [x] Extracted acquisition metadata from the raw CZIs
- [x] Generated a theoretical PSF (Born & Wolf) matching each acquisition's optics
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
- **One PSF for all channels.** A single emission wavelength is used to generate
  the PSF applied to every channel — is a per-channel PSF worth it?

---

## Data location

Raw and deconvolved data are stored outside the repo:

```
F:\user-projects\data\dudin-lab\deconvolution-workflow
```

(Also linked via `Shared_Folder.lnk`.) Each dataset lives in its own subfolder
(`paula\`, `baukje\`).
