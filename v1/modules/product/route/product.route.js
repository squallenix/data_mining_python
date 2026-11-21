import express from "express";

import {
  createProductController,
  getProductsController,
  getProductByIdController,
  updateProductController,
  deleteProductController,
} from "../controller/product.controller.js";

const router = express.Router();

router.post("/", createProductController);

router.get("/", getProductsController);

router.get("/:id", getProductByIdController);

router.put("/:id", updateProductController);

router.delete("/:id", deleteProductController);

export default router;