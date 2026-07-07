# Deconvolution Workflow

Created by: **Nicolas Chiaruttini**

GitLab: https://gitlab.unige.ch/unige-biochem/dudin-lab/deconvolution-workflow

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

## Datasets

The workflow is being tested on several datasets. Each has its own PSF — the per-dataset pages below record all of that:

| Dataset | Sample | Z step | Details |
|---------|--------|-------:|---------|
| **Paula** | 4-channel widefield fluorescence | 500 nm | [datasets/paula.md](datasets/paula.md) |
| **Baukje** | *Dictyostelium* + ConA (widefield) | 330 nm | [datasets/baukje.md](datasets/baukje.md) |

See each page for the exact acquisition metadata,
PSF parameters and before/after results.


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

- **Tool:** [BigDataViewer Playground](https://bigdataviewer-playground-documentation.readthedocs.io/en/latest/processing_images/deconvolution.html) **Tiled Multi-GPU deconvolution** in ImageJ / Fiji
- **Algorithm:** Richardson–Lucy with non-circulant edge handling and total variation regularization
- **Regularization:** none
- **Iterations:** 120 deconvolution steps
- **PSF:** the dataset's theoretical PSF, stored beforehand
- **Output:** result resaved into the `deconvolved` folder of the dataset

---

## Running it yourself — `Deconvolve.groovy`

[`Deconvolve.groovy`](Deconvolve.groovy) wraps the whole pipeline as a single
Fiji script: it opens a multi-channel image and a matching single-channel PSF
via Bio-Formats, runs **tiled, lazy Richardson–Lucy GPU deconvolution** (CLIJ2)
block by block, and can **view the result in BigDataViewer**, **export it as an
OME-TIFF** (channel order preserved), or both. The computation is lazy —
browsing the sources in BigDataViewer or writing the output file is what
actually triggers the block-by-block GPU work.

### View, save, or both

Two independent options control what happens after deconvolution:

- **Show sources in BigDataViewer** — opens the raw and deconvolved sources for a
  quick visual check (no file written).
- **Save deconvolved output (OME-TIFF)** — writes `<imageName>.ome.tiff` to the
  output folder.

Enable either or both. With saving off, the output folder is optional and no
file is written; with only saving on, the sources are freed after export (handy
for batch runs).

### Requirements

An up-to-date **Fiji** with the following update sites enabled
(`Help ▸ Update… ▸ Manage update sites`):

- **UNIGE-Biochem**
- **clij**
- **clij2**
- **clijx-deconvolution**

An OpenCL-capable **GPU** is used through CLIJ2.

### Configuring the GPU pool (optional)

By default the deconvolution runs on a single GPU (device `0`). If you have
multiple GPUs — or want to run several parallel contexts on one GPU — configure
the OpenCL device pool:

`Edit ▸ Options ▸ CLIJ Pool Options`

The dialog lists the available device indices and takes a **`Pool
Configuration`** string of the form `device_idx:n_workers, device_idx:n_workers`
(default `0:1`). For example, `0:2, 1:4` runs **2** contexts on GPU 0 and **4**
on GPU 1 — **6 GPU workers** in total. The setting is persisted in the ImageJ
preferences, so it applies to all subsequent runs.

> **Pool workers vs. `Number of GPU streams / threads`.** The pool config above
> sets the number of **GPU-side** workers. The script's **`Number of GPU streams
> / threads`** parameter is the number of **CPU-side** workers that feed the pool
> (loading, converting, handing blocks over to the GPU, then retrieving and
> writing the result). It's generally good to keep a few **more** CPU workers
> than the total number of GPU pool workers, so the GPUs are never left waiting.

### How to run (single image)

1. Open the script in Fiji: drag `Deconvolve.groovy` onto the main window, or
   `File ▸ Open…` then `Run` in the Script Editor.
2. Fill in the dialog and run:
   - **Image to deconvolve** — the multi-channel raw image (e.g. CZI).
   - **PSF image** — a single-channel PSF (one PSF is used for all channels).
   - **Show sources in BigDataViewer / Save deconvolved output** — pick view,
     save, or both (at least one).
   - **Output folder** — where `<imageName>.ome.tiff` is written when saving.
3. The result is shown in BigDataViewer and/or written to the output folder.

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
| Save deconvolved output   | true      | Write the OME-TIFF. Untick to only view.                 |
| Overwrite                 | false     | Refuses to clobber an existing output unless ticked.     |

> The script defaults (**120 iterations**, **no regularization**) match the
> datasets described here — tune them for your own data.


---

### Open questions

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
