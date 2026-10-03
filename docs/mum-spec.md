# MUM: the MAterial-under-Magnification Model

Status: specification, not yet built.

**MUM** learns from images of components under magnification: optical micrographs, SEM, and
possibly X-ray CT slices. Its labels come from how those components performed. It is
Stacey's MUM (`docs/stacey-spec.md`): it reads the images Stacey can't.

It answers three questions:
1. **Before testing:** from images of a fresh electrode, separator or powder, how is the cell
   likely to perform, and what is it likely to fail by?
2. **After testing:** from post-mortem images, what failed, and where in the image is the
   evidence?
3. **For Stacey:** a compact description of each sample's microstructure (an embedding),
   used as features alongside the recipe and the curves.

## The data

Each image record holds:
- **the image file;**
- **its scale:** µm per pixel and magnification. Physical scale is essential: the same
  feature looks different at different magnifications, so MUM works in physical units;
- **the instrument and mode:** optical, SEM (secondary or backscattered electrons), CT;
- **the sample and cell** it's from, linked through Stacey's lab data to the recipe, Ambuild
  run, charging curves and post-mortem;
- **when it was taken:** pristine, after formation, or post-mortem after N cycles;
- **where on the sample** it was taken.

**Labels come from two sources:**

| Kind | Source | Use |
| --- | --- | --- |
| **Weak labels** | the cell's measured performance (retention, capacity, efficiency) and its post-mortem failure modes | every image of a cell inherits them. Plentiful, but they describe the cell, not the image |
| **Strong labels** | experts' annotations: regions marking cracks, dendrites, delamination, voids, particle boundaries | fewer, but they say where. The ground truth the plan calls for, for post-mortem analysis |

**Storage:** images go in object storage and their metadata in the lab tables (Stacey S1).
Annotation uses a labelling tool such as Label Studio, which exports to the same store.

## Approach

**Small data first.** There will be hundreds of images, not millions, so MUM doesn't train a
vision model from scratch.

1. **Tiles at a fixed physical scale.** Large micrographs are cut into tiles of a fixed
   physical size, for example 20 × 20 µm, resampled to a common µm per pixel. Each tile is a
   view of the same amount of material.
2. **A pretrained backbone, frozen.** A self-supervised vision model gives each tile an
   embedding: DINOv2, or a CLIP image encoder. These generalise to micrographs better than
   ImageNet classifiers, though how well should be measured (see "Evaluation").
3. **Light heads on the embeddings:**
   - **performance:** a Gaussian process or ridge regression on a cell's pooled tile
     embeddings, with uncertainty. This suits the small data, as with Stacey;
   - **failure modes:** attention-based multiple-instance learning. A cell's label applies
     to its bag of tiles, and the model learns which tiles carry the evidence. Its attention
     weights show *where* the failure is, without needing strong labels;
   - **segmentation,** where strong labels exist: a small decoder on the backbone's features.
4. **Fine-tuning later.** Once there are thousands of labelled images, fine-tune the
   backbone, or adapt it with self-supervised pre-training on the lab's own unlabelled
   images.

**MUM's embeddings feed Stacey:** a cell's pooled tile embedding, reduced to a few dimensions,
becomes part of Stacey's input (Stacey S8).

## Evaluation

These rules guard against the usual ways image models flatter themselves:
- **Split by cell and by batch, never by tile.** Tiles from one image look alike, so a split
  by tile leaks.
- **Hold out an instrument or session.** Check the model hasn't learnt the microscope instead
  of the material.
- **Baselines:** predicting performance from the recipe and curves alone (Stacey without
  images), and hand-made image features (porosity from thresholding, particle size
  distributions). MUM has to beat both to be worth using.
- **Check what it's looking at:** the attention maps against the experts' strong labels,
  scored as localisation accuracy.
- **Calibration** of the performance intervals, as for AmPorSandbox.

**Logging:** everything goes to MLflow under `mum/`:
- the dataset snapshot (image IDs, scale, splits);
- the backbone and its version;
- metrics against the baselines, per instrument and per failure mode;
- example attention maps as artifacts.

## UI

- **Images:** browse a sample's micrographs with their scale bar and metadata. The model's
  attention or segmentation can be shown as an overlay, and you can link through to the
  cell's curves and post-mortem.
- **Annotate:** opens the labelling tool for an image, and records the result.
- **Predict:** upload images of a new sample and see its predicted performance and likely
  failure modes, with the evidence highlighted.

## Repository

MUM is a package in the AmPorSandbox repository, `amporsandbox.mum`, sharing the MLflow
plumbing and evaluation with AmPorSandbox and Stacey. Its own parts are:
- image loading;
- scale-aware tiling;
- the backbone wrappers;
- the multiple-instance and segmentation heads.

Its dependencies (PyTorch, the backbone weights, image libraries) are an optional extra, as
it needs a GPU to train at any size.

## Milestones

| | Delivers | Needs |
| --- | --- | --- |
| M1 | Image records: storage, metadata, scale handling, links to samples and cells | Stacey S1 |
| M2 | Tiling and backbone embeddings, tested on public micrograph datasets until lab images arrive | M1 |
| M3 | Performance from images: pooled embeddings and a GP, against the two baselines; embeddings available to Stacey | M2, cells with results |
| M4 | Failure modes by multiple-instance learning, with attention maps | M3, post-mortem labels |
| M5 | Annotation workflow, and segmentation where there are strong labels | M1, the labelling tool |
| M6 | The UI: Images, Annotate, Predict | M3 |
| M7 | Fine-tuning or self-supervised adaptation to the lab's images | enough images |

## Open questions

- **The instruments and imaging protocol:** a fixed protocol, with the same magnifications
  and the same regions per sample, would help more than any model choice.
- **Who annotates, and how much?** This decides when segmentation is worth building.
- **GPU access** for training: the cluster, or a workstation.
- **Image formats:** the microscopes' native files keep scale metadata that exported images
  often lose, so import the native files.
