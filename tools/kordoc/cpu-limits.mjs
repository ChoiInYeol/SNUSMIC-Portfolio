// Project-owned runtime configuration; keep the pinned kordoc package unmodified.
// Each OCR CLI is already single-job. Default ONNX pools ignore the pipeline's
// eight-CPU budget and create a pool per model, so bound each session explicitly.
import { InferenceSession } from "onnxruntime-node";
import sharp from "sharp";

// libvips otherwise allocates its pool from the whole host as well.
sharp.concurrency(1);

const create = InferenceSession.create;
InferenceSession.create = function (model, options = {}) {
  return create.call(this, model, {
    ...options,
    intraOpNumThreads: 2,
    interOpNumThreads: 1,
  });
};
